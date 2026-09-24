"""Only an explicit /d or $d prompt reaches the agent; routing is independent of activation and transport.

Passthrough mode (ordinary prompts sent to the agent) was removed: a prompt for an agent is given on purpose.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import Controller, Store, FakeBackend, hook, backend_ids, PLUGIN
from state import route, direct_payload


class Modes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store('modes', self.root, self.root / 'state')
        self.backend = FakeBackend()
        self.c = Controller(self.store, self.backend)

    def activate(self, agent='agy'):
        self.c.frontend(agent)
        d = self.c.adapter.DEFAULTS
        if agent == 'copilot':
            # Copilot has no model-setting operation; this shared fixture's
            # default verifier otherwise expects one. Routing is under test.
            with patch.object(self.backend, 'verify', side_effect=lambda owned, record=None:
                              (self.backend.metadata(owned) if record is None else record)['acpSessionId']):
                return self.c.activate(d['model'], d['access'], effort=d.get('effort'), agent=agent)
        return self.c.activate(d['model'], d['access'], effort=d.get('effort'), agent=agent)

    def event(self, prompt=None, name='UserPromptSubmit', **fields):
        event = dict(session_id='modes', cwd=str(self.root), hook_event_name=name, **fields)
        if prompt is not None:
            event['prompt'] = prompt
        return hook.handle(event, self.store.root)

    def test_every_state_is_direct_and_a_saved_passthrough_conversation_opens_in_direct(self):
        self.assertEqual(self.store.read()['routingMode'], 'direct')
        self.activate()
        self.assertEqual(route('ordinary message', self.store.read())['route'], 'host')
        self.assertEqual(route('/d task', self.store.read())['route'], 'direct')
        state = self.store.read()
        state.update(routingMode='passthrough', modeMenu=True)  # Saved by an older version.
        self.store.path.write_text(json.dumps(state), encoding='utf-8')
        opened = self.store.read()
        self.assertEqual(opened['routingMode'], 'direct')
        self.assertNotIn('modeMenu', opened)
        self.assertEqual(route('ordinary message', opened)['route'], 'host')
        self.assertTrue(opened['active'])  # The agent and its session are kept.
        state.pop('routingMode')
        self.store.path.write_text(json.dumps(state), encoding='utf-8')
        self.assertEqual(self.store.read()['routingMode'], 'direct')
        state['routingMode'] = 'unknown'
        self.store.path.write_text(json.dumps(state), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.read()

    def test_cli_mode_explains_that_only_d_reaches_the_agent(self):
        for active in (False, True):
            if active:
                self.activate()
            for prefix in ('/', '$'):
                for command in ('mode', 'mode passthrough', 'MODE direct'):
                    result = route(prefix + 'cli ' + command, self.store.read())
                    self.assertEqual(result['route'], 'hint')
                    self.assertIn('only through /d', result['text'])
                    self.assertIn('Passthrough mode was removed', result['text'])
        with self.assertRaises(AttributeError):
            self.c.mode  # The control is gone, not just hidden.

    def test_unified_active_settings_for_all_agents_and_prefixes(self):
        for agent in backend_ids():
            self.activate(agent)
            before = deepcopy(self.store.read())
            calls = len(self.backend.calls)
            for prefix in ('/', '$'):
                for alias in ('menu', 'model'):
                    self.assertEqual(route(prefix + 'CLI ' + alias, self.store.read()), {'route': 'settings'})
                    result = self.c.settings_menu()
                    for label in ('Agent Settings', '1. Change model', '2. Change effort',
                                  '3. Change access', '4. Toggle activity progress'):
                        self.assertIn(label, result['activationMenu'])
                    for gone in ('routing', 'Mode:', '| 5.'):  # A menu row, not a model's version number.
                        self.assertNotIn(gone, result['activationMenu'])
                    for key in ('active', 'main', 'owned', 'settings', 'routingMode', 'generation'):
                        self.assertEqual(result[key], before[key])
                    for number, phase in (('1', 'model'), ('2', 'effort'), ('3', 'access')):
                        self.assertEqual(route(number, result), {'route': 'tune', 'phase': phase, 'text': ''})
                    self.assertEqual(route('4', result), {'route': 'progress', 'choice': 'quiet'})
                    self.assertEqual(route('5', result), {'route': 'settings'})  # No longer a choice.
                    self.assertEqual(route('X', result), {'route': 'settings-dismiss'})
                    self.assertEqual(route('done', result), {'route': 'settings-dismiss'})
                    self.assertNotIn('Done', result['activationMenu'])  # X closes settings.
                    self.c.settings_menu(dismiss=True)
                    self.assertIsNone(self.store.read()['pending'])
            self.assertEqual(len(self.backend.calls), calls)
            self.c.off()

    def test_unified_settings_hooks_never_stop_or_dispatch(self):
        self.activate('codex')
        for alias in ('menu', 'model'):
            context = self.event('/cli ' + alias)['hookSpecificOutput']['additionalContext']
            self.assertIn('settings`', context)
        self.c.settings_menu()
        context = self.event('X')['hookSpecificOutput']['additionalContext']
        self.assertIn('settings --dismiss', context)
        self.assertTrue(self.store.read()['active'])
        self.c.settings_menu(dismiss=True)
        self.assertEqual(route('/cli model gpt-5.6-sol', self.store.read()),
                         {'route': 'tune', 'phase': 'model', 'text': 'gpt-5.6-sol'})
        self.c.off()
        for prefix in ('/', '$'):
            for command in ('menu', 'model', 'model gpt-6-astra'):
                result = route(prefix + 'CLI ' + command, self.store.read())
                self.assertEqual(result, {'route': 'hint', 'text': 'CLI-MODE: Agent not activated. /CLI to setup'})

    @patch('frontends.first_start', return_value=False)
    @patch('frontends.confirmed', return_value=True)
    @patch('frontends.routing_readiness', return_value={'ready': True})
    def test_agent_defaults_page_offers_no_routing_choice(self, readiness, confirmed, first_start):
        for agent in backend_ids():
            with self.subTest(agent=agent):
                result = self.c.frontend(agent)
                self.assertIn('1. Yes - use these defaults', result['activationMenu'])
                self.assertIn('2. Change defaults', result['activationMenu'])
                for gone in ('Mode:', 'routing mode', '| 3.'):
                    self.assertNotIn(gone, result['activationMenu'])
                self.assertEqual(route('3', self.store.read()), {'route': 'setup'})
        self.assertEqual(self.backend.calls, [])

    def test_direct_only_matches_complete_leading_tokens(self):
        self.activate()
        for prompt in ('hello', '/direct hello', '/debug', '$data', 'example /d hello', '`/d hello`', '"/d hello"', '```\n/d hi\n```'):
            self.assertEqual(route(prompt, self.store.read())['route'], 'host', prompt)
        for prompt in ('/d task', '  $D task', '/D\ntask'):
            self.assertEqual(route(prompt, self.store.read())['route'], 'direct', prompt)
        for prompt in ('/d', '$d  \r\n'):
            self.assertEqual(route(prompt, self.store.read())['route'], 'hint')
        self.c.off()
        self.assertEqual(route('/d task', self.store.read())['route'], 'hint')

    def test_direct_payload_preserves_remaining_whitespace_and_line_endings(self):
        self.assertEqual(direct_payload(' \t/d   text\r\n    code\n'), '  text\r\n    code\n')
        self.assertEqual(direct_payload('$d\r\n    code\r\n'), '    code\r\n')
        self.assertIsNone(direct_payload('/direct hello'))

    def test_no_unprefixed_send_can_reach_any_backend(self):
        for agent in backend_ids():
            with self.subTest(agent=agent):
                self.activate(agent)
                before = len(self.backend.calls)
                for prompt in ('host task', '/context', '/d', '$d  '):
                    with self.assertRaises(RuntimeError):
                        self.c.send(prompt)
                self.assertEqual(len(self.backend.calls), before)
                events = []
                self.event('$d   task\r\n    detail\n')
                self.c.send_request(self.store.read()['turnRoute']['requestId'], output=events.append)
                self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'message'), '  task\r\n    detail\n')
                self.c.off()

    def test_direct_native_command_is_validated_after_prefix_removal(self):
        self.activate('grok-build')
        owned = self.store.read()['owned'][0]
        self.backend.records[owned['name']]['acpx']['available_commands'] = [{'name': 'context'}]
        before = len(self.backend.calls)
        validator = self.c.adapter.Backend().validate_command
        with patch.object(self.backend, 'validate_command', side_effect=validator, create=True):
            with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
                self.c.send('/d /not-a-command')
            self.assertEqual(len(self.backend.calls), before)
            events = []
            self.c.send('/d /context', output=events.append)
        self.assertIn('/context', ''.join(e['text'] for e in events if e['type'] == 'message'))

    def test_direct_commands_use_each_acp_providers_validator_and_session(self):
        for agent in ('claude', 'grok-build', 'cursor', 'copilot', 'codex'):
            with self.subTest(agent=agent):
                self.activate(agent)
                before = self.store.read()
                owned = before['owned'][0]
                self.backend.records[owned['name']]['acpx']['available_commands'] = [
                    {'name': name} for name in ('help', 'context', 'commands')]
                validator = self.c.adapter.Backend().validate_command
                with patch.object(self.backend, 'validate_command', side_effect=validator, create=True):
                    for prefix in ('/d', '$d'):
                        for command in ('/help', '/context  detail\r\n  literal ', '/commands'):
                            events = []
                            self.c.send(prefix + ' ' + command, output=events.append)
                            self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'message'), command)
                            self.assertEqual(self.store.read()['main'], before['main'])
                    calls = len(self.backend.calls)
                    with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
                        self.c.send('/d /unknown-command')
                    self.assertEqual(len(self.backend.calls), calls)
                self.assertEqual(route('/help', self.store.read())['route'], 'help')
                self.assertEqual(route('/context', self.store.read())['route'], 'host')
                self.c.off()

    @patch('native_agy.prepare', return_value='gemini-3.8-flash-high')
    @patch('native_agy.native_inventory', return_value={'commands': [{'name': 'help'}, {'name': 'teamwork-preview'}]})
    def test_direct_agy_commands_handoff_once_and_keep_native_followups(self, inventory, prepare):
        import native_agy
        self.activate('agy')
        original = self.store.read()['main']
        with patch.object(self.backend, 'validate_command', create=True,
                          side_effect=lambda owned, text: native_agy.validate_command(text, owned['workspace'])):
            for payload in ('/d /help', '$d /teamwork  task\r\n  details ', '/d follow up'):
                events = []
                self.c.send(payload, output=events.append)
                self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'message'), direct_payload(payload))
                self.assertEqual(self.backend.closed, [original])
                owned = self.store.read()['owned'][0]
                self.assertEqual(owned['transport'], 'native')
                self.assertEqual(owned['providerSession'], 'native-fixture')
        self.c.off()

    def test_menu_and_setup_replies_never_reach_provider(self):
        self.activate()
        self.c.settings_menu()
        self.assertEqual(route('/d task', self.store.read())['route'], 'settings')
        with self.assertRaises(RuntimeError):
            self.c.send('/d task')
        self.c.settings_menu(dismiss=True)
        self.c.frontend()
        self.assertEqual(route('/d task', self.store.read())['route'], 'setup')

    def test_hooks_allow_normal_host_work_and_guard_delegated_work(self):
        self.activate()
        context = self.event('Host task')['hookSpecificOutput']['additionalContext']
        self.assertIn('Handle this turn normally in Codex', context)
        self.assertEqual(self.event(name='PreToolUse', tool_name='spawn_agent'), {})
        context = self.event('/d PRIVATE_TASK')['hookSpecificOutput']['additionalContext']
        self.assertIn('relay --request ', context)
        self.assertNotIn('PRIVATE_TASK', context)
        self.assertEqual(self.event(name='PreToolUse', tool_name='spawn_agent')['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertNotIn('PRIVATE_TASK', self.store.path.read_text())

    def test_compaction_restores_host_and_completed_turns(self):
        self.activate()
        self.event('local')
        self.assertIn('normally in Codex', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        self.event('/d hello')
        self.assertIn('relay --request ', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        self.c.send_request(self.store.read()['turnRoute']['requestId'], output=lambda e: None)
        self.assertIn('already dispatched', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        with self.assertRaisesRegex(RuntimeError, 'already dispatched'):
            self.c.send('/d hello')

    def test_real_controller_cli_has_no_mode_command(self):
        self.activate()
        env = dict(os.environ, CLI_MODE_DATA=str(self.store.root))
        command = [sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'modes',
                   '--workspace', str(self.root)]
        for args in (['mode'], ['mode', '--choice', 'passthrough']):
            result = subprocess.run(command + args, env=env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('invalid choice', result.stderr)
        self.assertEqual(self.store.read()['routingMode'], 'direct')


if __name__ == '__main__':
    unittest.main()
