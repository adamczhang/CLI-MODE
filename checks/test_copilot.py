"""GitHub Copilot backend: no model or effort catalog, a real native permission control."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, PLUGIN
import acpx
import adapters
import copilot_cli
import frontends
from controller import Controller
from state import Store, route, backend_ids

AGENT_MODE = 'https://agentclientprotocol.com/protocol/session-modes#agent'


class Registry(unittest.TestCase):
    def test_registered_and_implemented(self):
        self.assertIn('copilot', backend_ids())
        self.assertIn('copilot', adapters.implemented())
        self.assertIn('GitHub Copilot CLI', [i['displayName'] for i in frontends.backends()])

    def test_profile_matches_the_acpx_builtin(self):
        record = next(i for i in frontends.backends() if i['id'] == 'copilot')
        self.assertEqual(record['acpxProfile'], 'copilot')
        self.assertEqual(copilot_cli.Backend.profile, 'copilot')

    def test_guides_exist(self):
        base = PLUGIN / 'backends/copilot'
        for name in ('backend.md', 'references/acpx.md', 'references/confirmation.md'):
            self.assertTrue((base / name).exists(), name)


class Controls(unittest.TestCase):
    def test_controls_mirror_the_other_agents(self):
        off = {'active': False}
        for prefix in ('/', '$'):
            self.assertEqual(route(prefix + 'cli copilot', off),
                             {'route': 'frontend', 'agent': 'copilot'})
            self.assertEqual(route(prefix + 'cli bind copilot', off),
                             {'route': 'bind', 'agent': 'copilot'})
        self.assertEqual(route('/CLI COPILOT', off), {'route': 'frontend', 'agent': 'copilot'})

    def test_near_miss_names_are_not_controls(self):
        for text in ('/cli copilo', '/cli github', '/cli bind copilo'):
            self.assertEqual(route(text, {'active': False})['route'], 'hint', text)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.data = copilot_cli.catalog(self.root)

    def test_defaults_are_provider_default_with_allow_all(self):
        self.assertEqual(copilot_cli.DEFAULTS, dict(model='provider-default', access='allow'))
        chosen = copilot_cli.selection(self.root, 'provider-default', 'allow')
        self.assertEqual(chosen['modelName'], 'Provider default')
        self.assertEqual(chosen['effort'], 'Provider default')
        self.assertEqual(chosen['accessName'], 'Allow all')

    def test_no_model_or_effort_catalog_is_advertised(self):
        self.assertIsNone(self.data['modelControl'])
        self.assertIsNone(self.data['effortControl'])
        self.assertEqual(self.data['models'], [])
        self.assertEqual(frontends.phase_options(self.root, 'copilot', 'model'),
                         [('Provider default', 'provider-default')])
        self.assertEqual(frontends.phase_options(self.root, 'copilot', 'effort'),
                         [('Provider default', None)])

    def test_an_invented_model_or_effort_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'no model catalog'):
            copilot_cli.selection(self.root, 'gpt-5.4', 'allow')
        with self.assertRaisesRegex(ValueError, 'no reasoning effort control'):
            copilot_cli.selection(self.root, 'provider-default', 'allow', 'high')

    def test_access_maps_to_the_native_allow_all_control(self):
        self.assertEqual(self.data['accessControl']['key'], 'allow_all')
        allow = copilot_cli.selection(self.root, 'provider-default', 'allow')
        prompt = copilot_cli.selection(self.root, 'provider-default', 'prompt')
        self.assertEqual((allow['mode'], allow['modeKey']), ('on', 'allow_all'))
        self.assertEqual((prompt['mode'], prompt['modeKey']), ('off', 'allow_all'))

    def test_auto_edit_is_refused_with_a_reason(self):
        with self.assertRaisesRegex(ValueError, 'no\n?\\s*auto-approve-edits-only value'):
            copilot_cli.selection(self.root, 'provider-default', 'auto-edit')

    def test_interaction_mode_is_pinned_to_agent_and_uses_opaque_uris(self):
        chosen = copilot_cli.selection(self.root, 'provider-default', 'allow')
        self.assertEqual(chosen['interactionValue'], AGENT_MODE)
        self.assertEqual(chosen['interactionKey'], 'mode')
        unmapped = {o['nativeName'] for o in self.data['interactionMode']['advertisedUnmapped']}
        self.assertEqual(unmapped, {'Plan', 'Autopilot'})
        for item in self.data['interactionMode']['advertisedUnmapped']:
            self.assertTrue(item['nativeValue'].startswith('https://'), item)

    def test_setting_steps_set_permission_and_mode_but_never_a_model(self):
        chosen = copilot_cli.selection(self.root, 'provider-default', 'allow')
        steps = copilot_cli.setting_steps(chosen)
        self.assertEqual(steps, [['set', 'allow_all', 'on'], ['set', 'mode', AGENT_MODE]])
        self.assertFalse(any(step[1] == 'model' for step in steps))


class Verification(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.settings = copilot_cli.selection(self.root, 'provider-default', 'allow')
        self.owned = dict(name='s', workspace=str(self.root), settings=self.settings)

    def record(self, allow_all='on', mode=AGENT_MODE):
        return {'acpxRecordId': 'record-p', 'acpSessionId': 'rotated',
                'acpx': {'config_options': [{'id': 'allow_all', 'currentValue': allow_all},
                                            {'id': 'mode', 'currentValue': mode}]}}

    def test_identity_tracks_the_provider_conversation(self):
        self.assertEqual(copilot_cli.provider_identity(self.record()), 'rotated')

    def test_verify_checks_permission_and_mode(self):
        backend = copilot_cli.Backend()
        with patch.object(backend, 'metadata', return_value=self.record()):
            self.assertEqual(backend.verify(self.owned), 'rotated')
        for wrong in (dict(allow_all='off'), dict(mode=AGENT_MODE.replace('agent', 'plan'))):
            with patch.object(backend, 'metadata', return_value=self.record(**wrong)):
                with self.assertRaisesRegex(RuntimeError, 'did not accept'):
                    backend.verify(self.owned)

    def test_verify_does_not_require_a_model(self):
        # No model was requested, so none is verified and none may be claimed.
        backend = copilot_cli.Backend()
        bare = self.record()
        self.assertNotIn('model', {c['id'] for c in bare['acpx']['config_options']})
        with patch.object(backend, 'metadata', return_value=bare):
            self.assertEqual(backend.verify(self.owned), 'rotated')

    def test_argv_uses_the_copilot_profile(self):
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = copilot_cli.Backend().command(self.owned, ['status'])
        self.assertIn('copilot', command)
        for other in ('antigravity', 'claude', 'grok-build', 'cursor'):
            self.assertNotIn(other, command)
        self.assertIn('--approve-all', command)

    def test_prompt_access_drops_host_auto_approval_and_sets_allow_all_off(self):
        settings = copilot_cli.selection(self.root, 'provider-default', 'prompt')
        owned = dict(self.owned, settings=settings)
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = copilot_cli.Backend().command(owned, ['status'])
        self.assertNotIn('--approve-all', command)
        self.assertEqual(copilot_cli.setting_steps(settings)[0], ['set', 'allow_all', 'off'])


class Menus(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for backend in backend_ids():
            frontends.check_and_save(self.root, backend,
                                     check=lambda agent: dict(confirmed=True, checks=[]))

    def test_home_menu_lists_copilot(self):
        ready = {'ready': True}
        self.assertIn('GitHub Copilot CLI', frontends.menu(self.root, 'home', None, ready, ready))

    def test_single_choice_phases_render_one_provider_default_row(self):
        for phase in ('model', 'effort'):
            built = frontends.phase_menu(self.root, 'copilot', phase, None, 1)
            self.assertEqual(built['pages'], 1)
            self.assertIn('1. Provider default', built['text'])

    def test_access_phase_offers_the_two_advertised_choices(self):
        built = frontends.phase_menu(self.root, 'copilot', 'access', None, 1)
        self.assertIn('Allow (Allow all)', built['text'])
        self.assertIn('Prompt \u2014 approval requests stop the turn', built['text'])
        self.assertNotIn('auto-edit', built['text'])

    def test_activation_menu_shows_verified_defaults(self):
        settings = copilot_cli.selection(self.root, 'provider-default', 'allow')
        menu = frontends.menu(self.root, 'copilot', settings, {'ready': True}, {'ready': True})
        self.assertIn('GitHub Copilot CLI', menu)
        self.assertIn('Provider default', menu)
        self.assertIn('Allow all', menu)

    def test_probe_list_is_backend_specific(self):
        self.assertIn(('GitHub Copilot CLI', 'copilot'), frontends.BACKEND_TOOLS['copilot'])
        self.assertNotIn(('Cursor CLI', 'cursor-agent'), frontends.BACKEND_TOOLS['copilot'])


class Isolation(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = Path(tempfile.mkdtemp())
        self.store = Store('copilot-thread', str(self.workspace), self.root)

    def test_saved_backend_selects_its_own_adapter(self):
        with self.store.edit() as state:
            state['backend'] = 'copilot'
        self.assertIs(Controller(self.store, FakeBackend()).adapter, copilot_cli)

    def test_another_backends_session_blocks_activation(self):
        control = Controller(self.store, FakeBackend())
        control.frontend('copilot')
        with self.store.edit() as state:
            state['main'] = 'existing'
            state['owned'] = [dict(name='existing', backend='cursor', role='main',
                                   ready=True, settings={}, workspace=str(self.workspace))]
        with self.assertRaisesRegex(RuntimeError, 'Run off successfully'):
            control.activate('provider-default', 'allow', agent='copilot')


class Documentation(unittest.TestCase):
    def test_backend_documents_the_absent_catalog_and_real_permission(self):
        guide = (PLUGIN / 'backends/copilot/backend.md').read_text(encoding='utf-8')
        self.assertIn('no model catalog', guide)
        self.assertIn('allow_all', guide)

    def test_confirmation_forbids_naming_a_model(self):
        guide = (PLUGIN / 'backends/copilot/references/confirmation.md').read_text(encoding='utf-8')
        self.assertIn('Never name a specific Copilot model', guide)
        self.assertIn('no subscription quota command', guide)


if __name__ == '__main__':
    unittest.main()
