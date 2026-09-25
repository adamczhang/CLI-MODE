"""Claude Code backend: catalog selection, verification, admission and isolation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, PLUGIN
import acpx
import adapters
import claude_code
import frontends
import native_commands
from controller import Controller
from state import Store, route, backend_ids


class Registry(unittest.TestCase):
    def test_both_backends_registered_and_implemented(self):
        self.assertIn('claude', backend_ids())
        self.assertIn('claude', adapters.implemented())
        names = [item['displayName'] for item in frontends.backends()]
        self.assertIn('Claude Code CLI', names)

    def test_unimplemented_backend_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, 'No runtime adapter'):
            adapters.module('future')

    def test_entrypoint_resolves_inside_the_plugin(self):
        for item in frontends.backends():
            registry = PLUGIN / 'codex/skills/cli-mode/references'
            entry = (registry / item['entrypoint']).resolve()
            self.assertEqual(entry, (PLUGIN / 'backends' / item['id'] / 'backend.md').resolve())
            self.assertTrue(entry.exists())


class Controls(unittest.TestCase):
    def test_claude_controls_mirror_the_antigravity_controls(self):
        off = {'active': False}
        self.assertEqual(route('/cli claude', off), {'route': 'frontend', 'agent': 'claude'})
        self.assertEqual(route('$CLI CLAUDE', off), {'route': 'frontend', 'agent': 'claude'})
        self.assertEqual(route('/cli bind claude', off), {'route': 'bind', 'agent': 'claude'})
        self.assertEqual(route('$cli Bind Claude', off), {'route': 'bind', 'agent': 'claude'})
        self.assertEqual(route('/cli agy', off), {'route': 'frontend', 'agent': 'agy'})
        self.assertEqual(route('/cli bind agy', off), {'route': 'bind', 'agent': 'agy'})

    def test_partial_and_unknown_agent_names_are_not_controls(self):
        off = {'active': False}
        self.assertEqual(route('/cli claud', off)['route'], 'hint')
        self.assertEqual(route('/cli bind claud', off)['route'], 'hint')
        # An agent name with trailing text is not a menu command.
        self.assertEqual(route('/cli claude do the thing', off)['route'], 'hint')

    def test_help_lists_both_agents(self):
        import help_view
        commands = help_view.text()
        self.assertIn('cla (Claude Code)', commands)
        self.assertIn('agy (Antigravity)', commands)
        self.assertIn('/cli bind|spawn <agent> [name]', commands)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_defaults_are_opus_high_bypass(self):
        self.assertEqual(claude_code.DEFAULTS,
                         dict(model='opus[1m]', effort='high', access='allow'))
        chosen = claude_code.selection(self.root, 'opus', 'allow', 'high')
        self.assertEqual(chosen['model'], 'opus[1m]')
        self.assertEqual(chosen['modelName'], 'Opus 5.5')
        self.assertEqual(chosen['effort'], 'High')
        self.assertEqual(chosen['effortValue'], 'high')
        self.assertEqual(chosen['mode'], 'bypassPermissions')
        self.assertEqual(chosen['accessName'], 'Bypass permissions')

    def test_model_and_effort_are_separate_selectors(self):
        chosen = claude_code.selection(self.root, 'opus', 'allow', 'high')
        self.assertEqual(claude_code.setting_steps(chosen), [
            ['set', 'model', 'opus[1m]'],
            ['set', 'effort', 'high'],
            ['set', 'mode', 'bypassPermissions']])

    def test_effort_accepts_label_or_value_and_defaults_to_high(self):
        for effort in ('high', 'High', 'HIGH'):
            self.assertEqual(claude_code.selection(self.root, 'sonnet', 'allow', effort)['effortValue'], 'high')
        self.assertEqual(claude_code.selection(self.root, 'sonnet', 'allow')['effortValue'], 'high')

    def test_unadvertised_choices_are_refused_not_substituted(self):
        with self.assertRaisesRegex(ValueError, 'Model is not unambiguously advertised'):
            claude_code.selection(self.root, 'gemini-3.8-flash-high', 'allow', 'high')
        with self.assertRaisesRegex(ValueError, 'Effort is not unambiguously advertised'):
            claude_code.selection(self.root, 'opus', 'allow', 'ludicrous')
        with self.assertRaisesRegex(ValueError, 'Unsupported access mapping'):
            claude_code.selection(self.root, 'opus', 'plan', 'high')

    def test_plan_and_auto_are_advertised_but_never_mapped(self):
        data = claude_code.catalog(self.root)
        mapped = {item['nativeValue'] for item in data['accessControl']['options']}
        self.assertEqual(mapped, {'default', 'acceptEdits', 'bypassPermissions'})
        unmapped = {item['nativeValue'] for item in data['accessControl']['advertisedUnmapped']}
        self.assertEqual(unmapped, {'plan', 'auto'})

    def test_ambiguous_default_row_is_excluded_from_the_menu(self):
        data = claude_code.catalog(self.root)
        self.assertNotIn('default', {family['modelId'] for family in data['modelFamilies']})
        for family in data['modelFamilies']:
            self.assertNotIn('default', {item['value'] for item in family['efforts']})

    def test_old_cached_catalog_and_saved_alias_are_upgraded(self):
        data = claude_code.catalog(self.root)
        opus = next(f for f in data['modelFamilies'] if f['modelId'] == 'opus[1m]')
        opus.update(modelId='opus', name='Opus 5')
        haiku = next(f for f in data['modelFamilies'] if f['modelId'] == 'haiku')
        haiku.pop('effortSupported')
        haiku['efforts'] = [{'name': 'High', 'value': 'high'}]
        data['requestedDefaultModelId'] = 'opus'
        (self.root / 'catalogs').mkdir()
        (self.root / 'catalogs/claude.json').write_text(json.dumps(data))
        self.assertEqual(claude_code.selection(self.root, 'opus', 'allow', 'high')['model'], 'opus[1m]')
        self.assertIsNone(claude_code.selection(self.root, 'haiku', 'allow')['effortKey'])

    def test_old_refresh_that_omitted_opus_still_has_a_usable_default(self):
        data = claude_code.catalog(self.root)
        data['modelFamilies'] = [f for f in data['modelFamilies'] if f['modelId'] != 'opus[1m]']
        (self.root / 'catalogs').mkdir()
        (self.root / 'catalogs/claude.json').write_text(json.dumps(data))
        self.assertEqual(claude_code.selection(self.root, **claude_code.DEFAULTS)['model'], 'opus[1m]')

    def test_haiku_has_no_effort_steps_or_invented_menu_choices(self):
        chosen = claude_code.selection(self.root, 'haiku', 'allow')
        self.assertEqual(chosen['effort'], 'Provider default')
        self.assertEqual(claude_code.setting_steps(chosen), [
            ['set', 'model', 'haiku'], ['set', 'mode', 'bypassPermissions']])
        self.assertEqual(frontends.phase_options(self.root, 'claude', 'effort', chosen),
                         [('Provider default', None)])
        self.assertEqual(claude_code.selection(self.root, 'haiku', 'allow', 'Provider default'), chosen)
        with self.assertRaisesRegex(ValueError, 'no effort control'):
            claude_code.selection(self.root, 'haiku', 'allow', 'high')

    def test_refresh_accepts_absent_haiku_effort_but_rejects_malformed_selector(self):
        import catalogs
        data = claude_code.catalog(self.root)
        record = {'acpx': {'current_model_id': 'haiku', 'available_models': ['haiku', 'sonnet'],
                  'config_options': [{'id': 'mode', 'options': [{'value': 'bypassPermissions'}]}]}}
        refreshed = catalogs.from_metadata('claude', data, record)
        haiku = next(f for f in refreshed['modelFamilies'] if f['modelId'] == 'haiku')
        self.assertEqual(haiku['efforts'], [])
        self.assertFalse(haiku['effortSupported'])
        sonnet = next(f for f in refreshed['modelFamilies'] if f['modelId'] == 'sonnet')
        self.assertTrue(sonnet['efforts'])
        record['acpx']['config_options'].append({'id': 'effort', 'options': []})
        with self.assertRaisesRegex(ValueError, 'no supported advertised effort'):
            catalogs.from_metadata('claude', data, record)


class Verification(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.settings = claude_code.selection(self.root, 'opus', 'allow', 'high')
        self.owned = dict(name='s', workspace=str(self.root), settings=self.settings)

    def record(self, **options):
        values = dict(model='opus[1m]', effort='high', mode='bypassPermissions')
        values.update(options)
        return {'acpxRecordId': 'record-1', 'acpSessionId': 'rotated-session',
                'acpx': {'current_model_id': values['model'],
                         'config_options': [{'id': key, 'currentValue': value}
                                            for key, value in values.items()]}}

    def test_identity_tracks_the_provider_conversation(self):
        self.assertEqual(claude_code.provider_identity(self.record()), 'rotated-session')

    def test_verify_requires_all_three_accepted_settings(self):
        backend = claude_code.Backend()
        with patch.object(backend, 'metadata', return_value=self.record()):
            self.assertEqual(backend.verify(self.owned), 'rotated-session')
        for wrong in (dict(effort='low'), dict(mode='acceptEdits'), dict(model='sonnet')):
            with patch.object(backend, 'metadata', return_value=self.record(**wrong)):
                with self.assertRaisesRegex(RuntimeError, 'did not accept'):
                    backend.verify(self.owned)

    def test_verify_tolerates_extra_options_added_by_a_model_switch(self):
        backend = claude_code.Backend()
        extra = self.record()
        extra['acpx']['config_options'].append({'id': 'fast', 'currentValue': 'off'})
        with patch.object(backend, 'metadata', return_value=self.record()):
            self.assertEqual(backend.verify(self.owned), 'rotated-session')

    def test_haiku_verifies_without_effort_but_still_checks_model_and_access(self):
        owned = dict(self.owned, settings=claude_code.selection(self.root, 'haiku', 'allow'))
        record = self.record(model='haiku')
        record['acpx']['config_options'] = [x for x in record['acpx']['config_options'] if x['id'] != 'effort']
        backend = claude_code.Backend()
        self.assertEqual(backend.verify(owned, record), 'rotated-session')
        for item in record['acpx']['config_options']:
            if item['id'] == 'mode':
                item['currentValue'] = 'default'
        with self.assertRaisesRegex(RuntimeError, 'did not accept'):
            backend.verify(owned, record)

    def test_argv_uses_the_claude_profile_and_owned_ttl(self):
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = claude_code.Backend().command(self.owned, ['sessions', 'show', 's'])
        self.assertIn('claude', command)
        self.assertNotIn('antigravity', command)
        self.assertEqual(command[command.index('--ttl') + 1], '3600')  # One hour unless /cli timeout changes it.
        self.assertIn('--approve-all', command)

    def test_stricter_access_drops_host_auto_approval(self):
        owned = dict(self.owned, settings=claude_code.selection(self.root, 'opus', 'prompt', 'high'))
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = claude_code.Backend().command(owned, ['status'])
        self.assertNotIn('--approve-all', command)
        self.assertIn('--non-interactive-permissions', command)


class ModelTransitions(unittest.TestCase):
    def test_model_switch_drops_and_restores_effort_without_replacing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeBackend()
            control = Controller(Store('haiku-switch', root, root / 'state'), backend, 'claude')
            control.frontend('claude')
            control.activate('opus', 'allow', effort='high', agent='claude')
            identity = control.store.read()['owned'][0]['providerSession']
            backend.calls.clear()
            with control.store.edit() as state:
                state['turnRoute'] = dict(route='tune', choice='haiku')
            active = control.tune_choice('model')
            self.assertEqual(active['settings']['effort'], 'Provider default')
            self.assertEqual(active['owned'][0]['providerSession'], identity)
            self.assertFalse(any('effort' in call for call in backend.calls))
            # A user cannot set an effort the model does not support.
            with control.store.edit() as state:
                state['turnRoute'] = dict(route='tune', choice='high')
            menu = control.tune_choice('effort')
            self.assertIn('No advertised effort matches', menu['message'])
            self.assertEqual(control.store.read()['pending']['choices'][0]['value'], None)
            control.settings_menu(dismiss=True)
            backend.calls.clear()
            with control.store.edit() as state:
                state['turnRoute'] = dict(route='tune', choice='sonnet')
            active = control.tune_choice('model')
            self.assertEqual(active['settings']['effortValue'], 'high')
            self.assertEqual(active['owned'][0]['providerSession'], identity)
            self.assertTrue(any('effort' in call for call in backend.calls))
            self.assertTrue(control.off()['shutdownComplete'])


class Commands(unittest.TestCase):
    def test_terminal_only_commands_are_refused_before_dispatch(self):
        for name in ('clear', 'login', 'logout', 'todos', 'cost'):
            with self.assertRaisesRegex(RuntimeError, 'did not dispatch it'):
                claude_code.Backend().validate_command({'advertisedCommands': None}, '/' + name)

    def test_a_refusal_names_the_bound_agent(self):
        # The shared module must not name one provider to users of another.
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable Claude command'):
            claude_code.Backend().validate_command({'advertisedCommands': ['review']}, '/nope')
        import grok_build
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable Grok command'):
            grok_build.Backend().validate_command({'advertisedCommands': ['review']}, '/nope')

    def test_only_claude_refuses_its_adapters_terminal_only_list(self):
        # /cost is terminal-only for Claude, but is not a fact about other runtimes.
        import cursor_agent
        self.assertEqual(cursor_agent.Backend().validate_command({'advertisedCommands': None}, '/cost'), {})

    def test_unseen_command_is_admitted_when_no_list_was_observed(self):
        # ACPX does not persist availableCommands, so an unobserved list is not
        # evidence that a command is unknown.
        self.assertEqual(native_commands.validate(None, '/review the diff'), {})

    def test_unknown_command_is_refused_once_the_list_is_known(self):
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
            native_commands.validate(['review', 'compact'], '/nonsense')
        self.assertEqual(native_commands.validate(['review', 'compact'], '/review now'), {})

    def test_host_controls_are_not_provider_commands(self):
        self.assertFalse(native_commands.command_request('/cli claude'))
        self.assertFalse(native_commands.command_request('/help'))
        self.assertTrue(native_commands.command_request('/review'))

    def test_advertised_commands_are_captured_from_the_notification(self):
        event = {'params': {'update': {'sessionUpdate': 'available_commands_update',
                 'availableCommands': [{'name': 'review'}, {'name': 'compact'}]}}}
        del event  # Commands are read from the session record ACPX keeps, not live notifications.
        record = {'acpx': {'available_commands': [{'name': 'review'}, {'name': 'compact'}]}}
        self.assertEqual(native_commands.from_record(record), ['review', 'compact'])
        self.assertIsNone(native_commands.from_record({'acpx': {}}))

    def test_claude_never_switches_transport(self):
        self.assertFalse(claude_code.NATIVE_HANDOFF)
        self.assertTrue(__import__('agy').NATIVE_HANDOFF)


class Relay(unittest.TestCase):
    def test_public_relay_is_plans_and_plain_messages_only(self):
        def update(kind, **rest):
            return {'params': {'update': dict(sessionUpdate=kind, **rest)}}
        for private in ('agent_thought_chunk', 'tool_call', 'tool_call_update'):
            self.assertIsNone(claude_code.public_event(
                update(private, content={'type': 'text', 'text': 'PRIVATE'})))
        self.assertEqual(claude_code.public_event(
            update('agent_message_chunk', content={'type': 'text', 'text': 'hi'})),
            {'type': 'message', 'text': 'hi'})
        self.assertEqual(claude_code.public_event(update('plan', entries=[
            {'content': 'step', 'status': 'pending', 'priority': 'high'}])),
            {'type': 'plan', 'entries': [{'content': 'step', 'status': 'pending'}]})

    def test_both_backends_share_one_translation_path(self):
        import agy
        self.assertIs(claude_code.public_event, acpx.public_event)
        self.assertIs(agy.public_event, acpx.public_event)
        self.assertIs(claude_code.PublicRelay, acpx.PublicRelay)

    def test_non_public_session_updates_are_not_relayed(self):
        for kind in ('usage_update', 'config_option_update', 'session_info_update',
                     'available_commands_update', 'subagent_spawned'):
            self.assertIsNone(claude_code.public_event(
                {'params': {'update': {'sessionUpdate': kind}}}))


class Isolation(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = Path(tempfile.mkdtemp())
        self.store = Store('claude-thread', str(self.workspace), self.root)

    def control(self):
        return Controller(self.store, FakeBackend())

    def test_saved_backend_selects_its_own_adapter(self):
        with self.store.edit() as state:
            state['backend'] = 'claude'
        self.assertIs(self.control().adapter, claude_code)

    def test_setup_receipts_are_per_backend(self):
        frontends.check_and_save(self.root, 'agy', check=lambda agent: dict(confirmed=True, checks=[]))
        self.assertTrue(frontends.confirmed(self.root, 'agy'))
        # One backend's installation receipt never vouches for another.
        self.assertFalse(frontends.confirmed(self.root, 'claude'))

    def test_owned_session_of_another_backend_blocks_activation(self):
        control = self.control()
        control.frontend('claude')
        with self.store.edit() as state:
            state['main'] = 'existing'
            state['owned'] = [dict(name='existing', backend='agy', role='main',
                                   ready=True, settings={}, workspace=str(self.workspace))]
        with self.assertRaisesRegex(RuntimeError, 'Run off successfully'):
            control.activate('opus', 'allow', effort='high', agent='claude')

    def test_frontend_records_the_backend_that_owns_the_menu(self):
        control = self.control()
        control.frontend('claude')
        self.assertEqual(self.store.read()['pending']['backend'], 'claude')
        control.frontend('home')
        self.assertIsNone(self.store.read()['pending']['backend'])


class Menus(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_agent_menu_lists_both_agents(self):
        frontends.check_and_save(self.root, 'agy', check=lambda agent: dict(confirmed=True, checks=[]))
        frontends.check_and_save(self.root, 'claude', check=lambda agent: dict(confirmed=True, checks=[]))
        frontends.check_and_save(self.root, 'grok-build', check=lambda agent: dict(confirmed=True, checks=[]))
        menu = frontends.menu(self.root, 'home', None, {'ready': True}, {'ready': True})
        self.assertIn('1. Antigravity CLI', menu)
        self.assertIn('2. Claude Code CLI', menu)

    def test_first_start_menu_lists_both_agents(self):
        menu = frontends.menu(self.root, 'home', None, {'ready': False, 'reason': 'missing'}, {'ready': True})
        self.assertIn('New User Detected.', menu)
        self.assertIn('1. Antigravity CLI', menu)
        self.assertIn('2. Claude Code CLI', menu)

    def test_claude_activation_menu_shows_verified_defaults(self):
        frontends.check_and_save(self.root, 'claude', check=lambda agent: dict(confirmed=True, checks=[]))
        settings = claude_code.selection(self.root, 'opus', 'allow', 'high')
        menu = frontends.menu(self.root, 'claude', settings, {'ready': True}, {'ready': True})
        self.assertIn('Claude Code CLI', menu)
        self.assertIn('Opus 5', menu)
        self.assertIn('High', menu)
        self.assertIn('Bypass permissions', menu)
        self.assertIn('X. Exit', menu)

    def test_non_windows_probe_list_is_backend_specific(self):
        self.assertIn(('Claude Code CLI', 'claude'), frontends.BACKEND_TOOLS['claude'])
        self.assertIn(('npx', 'npx'), frontends.BACKEND_TOOLS['claude'])
        self.assertNotIn(('Antigravity CLI', 'agy'), frontends.BACKEND_TOOLS['claude'])


class Usage(unittest.TestCase):
    def setUp(self):
        path = PLUGIN / 'backends/claude/scripts/usage-summary.py'
        import importlib.util
        spec = importlib.util.spec_from_file_location('claude_usage', path)
        self.usage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.usage)
        import datetime as dt
        self.now = dt.datetime(2026, 9, 22, 4, 0, tzinfo=dt.timezone.utc)

    def payload(self, limits):
        return {'local_command_run': {'command': 'usage', 'args': ''},
                'usage_report': {'rate_limits': {'limits': limits, 'extra_usage': None}}}

    def test_percent_is_reported_as_given_with_iso_countdown(self):
        summary = self.usage.summarize(self.payload([
            {'kind': 'session', 'group': 'session', 'percent': 24,
             'resets_at': '2026-09-22T08:10:00+00:00', 'severity': 'normal'},
            {'kind': 'weekly_all', 'group': 'weekly', 'percent': 5,
             'resets_at': '2026-09-25T00:00:00+00:00', 'severity': 'normal'}]), self.now)
        self.assertEqual([w['window'] for w in summary['windows']], ['Five hour', 'Weekly'])
        self.assertEqual(summary['windows'][0]['utilization'], '24% used')
        self.assertEqual(summary['windows'][0]['resetRemaining'], '0 days 4 hours')
        self.assertEqual(summary['windows'][1]['resetRemaining'], '2 days 20 hours')

    def test_extra_advertised_windows_render_without_a_code_change(self):
        summary = self.usage.summarize(self.payload([
            {'kind': 'weekly_opus', 'group': 'weekly', 'percent': 60,
             'resets_at': '2026-09-25T00:00:00+00:00'}]), self.now)
        self.assertEqual(summary['windows'][0]['window'], 'Weekly (Opus)')

    def test_structured_scope_uses_labels_without_serializing_metadata(self):
        for scope, expected in [
            ({'model': {'display_name': 'Fable'}, 'surface': None}, 'Weekly (Fable)'),
            ({'model': {'display_name': 'Fable'}, 'surface': {'display_name': 'Code'}}, 'Weekly (Fable, Code)'),
            ({'model': {'id': 'unknown'}}, 'Weekly (scoped)'),
            ({'model': None, 'surface': None}, 'Weekly'),
            ('Fable', 'Weekly (Fable)'),
        ]:
            with self.subTest(scope=scope):
                self.assertEqual(self.usage.window_label({'group': 'weekly', 'scope': scope}), expected)

    def test_a_non_local_or_malformed_response_is_unavailable_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'local command'):
            self.usage.summarize({'usage_report': {}}, self.now)
        with self.assertRaisesRegex(ValueError, 'structured usage metadata'):
            self.usage.summarize({'local_command_run': {'command': 'usage'}}, self.now)
        with self.assertRaisesRegex(ValueError, 'No quota windows'):
            self.usage.summarize(self.payload([]), self.now)

    def test_unparseable_values_are_unavailable_rather_than_inferred(self):
        summary = self.usage.summarize(self.payload([
            {'kind': 'session', 'percent': 'lots', 'resets_at': 'soon'}]), self.now)
        self.assertEqual(summary['windows'][0]['utilization'], 'unavailable')
        self.assertEqual(summary['windows'][0]['resetRemaining'], 'unavailable')

    def test_a_past_reset_is_not_presented_as_a_future_one(self):
        summary = self.usage.summarize(self.payload([
            {'kind': 'session', 'percent': 10, 'resets_at': '2026-09-21T00:00:00+00:00'}]), self.now)
        self.assertEqual(summary['windows'][0]['resetRemaining'], 'due now; refresh required')
        self.assertIsNone(summary['nextReset'])


if __name__ == '__main__':
    unittest.main()
