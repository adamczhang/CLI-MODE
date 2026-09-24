"""Launcher compatibility and fresh activation verification regressions."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend
from controller import Controller
from state import Store
import acpx
import adapters


def fake_package(folder, version=acpx.ACPX_VERSION):
    package = Path(folder) / 'node_modules/acpx'
    (package / 'dist').mkdir(parents=True)
    (package / 'package.json').write_text(json.dumps({'name': 'acpx', 'version': version}))
    (package / 'dist/cli.js').touch()
    (package / 'dist/runtime.js').touch()
    return package


class Performance(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'path with spaces'
        self.root.mkdir()
        # A global npm prefix: launcher text is irrelevant, only the package matters.
        self.bin = self.root / 'npm'
        self.bin.mkdir()
        (self.bin / ('acpx.cmd' if os.name == 'nt' else 'acpx')).write_text('any launcher template')
        self.package = fake_package(self.bin)
        self.owned = self.root / 'owned'
        self.node = self.root / 'node.exe'
        self.node.touch()
        env = {'PATH': str(self.bin), 'CLI_MODE_ACPX_ROOT': str(self.owned)}
        for context in (patch('installer.refresh_paths'), patch.dict(os.environ, env),
                        patch.object(acpx.shutil, 'which', return_value=str(self.node)),
                        patch.object(acpx, 'node_version', return_value=(22, 13, 0))):
            context.start()
            self.addCleanup(context.stop)

    def test_owned_copy_is_preferred_over_global_npm(self):
        owned = fake_package(self.owned)
        install = acpx.runtime_install()
        self.assertEqual((install['source'], install['package']), ('owned', str(owned.resolve())))
        self.assertEqual(acpx.executable(), [str(self.node.resolve()), str(owned.resolve() / 'dist' / 'cli.js')])

    @unittest.skipUnless(os.name == 'nt', 'Windows npm prefix layout')
    def test_global_fallback_ignores_launcher_text_and_prefers_its_node(self):
        install = acpx.runtime_install()
        self.assertEqual((install['source'], install['package']), ('global', str(self.package.resolve())))
        (self.bin / 'node.exe').touch()
        self.assertEqual(acpx.runtime_install()['node'], str((self.bin / 'node.exe').resolve()))

    def test_wrong_version_or_missing_entry_point_is_skipped(self):
        fake_package(self.owned, version='0.17.1')
        (self.package / 'dist/runtime.js').unlink()
        with self.assertRaises(RuntimeError) as raised:
            acpx.runtime_install()
        self.assertIn(acpx.install_command(), str(raised.exception))
        self.assertIn(str(self.owned), acpx.install_command())

    def test_old_node_is_rejected_before_binding(self):
        fake_package(self.owned)
        with patch.object(acpx, 'node_version', return_value=(20, 11, 1)):
            with self.assertRaises(RuntimeError) as raised:
                acpx.runtime_install()
        self.assertIn('22.13.0', str(raised.exception))

    def test_saved_binding_is_reverified_without_rediscovery(self):
        owned = fake_package(self.owned)
        saved = acpx.runtime_install()
        (owned / 'package.json').write_text(json.dumps({'name': 'acpx', 'version': '0.19.0'}))
        with self.assertRaises(RuntimeError):
            acpx.runtime_install(saved)

    def test_setup_uses_the_shipped_lockfile(self):
        lock = json.loads((acpx.RUNTIME_SPEC / 'package-lock.json').read_text(encoding='utf-8'))
        spec = json.loads((acpx.RUNTIME_SPEC / 'package.json').read_text(encoding='utf-8'))
        self.assertEqual(spec['dependencies'], {'acpx': acpx.ACPX_VERSION})
        self.assertEqual(lock['packages']['node_modules/acpx']['version'], acpx.ACPX_VERSION)
        setup = (PLUGIN / 'scripts/setup.ps1').read_text(encoding='utf-8')
        self.assertIn("$AcpxVersion='" + acpx.ACPX_VERSION + "'", setup)
        self.assertIn('npm.cmd ci', setup)
        self.assertNotIn('install -g acpx', setup)

    def test_activation_reads_metadata_twice_and_checks_post_readiness_drift(self):
        for drift in (False, True):
            backend = FakeBackend()
            store = Store('activation-' + str(drift), self.root, self.root / str(drift))
            control = Controller(store, backend)
            control.frontend()
            original = backend.metadata
            calls = []
            def metadata(owned):
                calls.append(owned['name'])
                record = original(owned)
                if drift and len(calls) == 2:
                    record['acpx']['current_model_id'] = 'rejected-after-readiness'
                return record
            with patch.object(backend, 'metadata', side_effect=metadata):
                if drift:
                    with self.assertRaisesRegex(RuntimeError, 'model rejected'):
                        control.activate('gemini-3.8-flash-high', 'allow')
                    self.assertFalse(store.read()['active'])
                else:
                    control.activate('gemini-3.8-flash-high', 'allow')
                    self.assertTrue(store.read()['active'])
            self.assertEqual(len(calls), 2)
            control.off()

    def test_all_six_backends_verify_supplied_record_without_fetching(self):
        for agent in adapters.implemented():
            with self.subTest(agent=agent):
                adapter = adapters.module(agent)
                settings = adapter.selection(self.root, **adapter.DEFAULTS)
                values = {step[-2]: step[-1] for step in adapter.setting_steps(settings)}
                record = {'acpxRecordId': 'stable', 'acpSessionId': 'provider', 'acpx': {
                    'current_model_id': settings['model'],
                    'config_options': [{'id': key, 'currentValue': value} for key, value in values.items()]}}
                backend = adapter.Backend()
                with patch.object(backend, 'metadata', side_effect=AssertionError('duplicate read')):
                    self.assertTrue(backend.verify({'settings': settings}, record=record))
                    rejected = copy.deepcopy(record)
                    for item in rejected['acpx']['config_options']:
                        item['currentValue'] = 'not-accepted'
                    with self.assertRaises(RuntimeError):
                        backend.verify({'settings': settings}, record=rejected)
