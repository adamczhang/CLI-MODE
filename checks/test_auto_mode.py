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
        self.assertEqual(route('/d fix the parser', auto), {'route': 'hint', 'text': AUTO_D})
        self.assertEqual(route('$d fix it', auto)['text'], AUTO_D)
        for prompt in ('/cli spawn cod', '/cli bind agy', '/cli use AGY-7K', '/cli model fast', '/cli effort high',
                       '/cli menu', '/cli timeout 90', '/cli attach', '/cli brief', '/cli brief-add hi', '/cli cod',
                       '/cli close AGY-7K'):
            self.assertEqual(route(prompt, auto), {'route': 'hint', 'text': AUTO_OWNED}, prompt)
        for prompt in ('/cli close', '/cli off', '/cli stop all', '/cli close all'):
            self.assertEqual(route(prompt, auto), {'route': 'off'}, prompt)  # Every agent: they are one team.
        self.assertEqual(route('/cli', auto), {'route': 'mode-page'})  # /cli is the Mode page in AUTO.
        self.assertEqual(route('fix the parser please', auto), {'route': 'host'})
        for prompt in ('/cli diff', '/cli undo', '/cli cancel', '/cli list', '/cli queue', '/cli usage', '/cli help',
                       '/cli view on', '/cli mode direct'):
            self.assertNotEqual(route(prompt, auto).get('text'), AUTO_OWNED, prompt)

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

    def test_nothing_saved_means_no_agent_and_strong(self):
        self.assertEqual(auto_mode.load(self.root), {'agent': None, 'backup': None, 'strength': 'strong'})
        auto_mode.config_path(self.root).write_text('not json', encoding='utf-8')
        self.assertEqual(auto_mode.load(self.root)['strength'], 'strong')

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
        self.assertIn('Now: DIRECT', page)
        self.assertIn('1. DIRECT', page)
        self.assertEqual(self.store().read()['pending']['phase'], 'mode')
        settings = self.reply('3')
        self.assertIn('AUTO settings', settings)
        self.assertIn('not chosen yet', settings)
        self.assertIn('Delegation', self.reply('3'))
        chosen = self.reply('3')
        self.assertIn('Delegation: Max.', chosen)
        self.assertEqual(self.config()['strength'], 'max')
        self.assertIn('Now: DIRECT', self.reply('b'))
        self.assertIn('Mode page closed', self.reply('x'))
        self.assertIsNone(self.store().read()['pending'])

    def test_any_other_message_closes_the_page_and_goes_to_claude(self):
        self.reply('/cli mode')
        self.assertEqual(self.prompt('what is AUTO for?'), {})
        self.assertIsNone(self.store().read()['pending'])

    def test_first_auto_opens_the_picker_and_the_chosen_agent_becomes_the_auto_agent(self):
        picker = self.reply('/cli mode auto')
        self.assertIn('Select CLI Agent', picker)
        self.assertIn('Choose your AUTO agent', picker)
        self.assertIn('M. Mode: DIRECT', picker)
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
        self.assertEqual(self.reply('/d fix it'), AUTO_D)

    def start_auto(self):
        auto_mode.save(self.data, {'agent': agy_choice(), 'backup': None, 'strength': 'strong'})
        return self.reply('/cli mode auto')

    def test_auto_starts_the_saved_agent_in_the_hook_and_it_waits(self):
        reply = self.start_auto()
        state = self.store().read()
        self.assertTrue(state['active'])
        self.assertEqual(state['routingMode'], 'auto')
        self.assertIn('AUTO is on: Claude hands work to ' + self.name(), reply)
        self.assertIn('/d is off', reply)
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

    def test_direct_hands_the_running_agents_back(self):
        self.start_auto()
        reply = self.reply('/cli mode direct')
        self.assertIn('DIRECT is on', reply)
        state = self.store().read()
        self.assertEqual(state['routingMode'], 'direct')
        self.assertTrue(state['active'])  # The agent keeps running; /d reaches it again.
        self.assertEqual(route('/d fix it', state)['route'], 'direct')

    def test_off_ends_auto_with_its_agents(self):
        self.start_auto()
        self.reply('/cli off')
        state = self.store().read()
        self.assertFalse(state['active'])
        self.assertEqual(state['routingMode'], 'direct')
        self.assertNotIn('auto', state)

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
        self.assertIn('**Agent:** Antigravity ' + next(o['alias'] for o in state['owned'] if o['name'] == backup), card)
        self.assertIn(agent_label(state, backup) + ' is now your backup agent: it waits for work.', card)

    def test_a_typed_agent_and_model_is_saved_in_direct_and_starts_nothing(self):
        reply = self.reply('/cli mode agent agy gemini-3.8-flash-high')
        self.assertIn('It starts when AUTO is on', reply)
        self.assertEqual(self.config()['agent']['model'], 'gemini-3.8-flash-high')
        self.assertFalse(self.store().read()['active'])
        self.assertIn('models match', self.reply('/cli mode agent agy no-such-model'))


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
