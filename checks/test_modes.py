"""Host routing is independent of provider activation, configuration and transport."""
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

    def test_fresh_and_old_state_default_to_direct_and_saved_choices_survive(self):
        self.assertEqual(self.store.read()['routingMode'], 'direct')
        self.activate()
        self.assertEqual(route('ordinary message', self.store.read())['route'], 'host')
        self.assertEqual(route('/d task', self.store.read())['route'], 'direct')
        self.c.mode('passthrough')
        self.assertEqual(self.store.read()['routingMode'], 'passthrough')
        state = self.store.read()
        state.pop('routingMode')
        self.store.path.write_text(json.dumps(state), encoding='utf-8')
        self.assertEqual(self.store.read()['routingMode'], 'direct')
        self.assertEqual(route('ordinary message', state)['route'], 'host')
        state['routingMode'] = 'unknown'
        self.store.path.write_text(json.dumps(state), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.read()

    def test_unified_active_settings_for_all_agents_and_prefixes(self):
        for agent in backend_ids():
            self.activate(agent)
            for routing in ('passthrough', 'direct'):
                self.c.mode(routing)
                before = deepcopy(self.store.read())
                calls = len(self.backend.calls)
                for prefix in ('/', '$'):
                    for alias in ('mode', 'menu', 'model'):
                        self.assertEqual(route(prefix+'CLI '+alias, self.store.read()), {'route':'settings'})
                        result = self.c.settings_menu()
                        for label in ('Agent Settings', '1. Change model', '2. Change effort',
                                      '3. Change access', '4. Change routing mode', '5. Toggle activity progress'):
                            self.assertIn(label, result['activationMenu'])
                        for key in ('active','main','owned','settings','routingMode','generation'):
                            self.assertEqual(result[key], before[key])
                        for number, phase in (('1','model'),('2','effort'),('3','access')):
                            self.assertEqual(route(number,result), {'route':'tune','phase':phase,'text':''})
                        self.assertEqual(route('4',result), {'route':'mode','choice':''})
                        self.assertEqual(route('X',result), {'route':'settings-dismiss'})
                        self.assertEqual(route('done',result), {'route':'settings-dismiss'})
                        self.assertNotIn('Done', result['activationMenu'])  # X closes settings.
                        self.c.mode()
                        returned = self.c.mode(routing)
                        self.assertIn('Agent Settings', returned['activationMenu'])
                        self.c.settings_menu(dismiss=True)
                        self.assertIsNone(self.store.read()['pending'])
                self.assertEqual(len(self.backend.calls),calls)
            self.c.off()

    def test_unified_settings_hooks_never_stop_or_dispatch(self):
        self.activate('codex')
        self.c.mode('direct')
        for alias in ('mode','menu','model'):
            context = self.event('/cli '+alias)['hookSpecificOutput']['additionalContext']
            self.assertIn('settings`',context)
        self.c.settings_menu()
        context = self.event('X')['hookSpecificOutput']['additionalContext']
        self.assertIn('settings --dismiss',context)
        self.assertTrue(self.store.read()['active'])
        self.c.settings_menu(dismiss=True)
        self.assertEqual(route('/cli model gpt-5.6-sol',self.store.read()),
                         {'route':'tune','phase':'model','text':'gpt-5.6-sol'})
        self.c.off()
        for prefix in ('/', '$'):
            for command in ('mode', 'menu', 'model', 'mode direct', 'model gpt-6-astra'):
                result = route(prefix+'CLI '+command, self.store.read())
                self.assertEqual(result, {'route':'hint',
                    'text':'CLI-MODE: Agent not activated. /CLI to setup'})

    @patch('frontends.first_start', return_value=False)
    @patch('frontends.confirmed', return_value=True)
    @patch('frontends.routing_readiness', return_value={'ready':True})
    def test_agent_defaults_offer_routing_and_return_without_activation(self, readiness, confirmed, first_start):
        for agent in backend_ids():
            with self.subTest(agent=agent):
                self.assertEqual(self.store.read()['routingMode'], 'direct')
                result = self.c.frontend(agent)
                self.assertIn('Mode: Direct', result['activationMenu'])
                self.assertIn('3. Change routing mode', result['activationMenu'])
                before = deepcopy(self.store.read())
                self.assertEqual(route('3', before), {'route':'mode', 'choice':''})
                self.c.mode()
                result = self.c.mode('passthrough')
                self.assertIn('Agent Settings', result['activationMenu'])
                self.assertIn('Mode: Passthrough', result['activationMenu'])
                self.assertEqual(result['pending'], before['pending'])
                self.assertFalse(result['active'])
                self.assertEqual(result['owned'], before['owned'])
                self.assertEqual(result['settings'], before['settings'])
                self.assertEqual(route('1', result), {'route':'setup'})
                self.c.mode()
                dismissed = self.c.mode(dismiss=True)
                self.assertIn('Mode: Passthrough', dismissed['activationMenu'])
                self.assertEqual(dismissed['pending'], before['pending'])
                self.c.mode('direct')
        self.assertEqual(self.backend.calls, [])

    def test_mode_uses_shared_menu_renderer_and_current_choice_style(self):
        import frontends
        import menu_view
        for current in ('passthrough', 'direct'):
            menu = frontends.routing_mode_menu(current)
            self.assertIn(current.title() + '  (current)', menu)
            self.assertEqual(menu.count('X. Back'), 1)
            self.assertNotIn('Next page', menu)
            html = menu_view.render(menu)
            self.assertIn('class="subtitle">Routing Mode', html)
            self.assertIn('color:var(--green,#42d392)', html)
            self.assertIn('background:var(--card,#202124)', html)
        with self.store.edit() as state:
            state['pending'] = {'phase':'activation', 'stage':'menu', 'onboarding':'check'}
        self.assertEqual(route('3', self.store.read()), {'route':'setup'})

    def test_single_page_menu_preserves_every_unrelated_state_field(self):
        for active in (False, True):
            if active:
                self.activate()
            # Even another pending settings menu is preserved, not replaced.
            with self.store.edit() as state:
                state['pending'] = dict(id='saved', stage='menu', phase='effort', draft={'effort':'high'})
            before = deepcopy(self.store.read())
            calls = list(self.backend.calls)
            menu = self.c.mode()['activationMenu']
            self.assertIn('1. Passthrough', menu)
            self.assertIn('2. Direct', menu)
            self.assertNotIn('Next page', menu)
            self.assertEqual(route('2', self.store.read()), {'route':'mode', 'choice':'direct'})
            self.c.mode('direct')
            for key, value in before.items():
                if key not in ('routingMode', 'modeMenu', 'turnRoute'):
                    self.assertEqual(self.store.read()[key], value, key)
            self.assertEqual(calls, self.backend.calls)
            with self.store.edit() as state:
                state['pending'] = None

    def test_exit_only_dismisses_mode_menu_on_or_off(self):
        for active in (False, True):
            if active:
                self.activate()
            before = self.store.read()
            self.c.mode()
            self.assertEqual(route('X', self.store.read()), {'route':'mode-dismiss'})
            self.event('X')
            self.assertEqual(self.store.read()['active'], before['active'])
            self.c.mode(dismiss=True)
            after = self.store.read()
            for key in ('active', 'main', 'owned', 'settings', 'pending', 'generation', 'routingMode'):
                self.assertEqual(after[key], before[key])
            self.assertFalse(self.backend.closed)

    def test_mode_selection_during_inflight_work_does_not_cancel_or_reconfigure(self):
        self.activate()
        with self.store.edit() as state:
            state['inflight'] = {'running': {'session':state['main'], 'running':True}}
        before = self.store.read()
        self.c.mode('direct')
        self.assertEqual(self.store.read()['inflight'], before['inflight'])
        self.assertEqual(self.store.read()['generation'], before['generation'])
        self.assertTrue(self.store.read()['active'])
        self.assertFalse(self.backend.closed)

    def test_control_aliases_and_invalid_choices_never_delegate(self):
        self.activate()
        for prefix in ('/', '$'):
            self.assertEqual(route(prefix+'CLI mode DIRECT', self.store.read()), {'route':'mode','choice':'direct'})
            self.assertEqual(route(prefix+'cli mode', self.store.read()), {'route':'settings'})
            self.assertEqual(route(prefix+'cli mode invalid task', self.store.read())['route'], 'hint')
        with self.assertRaises(ValueError):
            self.c.mode('invalid')

    def test_direct_only_matches_complete_leading_tokens(self):
        self.activate()
        self.c.mode('direct')
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

    def test_no_unprefixed_send_can_reach_any_backend_in_direct_mode(self):
        for agent in backend_ids():
            with self.subTest(agent=agent):
                self.activate(agent)
                self.c.mode('direct')
                before = len(self.backend.calls)
                for prompt in ('host task', '/context', '/d', '$d  '):
                    with self.assertRaises(RuntimeError):
                        self.c.send(prompt)
                self.assertEqual(len(self.backend.calls), before)
                events = []
                self.event('$d   task\r\n    detail\n')
                self.c.send_request(self.store.read()['turnRoute']['requestId'], output=events.append)
                self.assertEqual(''.join(e['text'] for e in events if e['type']=='message'), '  task\r\n    detail\n')
                self.c.off()

    def test_direct_native_command_is_validated_after_prefix_removal(self):
        self.activate('grok-build')
        self.c.mode('direct')
        owned = self.store.read()['owned'][0]
        self.backend.records[owned['name']]['acpx']['available_commands'] = [{'name':'context'}]
        before = len(self.backend.calls)
        validator = self.c.adapter.Backend().validate_command
        with patch.object(self.backend, 'validate_command', side_effect=validator, create=True):
            with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
                self.c.send('/d /not-a-command')
            self.assertEqual(len(self.backend.calls), before)
            events = []
            self.c.send('/d /context', output=events.append)
        self.assertIn('/context', ''.join(e['text'] for e in events if e['type']=='message'))

    def test_passthrough_preserves_legacy_payload_and_switching_keeps_main(self):
        before = self.activate()
        self.c.mode('direct')
        self.c.mode('passthrough')
        events = []
        # Avoid interpreting /d as a native command: the raw prompt reaches validation unchanged.
        with patch.object(self.c.backend, 'validate_command', create=True) as validate, patch.object(self.c.adapter, 'NATIVE_HANDOFF', False), patch('adapters.descriptor'):
            validate.return_value = {}
            self.c.send('/d literal', output=events.append)
            self.assertEqual(validate.call_args.args[1], '/d literal')
        self.assertEqual(self.store.read()['main'], before['main'])
        self.assertEqual(self.store.read()['settings'], before['settings'])

    def test_direct_commands_use_each_acp_providers_validator_and_session(self):
        for agent in ('claude', 'grok-build', 'cursor', 'copilot', 'codex'):
            with self.subTest(agent=agent):
                self.activate(agent)
                self.c.mode('direct')
                before = self.store.read()
                owned = before['owned'][0]
                self.backend.records[owned['name']]['acpx']['available_commands'] = [
                    {'name':name} for name in ('help', 'context', 'commands')]
                validator = self.c.adapter.Backend().validate_command
                with patch.object(self.backend, 'validate_command', side_effect=validator, create=True):
                    for prefix in ('/d', '$d'):
                        for command in ('/help', '/context  detail\r\n  literal ', '/commands'):
                            events = []
                            self.c.send(prefix + ' ' + command, output=events.append)
                            self.assertEqual(''.join(e['text'] for e in events if e['type']=='message'), command)
                            self.assertEqual(self.store.read()['main'], before['main'])
                    calls = len(self.backend.calls)
                    with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
                        self.c.send('/d /unknown-command')
                    self.assertEqual(len(self.backend.calls), calls)
                self.assertEqual(route('/help', self.store.read())['route'], 'help')
                self.assertEqual(route('/context', self.store.read())['route'], 'host')
                self.c.off()

    @patch('native_agy.prepare', return_value='gemini-3.8-flash-high')
    @patch('native_agy.native_inventory', return_value={'commands':[{'name':'help'}, {'name':'teamwork-preview'}]})
    def test_direct_agy_commands_handoff_once_and_keep_native_followups(self, inventory, prepare):
        import native_agy
        self.activate('agy')
        self.c.mode('direct')
        original = self.store.read()['main']
        with patch.object(self.backend, 'validate_command', create=True,
                          side_effect=lambda owned, text: native_agy.validate_command(text, owned['workspace'])):
            for payload in ('/d /help', '$d /teamwork  task\r\n  details ', '/d follow up'):
                events = []
                self.c.send(payload, output=events.append)
                self.assertEqual(''.join(e['text'] for e in events if e['type']=='message'), direct_payload(payload))
                self.assertEqual(self.backend.closed, [original])
                owned = self.store.read()['owned'][0]
                self.assertEqual(owned['transport'], 'native')
                self.assertEqual(owned['providerSession'], 'native-fixture')
        self.c.off()

    def test_menu_and_setup_replies_never_reach_provider(self):
        self.activate()
        self.c.mode('direct')
        self.c.mode()
        self.assertEqual(route('/d task', self.store.read())['route'], 'mode')
        with self.assertRaises(RuntimeError):
            self.c.send('/d task')
        self.c.mode(dismiss=True)
        self.c.frontend()
        self.assertEqual(route('/d task', self.store.read())['route'], 'setup')

    def test_mode_persists_across_stop_and_reactivation(self):
        self.c.mode('direct')
        self.assertFalse(self.store.read()['active'])
        self.activate()
        self.c.off()
        self.activate()
        self.assertEqual(self.store.read()['routingMode'], 'direct')

    def test_hooks_allow_normal_host_work_and_guard_delegated_work(self):
        self.activate()
        self.c.mode('direct')
        context = self.event('Host task')['hookSpecificOutput']['additionalContext']
        self.assertIn('Handle this turn normally in Codex', context)
        self.assertEqual(self.event(name='PreToolUse', tool_name='spawn_agent'), {})
        context = self.event('/d PRIVATE_TASK')['hookSpecificOutput']['additionalContext']
        self.assertIn('relay --request ', context)
        self.assertNotIn('PRIVATE_TASK', context)
        self.assertEqual(self.event(name='PreToolUse', tool_name='spawn_agent')['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertNotIn('PRIVATE_TASK', self.store.path.read_text())

    def test_compaction_restores_host_direct_menu_and_completed_turn(self):
        self.activate()
        self.c.mode('direct')
        self.event('local')
        self.assertIn('normally in Codex', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        self.c.mode()
        self.assertIn('single-page', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        self.c.mode('direct')
        self.event('/d hello')
        self.assertIn('Direct mode', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        self.c.send_request(self.store.read()['turnRoute']['requestId'], output=lambda e: None)
        self.assertIn('already dispatched', self.event(name='SessionStart', source='compact')['hookSpecificOutput']['additionalContext'])
        with self.assertRaisesRegex(RuntimeError, 'already dispatched'):
            self.c.send('/d hello')

    def test_mode_change_during_send_preflight_keeps_accepted_routing_snapshot(self):
        self.activate()
        self.c.mode('passthrough')
        before = len(self.backend.calls)
        original = self.backend.metadata
        def metadata(owned):
            self.c.mode('direct')
            return original(owned)
        events = []
        with patch.object(self.backend, 'metadata', side_effect=metadata):
            self.c.send('host task', output=events.append)
        self.assertEqual([e['text'] for e in events if e['type'] == 'message'], ['host task'])
        self.assertEqual(len([args for args in self.backend.calls[before:] if '--file' in args]), 1)
        self.assertEqual(self.store.read()['routingMode'], 'direct')
        self.assertTrue(self.store.read()['active'])
        self.assertFalse(self.backend.closed)

    def test_real_controller_cli_renders_and_changes_mode_without_touching_activation(self):
        before = self.activate()
        env = dict(os.environ, CLI_MODE_DATA=str(self.store.root))
        command = [sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'modes',
                   '--workspace', str(self.root)]
        menu = self.root / 'mode.html'
        result = subprocess.run(command + ['--menu-output', str(menu), 'mode'], env=env,
                                capture_output=True, text=True, check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout)['menuView']['path'], str(menu))
        self.assertTrue(menu.is_file())
        for args in (['mode', '--choice', 'direct'], ['mode', '--dismiss']):
            subprocess.run(command + args, env=env, capture_output=True, text=True, check=True, timeout=15)
        after = self.store.read()
        self.assertEqual(after['routingMode'], 'direct')
        for key in ('active', 'main', 'owned', 'settings', 'pending', 'generation'):
            self.assertEqual(before[key], after[key])
