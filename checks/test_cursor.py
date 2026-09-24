"""Cursor backend: opaque bracketed model IDs, no effort, pinned interaction mode."""
import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, PLUGIN
import acpx
import adapters
import cursor_agent
import frontends
from controller import Controller
from presentation import MENU_MAX_OPTIONS
from state import Store, route, backend_ids


class Registry(unittest.TestCase):
    def test_registered_and_implemented(self):
        self.assertIn('cursor', backend_ids())
        self.assertIn('cursor', adapters.implemented())
        self.assertIn('Cursor CLI', [i['displayName'] for i in frontends.backends()])

    def test_profile_matches_the_acpx_builtin(self):
        record = next(i for i in frontends.backends() if i['id'] == 'cursor')
        self.assertEqual(record['acpxProfile'], 'cursor')
        self.assertEqual(cursor_agent.Backend.profile, 'cursor')

    def test_guides_exist(self):
        base = PLUGIN / 'backends/cursor'
        for name in ('backend.md', 'references/acpx.md', 'references/confirmation.md'):
            self.assertTrue((base / name).exists(), name)


class Controls(unittest.TestCase):
    def test_controls_mirror_the_other_agents(self):
        off = {'active': False}
        for prefix in ('/', '$'):
            self.assertEqual(route(prefix + 'cli cursor', off),
                             {'route': 'frontend', 'agent': 'cursor'})
            self.assertEqual(route(prefix + 'cli bind cursor', off),
                             {'route': 'bind', 'agent': 'cursor'})
        self.assertEqual(route('/CLI CURSOR', off), {'route': 'frontend', 'agent': 'cursor'})

    def test_near_miss_names_are_not_controls(self):
        for text in ('/cli curso', '/cli cursor-agent', '/cli bind curso'):
            self.assertEqual(route(text, {'active': False})['route'], 'hint', text)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.data = cursor_agent.catalog(self.root)

    def test_default_is_advertised_composer_with_provider_effort(self):
        self.assertEqual(cursor_agent.DEFAULTS,
                         dict(model='composer-2.5[fast=true]', access='allow'))
        self.assertEqual(self.data['requestedDefaultModelId'], cursor_agent.DEFAULTS['model'])
        chosen = cursor_agent.selection(self.root, **cursor_agent.DEFAULTS)
        self.assertEqual(chosen['modelName'], 'composer-2.5')
        self.assertEqual(chosen['effort'], 'Provider default')
        self.assertEqual(chosen['accessName'], 'Always approve')
        self.assertEqual(cursor_agent.setting_steps(chosen),
                         [['set', 'model', 'composer-2.5[fast=true]'], ['set', 'mode', 'agent']])
        self.assertIn('Model: composer-2.5\nEffort: Provider default',
                      frontends.settings_text('cursor', chosen))

    def test_catalog_is_long_enough_to_require_paging(self):
        self.assertGreater(len(self.data['modelFamilies']), MENU_MAX_OPTIONS)

    def test_model_values_are_opaque_advertised_ids(self):
        values = {f['modelId'] for f in self.data['modelFamilies']}
        self.assertIn('composer-2.5[fast=true]', values)
        self.assertIn('default[]', values)
        chosen = cursor_agent.selection(self.root, 'composer-2.5[fast=true]', 'allow')
        self.assertEqual(chosen['model'], 'composer-2.5[fast=true]')
        self.assertEqual(chosen['modelName'], 'composer-2.5')

    def test_a_bare_name_with_a_bracketed_variant_is_refused(self):
        # ACPX rejects an ambiguous bare name, so CLI-MODE never reconstructs one.
        with self.assertRaisesRegex(ValueError, 'full advertised ID'):
            cursor_agent.selection(self.root, 'composer-2.5', 'allow')

    def test_no_effort_control_is_advertised_or_invented(self):
        self.assertIsNone(self.data.get('effortControl'))
        for family in self.data['modelFamilies']:
            self.assertEqual(family['efforts'], [])
        chosen = cursor_agent.selection(self.root, 'default[]', 'allow')
        self.assertIsNone(chosen['effortValue'])
        self.assertIsNone(chosen['effortKey'])
        with self.assertRaisesRegex(ValueError, 'no reasoning effort control'):
            cursor_agent.selection(self.root, 'default[]', 'allow', 'high')

    def test_effort_phase_offers_one_explicit_provider_default(self):
        self.assertEqual(frontends.phase_options(self.root, 'cursor', 'effort'),
                         [('Provider default', None)])

    def test_auto_edit_is_refused_with_a_reason(self):
        with self.assertRaisesRegex(ValueError, 'no permission-mode selector'):
            cursor_agent.selection(self.root, 'default[]', 'auto-edit')

    def test_interaction_mode_is_pinned_to_agent_not_offered_as_access(self):
        chosen = cursor_agent.selection(self.root, 'default[]', 'allow')
        self.assertEqual(chosen['mode'], 'agent')
        self.assertEqual(chosen['modeKey'], 'mode')
        offered = {o['access'] for o in self.data['accessControl']['options']}
        self.assertEqual(offered, {'allow'})
        unmapped = {o['nativeValue'] for o in self.data['interactionMode']['advertisedUnmapped']}
        self.assertEqual(unmapped, {'plan', 'ask'})

    def test_setting_steps_apply_model_and_pinned_mode_only(self):
        chosen = cursor_agent.selection(self.root, 'default[]', 'allow')
        self.assertEqual(cursor_agent.setting_steps(chosen),
                         [['set', 'model', 'default[]'], ['set', 'mode', 'agent']])


class Verification(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.settings = cursor_agent.selection(self.root, 'default[]', 'allow')
        self.owned = dict(name='s', workspace=str(self.root), settings=self.settings)

    def record(self, model='default[]', mode='agent'):
        return {'acpxRecordId': 'record-c', 'acpSessionId': 'rotated',
                'acpx': {'current_model_id': model,
                         'config_options': [{'id': 'model', 'currentValue': model},
                                            {'id': 'mode', 'currentValue': mode}]}}

    def test_identity_tracks_the_provider_conversation(self):
        self.assertEqual(cursor_agent.provider_identity(self.record()), 'rotated')

    def test_verify_checks_model_and_pinned_mode(self):
        backend = cursor_agent.Backend()
        with patch.object(backend, 'metadata', return_value=self.record()):
            self.assertEqual(backend.verify(self.owned), 'rotated')
        for wrong in (dict(model='composer-2.5[fast=true]'), dict(mode='plan')):
            with patch.object(backend, 'metadata', return_value=self.record(**wrong)):
                with self.assertRaisesRegex(RuntimeError, 'did not accept'):
                    backend.verify(self.owned)

    def test_argv_uses_the_cursor_profile(self):
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = cursor_agent.Backend().command(self.owned, ['status'])
        self.assertIn('cursor', command)
        for other in ('antigravity', 'claude', 'grok-build'):
            self.assertNotIn(other, command)
        self.assertIn('--approve-all', command)

    def test_prompt_access_is_rejected_including_stale_catalog_and_session(self):
        data = cursor_agent.catalog(self.root)
        data['accessControl']['options'].append(dict(access='prompt', nativeName='Prompt', nativeValue=None))
        cached = self.root / 'catalogs/cursor.json'
        cached.parent.mkdir()
        cached.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'cannot be enforced'):
            cursor_agent.selection(self.root, 'default[]', 'prompt')
        self.assertEqual([value for _, value in frontends.phase_options(self.root, 'cursor', 'access')], ['allow'])
        owned = dict(self.owned, settings=dict(self.settings, access='prompt'))
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            with self.assertRaisesRegex(RuntimeError, 'cannot be enforced'):
                cursor_agent.Backend().command(owned, ['--file', 'prompt.txt'])
            self.assertIn('status', cursor_agent.Backend().command(owned, ['status']))


class Menus(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for backend in backend_ids():
            frontends.check_and_save(self.root, backend,
                                     check=lambda agent: dict(confirmed=True, checks=[]))

    def test_home_menu_lists_cursor(self):
        ready = {'ready': True}
        self.assertIn('Cursor CLI', frontends.menu(self.root, 'home', None, ready, ready))

    def test_the_long_model_list_paginates(self):
        first = frontends.phase_menu(self.root, 'cursor', 'model', None, 1)
        self.assertGreater(first['pages'], 1)
        self.assertIn('> Next page (2 of %d)' % first['pages'], first['text'])
        self.assertNotIn('< Previous page', first['text'])
        last = frontends.phase_menu(self.root, 'cursor', 'model', None, first['pages'])
        self.assertIn('< Previous page', last['text'])
        self.assertNotIn('> Next page', last['text'])

    def test_every_model_appears_exactly_once_across_pages(self):
        names, page = [], 1
        while True:
            built = frontends.phase_menu(self.root, 'cursor', 'model', None, page)
            names += [r.split('. ', 1)[1] for r in built['text'].splitlines() if r[:1].isdigit()]
            if page >= built['pages']:
                break
            page += 1
        expected = [f['name'] for f in cursor_agent.catalog(self.root)['modelFamilies']]
        self.assertEqual(names, expected)

    def test_activation_menu_shows_verified_defaults(self):
        settings = cursor_agent.selection(self.root, 'default[]', 'allow')
        menu = frontends.menu(self.root, 'cursor', settings, {'ready': True}, {'ready': True})
        self.assertIn('Cursor CLI', menu)
        self.assertIn('Auto', menu)
        self.assertIn('Provider default', menu)
        self.assertIn('Always approve', menu)

    def test_probe_list_is_backend_specific(self):
        self.assertIn(('Cursor CLI', 'cursor-agent'), frontends.BACKEND_TOOLS['cursor'])
        self.assertNotIn(('Grok Build CLI', 'grok'), frontends.BACKEND_TOOLS['cursor'])


class Isolation(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = Path(tempfile.mkdtemp())
        self.store = Store('cursor-thread', str(self.workspace), self.root)

    def test_saved_backend_selects_its_own_adapter(self):
        with self.store.edit() as state:
            state['backend'] = 'cursor'
        self.assertIs(Controller(self.store, FakeBackend()).adapter, cursor_agent)

    def test_another_backends_session_blocks_activation(self):
        control = Controller(self.store, FakeBackend())
        control.frontend('cursor')
        with self.store.edit() as state:
            state['main'] = 'existing'
            state['owned'] = [dict(name='existing', backend='grok-build', role='main',
                                   ready=True, settings={}, workspace=str(self.workspace))]
        with self.assertRaisesRegex(RuntimeError, 'Run off successfully'):
            control.activate('default[]', 'allow', agent='cursor')


class Documentation(unittest.TestCase):
    def test_backend_documents_the_opaque_ids_and_missing_selectors(self):
        guide = (PLUGIN / 'backends/cursor/backend.md').read_text(encoding='utf-8')
        self.assertIn('opaque', guide)
        self.assertIn('no permission-mode selector', guide)
        self.assertIn('paginate', guide)

    def test_confirmation_declares_utilization_unavailable(self):
        guide = (PLUGIN / 'backends/cursor/references/confirmation.md').read_text(encoding='utf-8')
        self.assertIn('no subscription quota command', guide)


if __name__ == '__main__':
    unittest.main()
