"""Behavioral coverage for audit fixes, transactions and provider diversity."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from test_controller import Controller, FakeBackend, Store, PLUGIN
import adapters
import catalogs
import frontends
import native_commands
from state import backend_ids, backend_records, route


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store('audit', self.root, self.root / 'state')
        self.backend = FakeBackend()
        self.c = Controller(self.store, self.backend)

    def activate(self, agent='codex'):
        self.c.frontend(agent)
        self.c.activate(agent=agent, **adapters.module(agent).DEFAULTS)

    def test_draft_model_drives_effort_choices_and_numbered_application(self):
        self.activate()
        self.c.tune('model')
        menu = self.c.options('model')
        number = next(i for i, item in enumerate(menu['choices'], 1) if item['value'] == 'gpt-5.6-luna')
        effort = self.c.choose(number)
        self.assertNotIn('ultra', [item['value'] for item in effort['choices']])
        self.c.choose(2)
        self.c.choose(1)
        self.assertEqual(self.store.read()['settings']['model'], 'gpt-5.6-luna')
        self.assertEqual(self.store.read()['settings']['effortValue'], 'medium')

    def test_snapshot_survives_catalog_reordering(self):
        self.activate()
        self.c.tune('model')
        original = self.c.options('model')['choices']
        with patch.object(adapters.module('codex'), 'catalog', side_effect=AssertionError('must use snapshot')):
            self.assertEqual(self.c.options('model')['choices'], original)

    def test_opening_settings_during_preflight_blocks_send(self):
        self.activate()
        metadata = self.backend.metadata
        def racing_metadata(owned):
            self.c.settings_menu()
            return metadata(owned)
        self.backend.metadata = racing_metadata
        before = len(self.backend.calls)
        with self.assertRaisesRegex(RuntimeError, 'Settle current work'):
            self.c.send('/d must not dispatch')
        self.assertEqual(len(self.backend.calls), before)

    def test_empty_command_update_clears_all_acp_adapters(self):
        for agent in backend_ids():
            if adapters.module(agent).NATIVE_HANDOFF:
                continue
            backend = adapters.module(agent).Backend()
            record = {'acpx': {'available_commands': []}}
            facts = {'advertisedCommands': native_commands.from_record(record)}
            self.assertEqual(facts, {'advertisedCommands': []}, agent)
            with self.assertRaises(RuntimeError):
                backend.validate_command(facts, '/previously-advertised')

    def test_antigravity_current_markers_across_all_variants(self):
        adapter = adapters.module('agy')
        for family in adapter.catalog(self.root)['modelFamilies']:
            for effort in family['efforts']:
                settings = adapter.selection(self.root, effort['modelId'], 'allow')
                for phase in ('model', 'effort', 'access'):
                    self.assertEqual(frontends.phase_menu(self.root, 'agy', phase, settings)['menu'].count('(current)'), 1)

    def test_agent_lists_paginate_for_existing_and_new_users(self):
        records = [{'id': str(i), 'displayName': 'Agent ' + str(i)} for i in range(12)]
        for confirmed in (True, False):
            with patch.object(frontends, 'backends', return_value=records), patch.object(frontends, 'confirmed', return_value=confirmed):
                first = frontends.menu(self.root, 'home', page=1)
                second = frontends.menu(self.root, 'home', page=2)
                self.assertNotEqual(first, second)
                for menu in (first, second):
                    rows = [line for line in menu.splitlines() if re.match(r'\| (\d+\.|[<>X]\.)', line)]
                    self.assertLessEqual(len(rows), 10)
                self.assertIn('12. Agent 11', second)

    def test_one_configured_agent_is_not_a_new_user(self):
        with patch.object(frontends, 'confirmed', side_effect=lambda root, agent: agent == 'agy'):
            self.assertFalse(frontends.first_start(self.root, 'home'))
            menu = frontends.menu(self.root, 'home')
            self.assertIn('Select CLI Agent', menu)
            self.assertIn('Setup needed', menu)

    def test_tuning_back_and_close_preserve_session(self):
        self.activate()
        before = self.store.read()['main']
        self.c.tune('effort')
        menu = self.c.options('effort')
        self.assertIn('X. Close settings', menu['activationMenu'])
        self.assertEqual(route('B', self.store.read())['route'], 'navigate')
        self.c.navigate('b')
        self.assertEqual(self.store.read()['pending']['phase'], 'settings')
        self.c.tune('access')
        self.assertEqual(route('X', self.store.read())['route'], 'settings-dismiss')
        self.c.settings_menu(dismiss=True)
        self.assertEqual(self.store.read()['main'], before)
        self.assertTrue(self.store.read()['active'])

    def test_initial_setup_back_uses_previous_phase(self):
        self.c.frontend('codex')
        self.c.options('access')
        self.c.navigate('b')
        self.assertEqual(self.store.read()['pending']['phase'], 'effort')

    def test_registry_contract_and_diagnostics_cover_every_backend(self):
        import doctor
        for backend in backend_ids():
            adapters.descriptor(backend)
        records = backend_records()
        records[0]['id'] = 'mutated'
        self.assertEqual(backend_records()[0]['id'], 'agy')
        self.assertEqual(set(doctor.inspect(self.root)['backends']), set(backend_ids()))
        installer = (PLUGIN / 'scripts/setup.ps1').read_text()
        for backend in backend_ids():
            self.assertIn("'" + backend + "'", installer)

    def test_structured_settings_do_not_parse_adapter_menu_prose(self):
        for agent in backend_ids():
            # Settings pages are built from structured fields for every agent.
            self.assertFalse(hasattr(adapters.module(agent), 'activation_menu'), agent)
            menu = frontends.active_settings_menu(agent, None, 'direct')
            for label in ('Model:', 'Effort:', 'Access:', 'Mode: Direct'):
                self.assertIn(label, menu)

    def test_refresh_without_owned_session_is_explicit_and_does_not_launch(self):
        self.c.frontend('codex')
        self.c.options('model')
        result = self.c.refresh()
        self.assertFalse(result['refreshed'])
        self.assertEqual(result['catalogStatus'], 'cached')
        self.assertFalse(self.backend.calls)

    def test_refresh_never_copies_current_efforts_to_another_model(self):
        data = adapters.module('codex').catalog(self.root)
        record = {'acpx': {'available_models': ['gpt-6-astra', 'gpt-5.6-luna'],
                           'current_model_id': 'gpt-6-astra', 'config_options': [
                               {'id': 'reasoning_effort', 'options': [{'value': 'ultra', 'name': 'Ultra'}]},
                               {'id': 'mode', 'options': [{'value': 'agent-full-access', 'name': 'Full access'}]}]}}
        refreshed = catalogs.from_metadata('codex', data, record)
        luna = next(f for f in refreshed['modelFamilies'] if f['modelId'] == 'gpt-5.6-luna')
        self.assertNotIn('ultra', [e['value'] for e in luna['efforts']])
        self.assertEqual(luna['effortSource'], 'cached-model-specific')

    def test_failed_refresh_retains_existing_snapshot(self):
        self.activate()
        self.c.tune('model')
        before = self.c.options('model')['choices']
        result = self.c.refresh()  # Fake backend has no model catalog.
        self.assertFalse(result['refreshed'])
        self.assertEqual(result['choices'], before)
        self.assertFalse((self.store.root / 'catalogs/codex.json').exists())

    def test_refresh_metadata_conversion_covers_all_six_capability_shapes(self):
        for agent in backend_ids():
            data = adapters.module(agent).catalog(self.root)
            controls = []
            access = data['accessControl']
            if access.get('key'):
                controls.append({'id': access['key'], 'options': [
                    {'value': x['nativeValue'], 'name': x['nativeName']} for x in access['options']]})
            family = data['modelFamilies'][0]
            ids = [f['modelId'] for f in data['modelFamilies']] if 'modelId' in family else [
                e['modelId'] for f in data['modelFamilies'] for e in f['efforts']]
            names = {model: model for model in ids}
            if agent == 'agy':
                names = {e['modelId']: f['name'] + ' (' + e['name'] + ')'
                         for f in data['modelFamilies'] for e in f['efforts']}
            if adapters.descriptor(agent)['effortRepresentation'] == 'separate':
                controls.append({'id': data['effortControl']['key'], 'options': family['efforts']})
            record = {'acpx': {'available_models': ids if agent != 'copilot' else None,
                               'available_model_names': names, 'current_model_id': ids[0],
                               'config_options': controls}}
            refreshed = catalogs.from_metadata(agent, data, record)
            self.assertEqual(len(refreshed['modelFamilies']), len(data['modelFamilies']), agent)
            self.assertEqual(refreshed['accessControl']['options'], data['accessControl']['options'], agent)

    def test_stale_choice_cannot_overwrite_a_new_menu(self):
        self.activate()
        self.c.tune('model')
        expected = self.store.read()['pending']
        self.c.settings_menu()
        with self.assertRaisesRegex(RuntimeError, 'Menu changed'):
            self.c.draft('effort', {'settings': {'model': 'gpt-5.6-luna'}}, expected=expected)
        self.assertEqual(self.store.read()['pending']['phase'], 'settings')

    def test_successful_refresh_updates_snapshot_without_runtime_settings_change(self):
        self.activate()
        self.c.tune('model')
        before = self.store.read()
        record = {'acpx': {'available_models': ['gpt-6-astra'], 'current_model_id': 'gpt-6-astra',
                           'config_options': [
                               {'id': 'reasoning_effort', 'options': [{'value': 'medium', 'name': 'Medium'}]},
                               {'id': 'mode', 'options': [{'value': 'agent-full-access', 'name': 'Full access'}]}]}}
        with patch.object(self.backend, 'metadata', return_value=record):
            result = self.c.refresh()
        self.assertTrue(result['refreshed'])
        self.assertEqual(len(result['choices']), 1)
        self.assertEqual(self.store.read()['main'], before['main'])
        self.assertEqual(self.store.read()['settings'], before['settings'])
        self.assertTrue((self.store.root / 'catalogs/codex.json').is_file())


if __name__ == '__main__':
    unittest.main()
