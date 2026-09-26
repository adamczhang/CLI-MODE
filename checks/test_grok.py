"""Grok Build backend: no native permission mode, separate reasoning effort, aliases."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, PLUGIN
import acpx
import adapters
import frontends
import grok_build
from controller import Controller
from state import Store, route, backend_ids, resolve_backend


class Registry(unittest.TestCase):
    def test_grok_is_registered_and_implemented(self):
        self.assertIn('grok-build', backend_ids())
        self.assertIn('grok-build', adapters.implemented())
        self.assertIn('Grok Build CLI', [item['displayName'] for item in frontends.backends()])

    def test_profile_matches_the_acpx_builtin(self):
        record = next(item for item in frontends.backends() if item['id'] == 'grok-build')
        self.assertEqual(record['acpxProfile'], 'grok-build')
        self.assertEqual(grok_build.Backend.profile, 'grok-build')

    def test_entrypoint_resolves_inside_the_plugin(self):
        registry = PLUGIN / 'codex/skills/cli-mode/references'
        record = next(item for item in frontends.backends() if item['id'] == 'grok-build')
        entry = (registry / record['entrypoint']).resolve()
        self.assertEqual(entry, (PLUGIN / 'backends/grok-build/backend.md').resolve())
        self.assertTrue(entry.exists())


class Controls(unittest.TestCase):
    def test_alias_and_canonical_id_both_resolve(self):
        off = {'active': False}
        for word in ('grok', 'grok-build', 'GROK', 'Grok-Build'):
            self.assertEqual(route('/cli ' + word, off), {'route': 'frontend', 'agent': 'grok-build'})
            self.assertEqual(route('/cli bind ' + word, off), {'route': 'bind', 'agent': 'grok-build'})
        self.assertEqual(route('$cli grok', off), {'route': 'frontend', 'agent': 'grok-build'})

    def test_resolution_is_canonical(self):
        self.assertEqual(resolve_backend('grok'), 'grok-build')
        self.assertEqual(resolve_backend('grok-build'), 'grok-build')
        self.assertEqual(resolve_backend('gro'), 'grok-build')  # Its three-letter tag.
        self.assertIsNone(resolve_backend('grk'))
        self.assertIsNone(resolve_backend('grokbuild'))

    def test_partial_or_trailing_text_is_not_a_control(self):
        off = {'active': False}
        self.assertEqual(route('/cli gr', off)['route'], 'hint')
        self.assertEqual(route('/cli grok now please', off)['route'], 'hint')
        self.assertEqual(route('/cli bind gr', off)['route'], 'hint')

    def test_help_lists_the_third_agent(self):
        import help_view
        commands = help_view.text()
        self.assertIn('gro (Grok Build)', commands)
        self.assertIn('/cli spawn <agent>', commands)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_defaults_are_grok_47_high_allow(self):
        self.assertEqual(grok_build.DEFAULTS, dict(model='grok-4.7', effort='high', access='allow'))
        chosen = grok_build.selection(self.root, 'grok-4.7', 'allow', 'high')
        self.assertEqual(chosen['modelName'], 'Grok 4.7')
        self.assertEqual(chosen['effort'], 'High')
        self.assertEqual(chosen['accessName'], 'Always approve')

    def test_effort_uses_the_reasoning_effort_key(self):
        chosen = grok_build.selection(self.root, 'grok-4.7', 'allow', 'xhigh')
        self.assertEqual(chosen['effortKey'], 'reasoning_effort')
        self.assertEqual(chosen['effortValue'], 'xhigh')
        self.assertEqual(chosen['effort'], 'Extra High')
        self.assertEqual(grok_build.setting_steps(chosen), [
            ['set', 'model', 'grok-4.7'],
            ['set', 'reasoning_effort', 'xhigh']])

    def test_no_native_mode_is_ever_applied(self):
        chosen = grok_build.selection(self.root, 'grok-4.7', 'allow', 'high')
        self.assertIsNone(chosen['mode'])
        self.assertIsNone(chosen['modeKey'])
        self.assertEqual(chosen['accessEnforcedBy'], 'acpx')
        # No settings step touches a permission mode.
        self.assertTrue(all(step[1] in ('model', 'reasoning_effort')
                            for step in grok_build.setting_steps(chosen)))

    def test_auto_edit_is_refused_with_a_reason_not_substituted(self):
        with self.assertRaisesRegex(ValueError, 'no permission-mode selector'):
            grok_build.selection(self.root, 'grok-4.7', 'auto-edit', 'high')

    def test_prompt_access_is_supported(self):
        chosen = grok_build.selection(self.root, 'grok-4.6', 'prompt', 'low')
        self.assertEqual(chosen['accessName'], 'Prompt')

    def test_unadvertised_choices_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Model is not unambiguously advertised'):
            grok_build.selection(self.root, 'grok-5', 'allow', 'high')
        with self.assertRaisesRegex(ValueError, 'Effort is not unambiguously advertised'):
            grok_build.selection(self.root, 'grok-4.7', 'allow', 'max')

    def test_catalog_has_no_ambiguous_default_row(self):
        data = grok_build.catalog(self.root)
        self.assertNotIn('default', {family['modelId'] for family in data['modelFamilies']})
        for family in data['modelFamilies']:
            self.assertNotIn('default', {item['value'] for item in family['efforts']})


class Verification(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.settings = grok_build.selection(self.root, 'grok-4.7', 'allow', 'high')
        self.owned = dict(name='s', workspace=str(self.root), settings=self.settings)

    def record(self, model='grok-4.7', effort='high'):
        return {'acpxRecordId': 'record-9', 'acpSessionId': 'record-9',
                'acpx': {'current_model_id': model,
                         'config_options': [{'id': 'model', 'currentValue': model},
                                            {'id': 'reasoning_effort', 'currentValue': effort}]}}

    def test_verify_checks_model_and_effort_only(self):
        backend = grok_build.Backend()
        with patch.object(backend, 'metadata', return_value=self.record()):
            self.assertEqual(backend.verify(self.owned), 'record-9')
        for wrong in (dict(model='grok-4.6'), dict(effort='low')):
            with patch.object(backend, 'metadata', return_value=self.record(**wrong)):
                with self.assertRaisesRegex(RuntimeError, 'did not accept'):
                    backend.verify(self.owned)

    def test_verify_does_not_require_a_mode_option(self):
        backend = grok_build.Backend()
        bare = self.record()
        self.assertNotIn('mode', {c['id'] for c in bare['acpx']['config_options']})
        with patch.object(backend, 'metadata', return_value=bare):
            self.assertEqual(backend.verify(self.owned), 'record-9')

    def test_argv_uses_the_grok_build_profile(self):
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = grok_build.Backend().command(self.owned, ['status'])
        self.assertIn('grok-build', command)
        self.assertNotIn('antigravity', command)
        self.assertNotIn('claude', command)
        self.assertIn('--approve-all', command)
        self.assertEqual(command[command.index('--ttl') + 1], '3600')  # One hour unless /cli timeout changes it.

    def test_prompt_access_drops_host_auto_approval(self):
        owned = dict(self.owned, settings=grok_build.selection(self.root, 'grok-4.7', 'prompt', 'high'))
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = grok_build.Backend().command(owned, ['status'])
        self.assertNotIn('--approve-all', command)
        self.assertIn('--non-interactive-permissions', command)


class Relay(unittest.TestCase):
    def test_shares_the_single_translation_path(self):
        self.assertIs(grok_build.public_event, acpx.public_event)
        self.assertIs(grok_build.PublicRelay, acpx.PublicRelay)

    def test_private_events_stay_excluded(self):
        for private in ('agent_thought_chunk', 'tool_call', 'tool_call_update'):
            self.assertIsNone(grok_build.public_event(
                {'params': {'update': {'sessionUpdate': private,
                                       'content': {'type': 'text', 'text': 'PRIVATE'}}}}))
        self.assertEqual(grok_build.public_event(
            {'params': {'update': {'sessionUpdate': 'agent_message_chunk',
                                   'content': {'type': 'text', 'text': 'hi'}}}}),
            {'type': 'message', 'text': 'hi'})

    def test_no_transport_switch(self):
        self.assertFalse(grok_build.NATIVE_HANDOFF)


class Isolation(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = Path(tempfile.mkdtemp())
        self.store = Store('grok-thread', str(self.workspace), self.root)

    def control(self):
        return Controller(self.store, FakeBackend())

    def configured(self):
        """Prerequisites confirmed and this plugin's hook observed this turn."""
        import time
        frontends.check_and_save(self.root, 'grok-build', check=lambda agent: dict(confirmed=True, checks=[]))
        with self.store.edit() as state:
            state['hookSeen'] = dict(event='UserPromptSubmit', time=time.time(),
                                     plugin=str(PLUGIN), data=str(self.root))

    def test_saved_backend_selects_its_own_adapter(self):
        with self.store.edit() as state:
            state['backend'] = 'grok-build'
        self.assertIs(self.control().adapter, grok_build)

    def test_receipts_are_per_backend(self):
        frontends.check_and_save(self.root, 'grok-build', check=lambda agent: dict(confirmed=True, checks=[]))
        self.assertTrue(frontends.confirmed(self.root, 'grok-build'))
        self.assertFalse(frontends.confirmed(self.root, 'claude'))
        self.assertFalse(frontends.confirmed(self.root, 'agy'))

    def test_another_backends_settings_never_preload_this_menu(self):
        # Accepted settings belong to the backend that accepted them. Showing
        # them here would offer a model and access this runtime never advertised.
        import claude_code
        self.configured()
        control = self.control()
        accepted = claude_code.selection(self.root, 'opus', 'allow', 'high')
        with self.store.edit() as state:
            state['backend'] = 'claude'
            state['settings'] = accepted
        menu = control.frontend('grok-build')['activationMenu']
        self.assertIn('Grok Build CLI', menu)
        self.assertIn('Grok 4.7', menu)
        self.assertNotIn('Opus 5', menu)
        self.assertNotIn('Bypass permissions', menu)

    def test_own_saved_settings_still_preload(self):
        self.configured()
        control = self.control()
        accepted = grok_build.selection(self.root, 'grok-4.6', 'prompt', 'low')
        with self.store.edit() as state:
            state['backend'] = 'grok-build'
            state['settings'] = accepted
        menu = control.frontend('grok-build')['activationMenu']
        self.assertIn('Grok 4.6', menu)
        self.assertIn('Prompt', menu)

    def test_another_backends_session_blocks_activation(self):
        control = self.control()
        control.frontend('grok-build')
        with self.store.edit() as state:
            state['main'] = 'existing'
            state['owned'] = [dict(name='existing', backend='claude', role='main',
                                   ready=True, settings={}, workspace=str(self.workspace))]
        with self.assertRaisesRegex(RuntimeError, 'Run off successfully'):
            control.activate('grok-4.7', 'allow', effort='high', agent='grok-build')


class Menus(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def confirm_all(self):
        for backend in ('agy', 'claude', 'grok-build'):
            frontends.check_and_save(self.root, backend, check=lambda agent: dict(confirmed=True, checks=[]))

    def test_agent_menu_lists_three_agents(self):
        self.confirm_all()
        menu = frontends.menu(self.root, 'home', None, {'ready': True}, {'ready': True})
        self.assertIn('1. Antigravity CLI', menu)
        self.assertIn('2. Claude Code CLI', menu)
        self.assertIn('3. Grok Build CLI', menu)

    def test_activation_menu_shows_verified_defaults(self):
        self.confirm_all()
        settings = grok_build.selection(self.root, 'grok-4.7', 'allow', 'high')
        menu = frontends.menu(self.root, 'grok-build', settings, {'ready': True}, {'ready': True})
        self.assertIn('Grok Build CLI', menu)
        self.assertIn('Grok 4.7', menu)
        self.assertIn('Always approve', menu)
        self.assertIn('X. Exit', menu)

    def test_probe_list_is_backend_specific(self):
        tools = frontends.BACKEND_TOOLS['grok-build']
        self.assertIn(('Grok Build CLI', 'grok'), tools)
        self.assertNotIn(('Antigravity CLI', 'agy'), tools)
        self.assertNotIn(('Claude Code CLI', 'claude'), tools)


class Documentation(unittest.TestCase):
    def test_backend_documents_the_missing_permission_mode(self):
        guide = (PLUGIN / 'backends/grok-build/backend.md').read_text(encoding='utf-8')
        self.assertIn('no permission-mode selector', guide)
        self.assertIn('auto-edit', guide)

    def test_confirmation_declares_utilization_unavailable(self):
        guide = (PLUGIN / 'backends/grok-build/references/confirmation.md').read_text(encoding='utf-8')
        self.assertIn('unavailable', guide)
        # Per-session token/cost must never be presented as subscription quota,
        # and `usage` as a prompt must never be used to obtain it.
        self.assertIn('no utilization', guide)
        self.assertIn('billed as ordinary model turns', guide)
        self.assertIn('TUI modal', guide)


if __name__ == '__main__':
    unittest.main()
