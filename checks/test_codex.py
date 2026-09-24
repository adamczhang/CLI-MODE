"""Codex-specific settings, verification and command-admission regressions."""
import tempfile
import json
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch
from test_controller import PLUGIN
from test_controller import Controller, FakeBackend, Store
import acpx
import codex_cli
import frontends
from state import backend_records, route


class Codex(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = codex_cli.selection(self.root, 'gpt-6-astra', 'allow', 'medium')
        self.owned = dict(name='codex-test', workspace=str(self.root), settings=self.settings)

    def test_sixth_registry_entry_and_control_aliases(self):
        self.assertEqual(backend_records()[5]['id'], 'codex')
        for prefix in ('/', '$'):
            self.assertEqual(route(prefix+'cli codex', {'active':False}), {'route':'frontend', 'agent':'codex'})
            self.assertEqual(route(prefix+'cli bind codex', {'active':False}), {'route':'bind', 'agent':'codex'})
        self.assertIn(('Codex CLI', 'codex'), frontends.BACKEND_TOOLS['codex'])

    def test_default_is_advertised_sol_with_high_effort(self):
        defaults = codex_cli.DEFAULTS
        self.assertEqual(defaults, dict(model='gpt-6-sol', effort='high', access='allow'))
        catalog = codex_cli.catalog(self.root)
        self.assertEqual(catalog['requestedDefaultModelId'], defaults['model'])
        self.assertEqual(catalog['requestedDefaultEffort'], defaults['effort'])
        selected = codex_cli.selection(self.root, **defaults)
        self.assertEqual((selected['modelName'], selected['effort']), ('GPT-6 Sol', 'High'))
        self.assertEqual(codex_cli.setting_steps(selected), [
            ['set', 'model', 'gpt-6-sol'], ['set', 'reasoning_effort', 'high'],
            ['set', 'mode', 'agent-full-access']])
        self.assertIn('Model: GPT-6 Sol\nEffort: High', frontends.settings_text('codex', selected))

    def test_older_cached_catalog_does_not_hide_new_default(self):
        cached = codex_cli.catalog(self.root)
        cached['modelFamilies'] = [f for f in cached['modelFamilies']
                                   if f['modelId'] != 'gpt-6-sol']
        cached['modelFamilies'].append(dict(modelId='account-extra', name='Account extra',
                                            efforts=[dict(name='Low', value='low')]))
        cached['models'] = [m for m in cached['models'] if m['id'] != 'gpt-6-sol']
        folder = self.root / 'catalogs'
        folder.mkdir()
        path = folder / 'codex.json'
        path.write_text(json.dumps(cached), encoding='utf-8')
        refreshed = codex_cli.catalog(self.root)
        self.assertEqual(refreshed['requestedDefaultModelId'], 'gpt-6-sol')
        self.assertEqual(refreshed['requestedDefaultEffort'], 'high')
        self.assertIn('account-extra', [f['modelId'] for f in refreshed['modelFamilies']])
        self.assertEqual(codex_cli.selection(self.root, **codex_cli.DEFAULTS)['effort'], 'High')
        sol = next(f for f in refreshed['modelFamilies'] if f['modelId'] == 'gpt-6-sol')
        sol['efforts'] = [dict(name='Medium', value='medium')]
        path.write_text(json.dumps(refreshed), encoding='utf-8')
        self.assertEqual(codex_cli.selection(self.root, **codex_cli.DEFAULTS)['effort'], 'High')

    def test_effort_options_belong_to_selected_model(self):
        for family in codex_cli.catalog(self.root)['modelFamilies']:
            for effort in family['efforts']:
                s = codex_cli.selection(self.root, family['modelId'], 'allow', effort['value'])
                self.assertEqual(s['effortValue'], effort['value'])
        with self.assertRaises(ValueError):
            codex_cli.selection(self.root, 'gpt-5.6-luna', 'allow', 'ultra')
        with self.assertRaises(ValueError):
            codex_cli.selection(self.root, 'unadvertised', 'allow', 'medium')

    def test_access_and_setting_order(self):
        self.assertEqual(codex_cli.setting_steps(self.settings), [
            ['set','model','gpt-6-astra'], ['set','reasoning_effort','medium'],
            ['set','mode','agent-full-access']])
        for unsupported in ('prompt', 'auto-edit'):
            with self.assertRaisesRegex(ValueError, 'Full access only'):
                codex_cli.selection(self.root, 'gpt-6-astra', unsupported, 'medium')
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            argv = codex_cli.Backend().command(self.owned, ['status'])
        self.assertIn('codex', argv)
        self.assertIn('--approve-all', argv)

    def test_verify_rejects_each_unaccepted_setting(self):
        values = {'model':'gpt-6-astra', 'reasoning_effort':'medium', 'mode':'agent-full-access'}
        def record(changes):
            selected = dict(values, **changes)
            return {'acpxRecordId':'stable', 'acpSessionId':'rotates', 'acpx':{
                'current_model_id':selected['model'], 'config_options':[
                    {'id':k,'currentValue':v} for k,v in selected.items()]}}
        backend = codex_cli.Backend()
        with patch.object(backend, 'metadata', return_value=record({})):
            self.assertEqual(backend.verify(self.owned), 'rotates')
        for key in values:
            with patch.object(backend, 'metadata', return_value=record({key:'wrong'})):
                with self.assertRaises(RuntimeError):
                    backend.verify(self.owned)

    def test_cached_catalog_and_stale_ownership_cannot_restore_prompt(self):
        data = codex_cli.catalog(self.root)
        data['accessControl']['options'].append(dict(access='prompt',nativeValue='read-only',nativeName='Ask'))
        (self.root/'catalogs').mkdir()
        (self.root/'catalogs/codex.json').write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual([x['access'] for x in codex_cli.catalog(self.root)['accessControl']['options']], ['allow'])
        stale = dict(self.owned, settings=dict(self.settings,access='prompt',mode='read-only'))
        with patch('acpx.subprocess.Popen') as start:
            with self.assertRaisesRegex(RuntimeError, 'Full access only'):
                codex_cli.Backend().start(stale, ['--file', 'task.txt'])
            start.assert_not_called()

    def test_commands_require_provider_advertisement(self):
        backend = codex_cli.Backend()
        self.assertEqual(backend.validate_command({'advertisedCommands':['review']}, '/review this'), {})
        for command in ('/login', '/unknown'):
            with self.assertRaisesRegex(RuntimeError, 'Codex'):
                backend.validate_command({'advertisedCommands':['review']}, command)
        self.assertFalse(codex_cli.NATIVE_HANDOFF)

    def test_first_readiness_uses_empty_session_recovery_before_strict_controls(self):
        class BootstrapBackend(FakeBackend):
            bootstrap_with_cli_readiness = True

        backend = BootstrapBackend()
        controller = Controller(Store('codex-bootstrap', self.root, self.root / 'state'),
                                backend, agent='codex')
        probes = []
        def readiness(owned, *args, **kwargs):
            probes.append((bool(owned.get('bootstrapPrompt')), owned.get('providerSession')))
        controller.frontend('codex')
        with patch.object(controller, 'readiness', side_effect=readiness):
            active = controller.activate('gpt-6-astra', 'allow', effort='medium', agent='codex')
        self.assertTrue(active['active'])
        # Exactly one provider readiness prompt: the bootstrap probe.
        self.assertEqual([bootstrap for bootstrap, _ in probes], [True])
        self.assertIsNone(probes[0][1])
        self.assertTrue(active['owned'][0]['providerSession'])
        self.assertEqual(backend.calls[0][-3:], ['ensure', '--name', active['main']])
        self.assertEqual([call[-3:] for call in backend.calls[1:]],
                         codex_cli.setting_steps(self.settings))
        self.assertTrue(controller.off()['shutdownComplete'])

    def test_bootstrap_prompt_uses_acpx_cli_instead_of_strict_bridge(self):
        backend = codex_cli.Backend()
        with patch.object(backend, 'command', return_value=['acpx', '--format', 'json', 'codex', '--file', 'probe.txt']), \
             patch('acpx.subprocess.Popen') as start:
            backend.start(dict(self.owned, bootstrapPrompt=True), ['--file', 'probe.txt'])
        args = start.call_args.args[0]
        self.assertEqual(args[:2], ['acpx', '--format'])
        self.assertEqual(start.call_args.kwargs['stdin'], subprocess.DEVNULL)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell login probe')
    def test_successful_login_on_stderr_is_not_an_auth_failure(self):
        source = (PLUGIN/'scripts/setup.ps1').read_text(encoding='utf-8')
        function = source[source.index('function Codex-SignedIn {'):source.index('function Scan {')]
        shim = self.root/'codex.cmd'
        path = str(shim).replace("'", "''")
        script = self.root/'probe.ps1'
        script.write_text("$ErrorActionPreference='Stop'\nfunction Get-Command { param($Name) "
            "[pscustomobject]@{Source='" + path + "'} }\n" + function +
            "\nCodex-SignedIn\n$ErrorActionPreference\n", encoding='utf-8')
        for code, expected in ((0, 'True'), (1, 'False')):
            shim.write_text('@echo off\necho Login status 1>&2\nexit /b '+str(code)+'\n')
            result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script)],
                                    capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.split(), [expected, 'Stop'])


if __name__ == '__main__':
    unittest.main()
