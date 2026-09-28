"""AUTO and DIRECT modes (Claude Code only): routing, the Mode page, the AUTO agent config, and starting the AUTO
agents when AUTO turns on. Codex keeps DIRECT alone: /cli mode there still says Passthrough was removed."""
import json
import os
import unittest
from unittest.mock import patch

from test_claude_hook import ClaudeHook, SESSION, claude
import auto_mode
import host
from state import AUTO_D, AUTO_OWNED, route


def agy_choice(model='gemini-3.8-flash-high'):
    return {'agent': 'agy', 'model': model, 'effort': None, 'access': 'allow'}


def codex_choice(effort='high'):
    return {'agent': 'codex', 'model': 'gpt-6-sol', 'effort': effort, 'access': 'allow'}


class AutoRouting(unittest.TestCase):
    """route() alone: what each prompt means in DIRECT and in AUTO."""

    def setUp(self):
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)

    @staticmethod
    def state(mode='direct', **extra):
        return dict(dict(active=True, pending=None, routingMode=mode, owned=[], main=None, helpMenu=None), **extra)

    def test_cli_mode_commands(self):
        cases = {
            '/cli mode': {'route': 'mode-page'},
            '$cli mode AUTO': {'route': 'mode-set', 'mode': 'auto'},
            '/cli mode direct': {'route': 'mode-set', 'mode': 'direct'},
            '/cli mode agent': {'route': 'mode-agent', 'role': 'agent'},
            '/cli mode backup': {'route': 'mode-agent', 'role': 'backup'},
            '/cli mode agent cod gpt 6 sol': {'route': 'mode-agent', 'role': 'agent', 'agent': 'codex',
                                              'text': 'gpt 6 sol'},
            '/cli mode backup gro': {'route': 'mode-agent', 'role': 'backup', 'agent': 'grok-build', 'text': ''},
            '/cli mode backup none': {'route': 'mode-backup-clear'},
            '/cli mode strength MAX': {'route': 'mode-strength', 'strength': 'max'},
            '/cli mode effort Low': {'route': 'mode-effort', 'effort': 'low'},
            '/cli mode fast ON': {'route': 'mode-fast', 'fast': 'on'},
        }
        for prompt, expected in cases.items():
            self.assertEqual(route(prompt, self.state()), expected, prompt)
        for prompt in ('/cli mode fast', '/cli mode strength extreme', '/cli mode agent nobody'):
            self.assertEqual(route(prompt, self.state())['route'], 'hint', prompt)

    def test_codex_keeps_the_removed_passthrough_answer(self):
        host.select(host.CODEX)
        for prompt in ('/cli mode', '/cli mode auto'):
            reply = route(prompt, self.state())
            self.assertEqual(reply['route'], 'hint')
            self.assertIn('Passthrough mode was removed', reply['text'])

    def test_auto_turns_d_off_and_keeps_orchestration_with_claude(self):
        auto = self.state('auto')
        self.assertEqual(route('/d what does the parser do?', auto), {'route': 'auto-host'})  # Claude itself.
        self.assertEqual(route('$d fix it', auto), {'route': 'auto-host'})
        self.assertEqual(route('/d   ', auto), {'route': 'hint', 'text': AUTO_D})
        for prompt in ('/cli spawn cod', '/cli bind agy', '/cli use AGY-7K', '/cli model fast', '/cli effort high',
                       '/cli menu', '/cli timeout 90', '/cli attach', '/cli brief', '/cli brief-add hi',
                       '/cli close AGY-7K',
                       # One agent's piece of the work is Claude's to act on: the user asks Claude, which still can.
                       '/cli undo', '/cli undo AGY-7K', '/cli diff', '/cli diff AGY-7K', '/cli queue', '/cli resume',
                       '/cli dir', '/cli test pytest -q', '/cli progress quiet', '/cli cancel AGY-7K'):
            self.assertEqual(route(prompt, auto), {'route': 'hint', 'text': AUTO_OWNED}, prompt)
        for prompt in ('/cli close', '/cli off', '/cli stop all', '/cli close all'):
            self.assertEqual(route(prompt, auto), {'route': 'off'}, prompt)  # Every agent: they are one team.
        for prompt in ('/cli cancel', '/cli cancel all'):
            self.assertEqual(route(prompt, auto), {'route': 'cancel-all'}, prompt)  # The one brake, for every agent.
        self.assertEqual(route('/cli cancel', dict(auto, active=False))['route'], 'hint')  # Nothing is running.
        self.assertEqual(route('/cli usage', auto), {'route': 'usage'})  # No name: every running agent's usage.
        self.assertEqual(route('/cli', auto), {'route': 'mode-page'})  # /cli is the Mode page once AUTO runs.
        # An agent's own page: starting it there makes it the AUTO agent (menus.frontend, binding.activate).
        self.assertEqual(route('/cli cod', auto), {'route': 'frontend', 'agent': 'codex'})
        self.assertEqual(route('fix the parser please', auto), {'route': 'host'})
        for prompt in ('/cli list', '/cli usage', '/cli usage AGY-7K', '/cli help', '/cli view on', '/cli mode direct',
                       '/cli approve', '/cli deny', '/cli access'):
            self.assertNotEqual(route(prompt, auto).get('text'), AUTO_OWNED, prompt)

    def test_auto_help_shows_only_the_users_own_controls(self):
        card = route('/cli help', self.state('auto'))['text']
        for hidden in ('/cli undo', '/cli diff', '/cli queue', '/cli dir', '/cli test', '/cli progress', '/cli spawn',
                       '/cli menu', '/cli use', '/cli brief', '/d <'):
            self.assertNotIn(hidden, card)
        for shown in ('Help (AUTO)', '/cli list', '/cli approve|deny', '/cli cancel', '/cli usage', '/cli off',
                      '/cli mode', '/cli view', 'Ask Claude to undo'):
            self.assertIn(shown, card)
        self.assertTrue(all(len(line) <= 40 for line in card.splitlines()), card)
        self.assertIn('/cli undo [name]', route('/cli help', self.state())['text'])  # DIRECT keeps the full card.

    def test_while_off_auto_starts_from_the_agent_list(self):
        off = self.state('auto', active=False)
        self.assertEqual(route('/cli', off), {'route': 'home'})  # The agent list, the saved AUTO agent first.
        self.assertEqual(route('/d fix it', off)['route'], 'hint')  # Nothing runs: /d says how to start.
        self.assertEqual(route('/cli bind agy', off), {'route': 'hint', 'text': AUTO_OWNED})

    def test_direct_is_unchanged(self):
        direct = self.state()
        self.assertEqual(route('/cli', direct), {'route': 'home'})
        self.assertEqual(route('/d fix it', dict(direct, active=False))['route'], 'hint')  # Nothing running.
        self.assertEqual(route('/cli spawn cod', direct)['route'], 'bind')

    def test_the_mode_page_takes_numbers_b_and_x_and_lets_anything_else_through(self):
        opened = self.state(pending=None)
        opened['pending'] = dict(id='p', stage='menu', phase='mode')
        self.assertEqual(route('2', opened), {'route': 'mode-choose', 'number': 2})
        self.assertEqual(route('B', opened), {'route': 'mode-back'})
        self.assertEqual(route('x', opened), {'route': 'mode-dismiss'})
        self.assertEqual(route('what does AUTO do?', opened), {'route': 'mode-invalid'})
        self.assertEqual(route('/cli mode auto', opened)['route'], 'mode-set')  # A command still runs.

    def test_the_agent_list_opens_the_mode_page_with_m_on_claude_only(self):
        listing = self.state()
        listing['pending'] = dict(id='p', stage='menu', phase='agent', choices=[{'label': 'x', 'value': 'agy'}])
        self.assertEqual(route('m', listing), {'route': 'mode-page'})
        host.select(host.CODEX)
        self.assertEqual(route('m', listing), {'route': 'setup'})


class Config(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_nothing_saved_means_no_agent_and_normal(self):
        self.assertEqual(auto_mode.load(self.root), {'agent': None, 'backup': None, 'strength': 'normal'})
        auto_mode.config_path(self.root).write_text('not json', encoding='utf-8')
        self.assertEqual(auto_mode.load(self.root)['strength'], 'normal')

    def test_strong_saved_as_the_old_default_is_normal_but_a_choice_stays(self):
        """Strong was the default until 2026-09-27 and was saved with the agent: only a choice of it counts."""
        auto_mode.save(self.root, {'agent': agy_choice(), 'backup': None, 'strength': 'strong'})
        self.assertEqual(auto_mode.load(self.root)['strength'], 'normal')
        auto_mode.save(self.root, {'agent': agy_choice(), 'backup': None, 'strength': 'max'})
        self.assertEqual(auto_mode.load(self.root)['strength'], 'max')  # Never a default: a choice.
        auto_mode.save(self.root, {'agent': agy_choice(), 'backup': None, 'strength': 'strong',
                                   'strengthChosen': True})
        loaded = auto_mode.load(self.root)
        self.assertEqual((loaded['strength'], loaded['strengthChosen']), ('strong', True))
        auto_mode.save(self.root, dict(loaded, agent=agy_choice()))  # Kept through a later save.
        self.assertEqual(auto_mode.load(self.root)['strength'], 'strong')
        self.assertIn('1. Normal (default)', auto_mode.page_text('auto-strength', self.root, {}))

    def test_round_trip_and_unknown_agents_are_dropped(self):
        auto_mode.save(self.root, {'agent': agy_choice(), 'backup': {'agent': 'nobody'}, 'strength': 'max'})
        loaded = auto_mode.load(self.root)
        self.assertEqual(loaded['agent'], agy_choice())
        self.assertIsNone(loaded['backup'])
        self.assertEqual(loaded['strength'], 'max')

    def test_pages_fit_the_40_column_frame(self):
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)
        state = dict(routingMode='auto')
        for config in ({'agent': None, 'backup': None, 'strength': 'strong'},
                       {'agent': agy_choice(), 'backup': agy_choice(), 'strength': 'normal'}):
            auto_mode.save(self.root, config)
            for page in ('mode', 'auto-settings', 'auto-strength'):
                text = auto_mode.page_text(page, self.root, state)
                widths = {len(line) for line in text.splitlines() if line.startswith(('+', '|'))}
                self.assertEqual(widths, {40}, (page, text))
                self.assertIn('X. Exit', text)
        self.assertIn('Now: AUTO', auto_mode.page_text('mode', self.root, state))
        self.assertIn('4. Remove backup', auto_mode.page_text('auto-settings', self.root, state))


class ModeHook(ClaudeHook):
    """The Mode page and AUTO through the Claude hook, with a fake agent backend."""

    def setUp(self):
        super().setUp()
        usage = patch('confirmation.usage', return_value={'status': 'unavailable', 'reason': 'test'})
        usage.start()
        self.addCleanup(usage.stop)
        confirmed = patch('frontends.confirmed', return_value=True)  # Every agent counts as set up.
        confirmed.start()
        self.addCleanup(confirmed.stop)

    def reply(self, text):
        output = self.prompt(text)
        return output.get('reason', '') if output else ''

    def config(self):
        return auto_mode.load(self.data)

    def test_the_mode_page_and_its_sub_pages(self):
        page = self.reply('/cli mode')
        self.assertIn('Now: AUTO', page)  # The default on Claude Code.
        self.assertIn('2. AUTO (default)', page)
        self.assertIn('1. DIRECT', page)
        self.assertEqual(self.store().read()['pending']['phase'], 'mode')
        settings = self.reply('3')
        self.assertIn('AUTO settings', settings)
        self.assertIn('not chosen yet', settings)
        self.assertIn('Delegation', self.reply('3'))
        chosen = self.reply('3')
        self.assertIn('Delegation: Max.', chosen)
        self.assertEqual(self.config()['strength'], 'max')
        self.assertIn('Now: AUTO', self.reply('b'))
        self.assertIn('Mode page closed', self.reply('x'))
        self.assertIsNone(self.store().read()['pending'])

    def test_auto_is_the_default_and_direct_stays_once_chosen(self):
        from state import Store
        self.assertEqual(self.prompt('hello'), {})  # Off: nothing added to Claude's turns.
        self.assertEqual(self.store().read()['routingMode'], 'auto')  # A new conversation.
        with self.store().edit() as state:  # Saved by an earlier version, when DIRECT was the default, and off.
            state['routingMode'] = 'direct'
        self.assertEqual(self.store().read()['routingMode'], 'auto')
        self.assertIn('DIRECT is on', self.reply('/cli mode direct'))
        self.reply('/cli off')
        self.assertEqual(self.store().read()['routingMode'], 'direct')  # Chosen here: it stays.
        listing = self.reply('/cli')
        self.assertIn('Select CLI Agent', listing)
        self.assertIn('M. Mode: DIRECT', listing)
        self.reply('x')
        self.reply('/cli mode auto')  # Nothing saved: the AUTO picker. AUTO turns on once its agent starts.
        choices = [choice['value'] for choice in self.store().read()['pending']['choices']]
        self.reply(str(choices.index('agy') + 1))
        self.reply('1')
        state = self.store().read()
        self.assertEqual(state['routingMode'], 'auto')
        self.assertNotIn('directChosen', state)
        host.select(host.CODEX)  # Codex has no AUTO: its conversations stay DIRECT.
        self.addCleanup(host.select, host.CODEX)
        self.assertEqual(Store('codex-thread', self.project, self.data).read()['routingMode'], 'direct')

    def test_cli_starts_the_saved_auto_agent_first(self):
        auto_mode.save(self.data, {'agent': agy_choice(), 'backup': None, 'strength': 'strong'})
        listing = self.reply('/cli')
        self.assertIn('Select AUTO Agent', listing)
        self.assertIn('1 starts your saved AUTO agent.', listing)
        self.assertIn('| 1. Antigravity, Gemini 3.8 Flash ', listing)  # One row: it fits the 40 columns.
        self.assertIn('M. Mode: AUTO', listing)
        choices = [choice['value'] for choice in self.store().read()['pending']['choices']]
        self.assertEqual(choices[0], 'auto-saved')
        self.assertIn('agy', choices[1:])  # The agents follow, 2 onwards; any of them becomes the AUTO agent.
        reply = self.reply('1')
        self.assertIn('AUTO is on: Claude hands work to Antigravity-01', reply)
        state = self.store().read()
        self.assertTrue(state['active'])
        self.assertEqual(state['auto']['agent'], state['main'])
        self.assertEqual(state['owned'][0]['timeout'], auto_mode.AUTO_TIMEOUT)  # Two hours idle, or /cli off.
        self.assertIn('Now: AUTO', self.reply('/cli'))  # Running: /cli is the Mode page.

    def test_any_other_message_closes_the_page_and_goes_to_claude(self):
        self.reply('/cli mode')
        self.assertEqual(self.prompt('what is AUTO for?'), {})
        self.assertIsNone(self.store().read()['pending'])

    def test_first_auto_opens_the_picker_and_the_chosen_agent_becomes_the_auto_agent(self):
        picker = self.reply('/cli mode auto')
        self.assertIn('Select AUTO Agent', picker)
        self.assertIn('Choose your AUTO agent', picker)
        self.assertIn('M. Mode: AUTO', picker)
        self.assertNotIn('saved AUTO agent', picker)  # Nothing saved yet: the agents alone.
        pending = self.store().read()['pending']
        self.assertEqual(pending['purpose'], 'auto-on')
        number = [choice['value'] for choice in pending['choices']].index('agy') + 1
        self.reply(str(number))
        self.assertEqual(self.store().read()['pending']['purpose'], 'auto-on')  # Kept on the agent's page.
        card = self.reply('1')  # Yes - use these defaults.
        self.assertIn('CLI-MODE Activated', card)
        self.assertIn('AUTO is on: Claude hands work to ' + self.name(), card)  # Said under the card.
        state = self.store().read()
        self.assertTrue(state['active'])
        self.assertEqual(state['routingMode'], 'auto')
        self.assertEqual(state['auto']['agent'], state['main'])
        self.assertEqual(self.config()['agent']['agent'], 'agy')
        self.assertEqual(self.store().read()['requests'] if 'requests' in self.store().read() else {}, {})

    def start_auto(self):
        auto_mode.save(self.data, {'agent': agy_choice(), 'backup': None, 'strength': 'strong'})
        return self.reply('/cli mode auto')

    def test_auto_starts_the_saved_agent_in_the_hook_and_it_waits(self):
        reply = self.start_auto()
        state = self.store().read()
        self.assertTrue(state['active'])
        self.assertEqual(state['routingMode'], 'auto')
        self.assertIn('AUTO is on: Claude hands work to ' + self.name(), reply)
        self.assertIn('/d asks Claude itself', reply)
        self.assertIsNone(state['pending'])
        brief = (self.project / 'Agent_Working_Folder' / 'BRIEF.md')
        if brief.is_file():  # Briefs are off in AUTO: no host-note entry waits for Claude.
            self.assertNotIn('has not written this note yet', brief.read_text(encoding='utf-8'))
        self.assertIn('AUTO is already on', self.reply('/cli mode auto'))  # Nothing started twice.
        self.assertEqual(len(self.store().read()['owned']), 1)

    def test_in_auto_cli_is_the_mode_page_and_orchestration_is_refused(self):
        self.start_auto()
        self.assertIn('Now: AUTO', self.reply('/cli'))
        self.reply('x')
        self.assertEqual(self.reply('/cli spawn agy'), AUTO_OWNED)
        self.assertNotEqual(self.reply('/cli list'), AUTO_OWNED)
        for prompt in ('/cli undo', '/cli diff', '/cli queue', '/cli dir', '/cli test off'):
            self.assertEqual(self.reply(prompt), AUTO_OWNED, prompt)  # One agent's work: ask Claude.
        self.assertIn('Help (AUTO)', self.reply('/cli help'))
        self.reply('x')

    def test_cancel_in_auto_is_one_brake_for_every_agent(self):
        from state import agent_entry, agent_label
        self.start_auto()
        state = self.store().read()
        lead = state['auto']['agent']
        self.assertIn('No agent turn was running', self.reply('/cli cancel'))
        with self.store().edit() as saved:  # The AUTO agent's running turn, as the queue worker records it.
            saved['inflight']['0' * 32] = dict(session=lead, kind='prompt', pid=os.getpid())
        self.assertIn('Cancel requested for ' + agent_label(state, lead) + ': its turn stops, and Claude is told',
                      self.reply('/cli cancel'))
        self.assertEqual(self.reply('/cli cancel ' + agent_entry(state, lead)['alias']), AUTO_OWNED)

    def test_direct_hands_the_running_agents_back(self):
        self.start_auto()
        reply = self.reply('/cli mode direct')
        self.assertIn('DIRECT is on', reply)
        state = self.store().read()
        self.assertEqual(state['routingMode'], 'direct')
        self.assertTrue(state['active'])  # The agent keeps running; /d reaches it again.
        self.assertEqual(route('/d fix it', state)['route'], 'direct')

    def test_off_closes_auto_agents_and_cli_starts_them_again(self):
        self.start_auto()
        self.reply('/cli off')
        state = self.store().read()
        self.assertFalse(state['active'])
        self.assertEqual(state['routingMode'], 'auto')  # The mode stays; its agents are gone.
        self.assertNotIn('auto', state)
        self.assertIn('1 starts your saved AUTO agent.', self.reply('/cli'))
        self.assertIn('AUTO is on: Claude hands work to Antigravity-01', self.reply('1'))

    def test_a_backup_starts_too_and_the_auto_agent_stays_current(self):
        auto_mode.save(self.data, {'agent': agy_choice(), 'backup': agy_choice(), 'strength': 'strong'})
        reply = self.reply('/cli mode auto')
        state = self.store().read()
        self.assertEqual(len(state['owned']), 2)
        self.assertEqual(state['main'], state['auto']['agent'])
        self.assertNotEqual(state['auto']['agent'], state['auto']['backup'])
        self.assertIn(', backup ', reply)
        removed = self.reply('/cli mode backup none')
        self.assertIn('Backup removed.', removed)
        self.assertIn('was closed', removed)
        state = self.store().read()
        self.assertEqual(len(state['owned']), 1)
        self.assertIsNone(self.config()['backup'])

    def test_a_backup_chosen_on_the_settings_page_gets_its_own_card(self):
        # Live run 1 (2026-09-27): the backup's activation card described the AUTO agent, the current one.
        self.start_auto()
        lead = self.store().read()['main']
        self.reply('/cli mode')
        self.reply('3')  # AUTO settings.
        self.reply('2')  # Backup agent: the picker.
        pending = self.store().read()['pending']
        self.assertEqual(pending['purpose'], 'auto-backup')
        self.reply(str([choice['value'] for choice in pending['choices']].index('agy') + 1))
        card = self.reply('1')
        state = self.store().read()
        backup = state['auto']['backup']
        from state import agent_label
        self.assertEqual(state['main'], lead)  # The AUTO agent stays current.
        self.assertIn('**Agent:** Antigravity-02 |', card)  # An AUTO name stands alone: it says the agent.
        self.assertIn(agent_label(state, backup) + ' is now your backup agent: it waits for work.', card)

    def test_choosing_an_auto_agent_turns_auto_on(self):
        # Live, 2026-09-27: an AUTO agent chosen from AUTO settings started in DIRECT, and AUTO needed another command.
        auto_mode.save(self.data, {'agent': None, 'backup': agy_choice(), 'strength': 'strong'})
        self.reply('/cli mode agent')
        pending = self.store().read()['pending']
        self.assertEqual(pending['purpose'], 'auto-agent')
        self.reply(str([choice['value'] for choice in pending['choices']].index('agy') + 1))
        card = self.reply('1')
        self.assertIn('AUTO is on: Claude hands work to ' + self.name(), card)
        state = self.store().read()
        self.assertEqual(state['routingMode'], 'auto')
        self.assertEqual(len(state['owned']), 2)  # The backup started too: AUTO's agents all wait for work.
        self.assertEqual(state['main'], state['auto']['agent'])

    def test_auto_agents_are_named_after_their_kind_and_numbered(self):
        auto_mode.save(self.data, {'agent': agy_choice(), 'backup': agy_choice(), 'strength': 'strong'})
        self.reply('/cli mode auto')
        state = self.store().read()
        from state import agent_label
        names = {role: agent_label(state, session) for role, session in state['auto'].items()}
        self.assertEqual(names, {'agent': 'Antigravity-01', 'backup': 'Antigravity-02'})
        self.reply('/cli off')
        self.reply('/cli mode direct')
        self.reply('/cli bind agy')  # DIRECT keeps its generated names.
        self.assertRegex(self.name(), r'^Antigravity AGY-[A-Z0-9]{2}$')
        self.assertEqual(auto_mode.auto_name('codex', ['Codex-01', 'codex-03']), 'Codex-02')

    def test_effort_and_fast_mode_change_the_running_auto_agent_in_place(self):
        # Usage test, 2026-09-27: GPT-6 Sol at High was most of AUTO's time. AUTO settings choose the agent's effort
        # and Codex's own fast mode; the running agent takes them in place (same session, no restart).
        auto_mode.save(self.data, {'agent': codex_choice(), 'backup': None, 'strength': 'strong'})
        self.reply('/cli mode auto')
        before = self.store().read()
        lead = before['auto']['agent']
        self.reply('/cli mode')
        settings = self.reply('3')
        self.assertIn('4. Effort: High', settings)
        self.assertIn('5. Fast mode: Off', settings)
        faster = self.reply('5')
        self.assertIn('Fast mode: On. Codex-01 now works with it.', faster)
        self.assertIn('5. Fast mode: On', faster)
        state = self.store().read()
        self.assertEqual([item['name'] for item in state['owned']], [lead])  # The same agent, not a new one.
        self.assertIs(state['owned'][0]['settings']['fast'], True)
        self.assertIs(self.config()['agent']['fast'], True)
        page = self.reply('4')  # The Effort page: the agent's own levels.
        self.assertIn('Now: High', page)
        number = [label for label, _ in auto_mode.effort_options(self.data, self.config()['agent'])].index('Low') + 1
        self.assertIn('Effort: Low.', self.reply(str(number)))
        self.assertEqual(self.store().read()['owned'][0]['settings']['effortValue'], 'low')
        self.assertEqual(self.config()['agent']['effort'], 'low')
        self.assertIn('Fast mode: Off.', self.reply('/cli mode fast off'))
        self.assertIn('Choose one of', self.reply('/cli mode effort warp'))
        import codex_cli  # Codex's fast-mode config option only when chosen: other settings stay as they were.
        steps = codex_cli.setting_steps(dict(state['owned'][0]['settings'], fast=True))
        self.assertEqual(steps[-1], ['set', 'fast-mode', 'on'])
        self.assertNotIn('fast-mode', str(codex_cli.setting_steps({k: v for k, v in state['owned'][0]['settings']
                                                                   .items() if k != 'fast'})))

    def test_no_effort_or_fast_rows_for_an_agent_without_them(self):
        auto_mode.save(self.data, {'agent': agy_choice(), 'backup': None, 'strength': 'strong'})
        settings = auto_mode.page_text('auto-settings', self.data, {})
        self.assertNotIn('Effort', settings)  # Antigravity's effort is part of its model.
        self.assertNotIn('Fast mode', settings)
        self.assertIn('Fast mode is Codex', self.reply('/cli mode fast on'))

    def test_a_typed_agent_and_model_turns_auto_on(self):
        self.assertIn('models match', self.reply('/cli mode agent agy no-such-model'))
        reply = self.reply('/cli mode agent agy gemini-3.8-flash-high')
        self.assertIn('AUTO is on: Claude hands work to', reply)
        self.assertEqual(self.config()['agent']['model'], 'gemini-3.8-flash-high')
        state = self.store().read()
        self.assertTrue(state['active'])
        self.assertEqual(state['routingMode'], 'auto')


class CodexHasNoAuto(unittest.TestCase):
    def test_the_controller_refuses_mode_on_codex(self):
        import tempfile
        from controller import build_parser, run
        with tempfile.TemporaryDirectory() as folder:
            args = build_parser().parse_args(['--thread', 't', '--workspace', folder, '--data-root', folder,
                                              '--host', 'codex', 'auto', 'page'])
            with self.assertRaises(ValueError):
                run(args)


if __name__ == '__main__':
    unittest.main()
