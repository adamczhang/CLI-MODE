"""Quota/catalog and real subprocess hook/CLI tests; no provider calls."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

PLUGIN = Path(os.environ.get('CLI_MODE_TEST_PLUGIN', Path(__file__).resolve().parents[1] / 'plugins/cli-mode')).resolve()
sys.path.insert(0, str(PLUGIN / 'scripts'))
import agy
spec = importlib.util.spec_from_file_location('usage_summary', PLUGIN / 'backends/agy/scripts/usage-summary.py')
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class Features(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cli-mode-feature-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, CODEX_HOME=str(self.root / 'home'), CLI_MODE_DATA=str(self.root / 'data'),
                        CODEX_THREAD_ID='fresh-feature-thread')

    def cli(self, *args):
        return subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'), '--workspace', str(self.root), *args],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)

    def hook(self, event, **fields):
        result = subprocess.run([sys.executable, str(PLUGIN / 'hooks/route.py')],
            input=json.dumps(dict(session_id='fresh-feature-thread', cwd=str(self.root), hook_event_name=event, **fields)),
            env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_cold_commands_and_missing_hook_gate_in_separate_processes(self):
        self.assertFalse(json.loads(self.cli('status').stdout)['active'])
        self.assertEqual(self.cli('frontend').returncode, 0)
        denied = self.cli('activate')
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('No routing hook has run', json.loads(denied.stdout)['error'])
        for command in ('/cli unknown', '$cli model Flash', '/cli permissions allow'):
            output = self.hook('UserPromptSubmit', prompt=command)
            expected = {'$cli model Flash': 'CLI-MODE: Agent not activated. /CLI to setup',
                        '/cli unknown': 'CLI-MODE has no /cli unknown. Say /help to see options.'}.get(
                            command, '/cli to activate.  Say /help to see options')
            self.assertIn('Reply with exactly '+json.dumps(expected), output['hookSpecificOutput']['additionalContext'])
        self.assertFalse(json.loads(self.cli('status').stdout)['owned'])
        self.assertTrue(json.loads(self.cli('off').stdout)['shutdownComplete'])

    def test_cli_without_workspace_defaults_to_execution_directory(self):
        result = subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'), 'status'],
                                env=self.env, cwd=self.root, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['workspace'], str(self.root.resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows command-wrapper regression')
    def test_windows_hooks_through_cmd_with_spaces_and_stdin(self):
        # Spaces and '&' in the plugin path must survive every way the command
        # can reach cmd.exe, including Codex's own: `cmd.exe /C "<command>"`.
        copied = self.root / 'plugin path with spaces & more'
        shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns('__pycache__'))
        hooks = json.loads((copied / 'hooks/hooks.json').read_text())['hooks']
        env = dict(self.env, PLUGIN_ROOT=str(copied))
        for event in ('SessionStart', 'UserPromptSubmit', 'PreToolUse'):
            command = hooks[event][0]['hooks'][0]['commandWindows']
            payload = json.dumps(dict(session_id='windows-fixture', cwd=str(self.root),
                                     hook_event_name=event, prompt='/cli', tool_name='spawn_agent'))
            # cmd.exe takes the command line as written (Python's list quoting would add \" escapes cmd keeps).
            invocations = (os.environ['COMSPEC'] + ' /C "' + command + '"',   # Codex: raw quoted argument.
                           os.environ['COMSPEC'] + ' /D /S /C "' + command + '"',
                           command)                                            # No shell at all.
            # Codex 0.155 runs Windows hooks through PowerShell, where the old quote-free form was a parse error.
            invocations += tuple([shell, '-NoLogo', '-NoProfile', '-Command', command]
                                 for shell in (shutil.which('pwsh'), shutil.which('powershell')) if shell)
            for args in invocations:
                result = subprocess.run(args, input=payload,
                    env=env, cwd=self.root, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                if event == 'UserPromptSubmit':
                    self.assertIn('frontend --agent home', value['hookSpecificOutput']['additionalContext'])
                elif event == 'SessionStart':
                    # A new session gets the short preload, pointing at this plugin copy.
                    preload = value['hookSpecificOutput']['additionalContext']
                    self.assertIn('no CLI agent is active', preload)
                    self.assertIn(str(copied), preload)
                else:
                    self.assertEqual(value, {})

    @unittest.skipUnless(os.name == 'nt', 'Windows command-wrapper regression')
    def test_windows_hook_propagates_script_failure(self):
        hooks = json.loads((PLUGIN / 'hooks/hooks.json').read_text())['hooks']
        command = hooks['UserPromptSubmit'][0]['hooks'][0]['commandWindows']
        result = subprocess.run(os.environ['COMSPEC'] + ' /C "' + command + '"', input='{broken',
            env=dict(self.env, PLUGIN_ROOT=str(PLUGIN)), cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('state could not be restored', json.loads(result.stdout)['systemMessage'])

    def test_compaction_does_not_restart_frontend(self):
        self.hook('UserPromptSubmit', prompt='/cli agy')
        self.cli('frontend')
        draft = self.root / 'draft.json'
        draft.write_text('{"model":"gemini-pro-agent","snapshot":["High","Low"]}')
        self.cli('draft', '--phase', 'effort', '--file', str(draft))
        context = self.hook('SessionStart', source='compact')['hookSpecificOutput']['additionalContext']
        self.assertIn('setup menu is pending', context)
        self.assertNotIn('Run controller frontend', context)
        self.assertEqual(json.loads(self.cli('status').stdout)['pending']['phase'], 'effort')

    def test_cached_model_effort_and_access_mappings_without_launch(self):
        selection = agy.selection(self.root, 'gemini-pro-agent', 'prompt')
        self.assertEqual((selection['modelName'], selection['effort'], selection['mode']), ('Gemini 3.1 Pro', 'High', 'default'))
        self.assertEqual(agy.selection(self.root, 'gemini-3.8-flash-low', 'allow')['effort'], 'Low')
        with self.assertRaises(ValueError): agy.selection(self.root, 'gemini-3.1-pro-medium', 'allow')
        with self.assertRaises(ValueError): agy.selection(self.root, 'gemini-pro-agent', 'unknown-access')
        data = agy.catalog(self.root)
        refreshed = self.root / 'catalogs/agy.json'
        refreshed.parent.mkdir()
        data['fetchedAt'] = 'fresh-test-catalog'
        refreshed.write_text(json.dumps(data))
        self.assertEqual(agy.catalog(self.root)['fetchedAt'], 'fresh-test-catalog')

    def test_agy_auto_edit_stays_blocked_in_old_catalogs_and_saved_sessions(self):
        from unittest.mock import patch
        data = agy.catalog(self.root)
        data['accessControl']['options'].append(dict(access='auto-edit', nativeValue='auto_edit'))
        cached = self.root / 'catalogs/agy.json'
        cached.parent.mkdir()
        cached.write_text(json.dumps(data))
        self.assertNotIn('auto-edit', [v['access'] for v in agy.catalog(self.root)['accessControl']['options']])
        with self.assertRaisesRegex(ValueError, 'client file permissions'):
            agy.selection(self.root, 'gemini-3.8-flash-high', 'auto-edit')
        owned = dict(workspace=str(self.root), settings=dict(access='auto-edit'))
        with self.assertRaisesRegex(RuntimeError, 'client file permissions'):
            agy.Backend().command(owned, ['--file', 'prompt.txt'])
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            self.assertIn('status', agy.Backend().command(owned, ['status']))

    def payload(self, buckets):
        return {'status':'SUCCESS','command':{'name':'usage','data':{'groups':[{'name':'Gemini Models','buckets':buckets}]}}}

    def test_quota_math_countdown_and_next_reset(self):
        now = dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc)
        buckets = [{'window':'weekly','remaining_fraction':.75,'reset_time':(now+dt.timedelta(hours=49)).isoformat()},
                   {'window':'5h','remaining_fraction':1,'reset_time':(now+dt.timedelta(minutes=30)).isoformat()}]
        result = usage.summarize(self.payload(buckets), 'gemini-pro-agent', now)
        self.assertEqual(result['windows'][0]['utilization'], '25.0% used')
        self.assertEqual(result['windows'][0]['resetRemaining'], '2 days 1 hours')
        self.assertEqual(result['nextReset']['window'], '5h')
        self.assertIn('less than 1 hour', result['nextReset']['remaining'])
        self.assertIn('CLI account', result['source'])

    def test_invalid_quota_values_and_expired_reset_not_invented(self):
        now = dt.datetime(2026,9,21,tzinfo=dt.timezone.utc)
        for invalid in (None, True, float('nan'), -1, 2, '0.5'):
            value = usage.summarize(self.payload([{'window':'weekly','remaining_fraction':invalid,'reset_time':'2026-09-20T00:00:00Z'}]), 'gemini-pro-agent', now)
            self.assertEqual(value['windows'][0]['utilization'], 'unavailable')
            self.assertIsNone(value['nextReset'])

    def test_malformed_quota_payloads_fail_cleanly(self):
        now = dt.datetime.now(dt.timezone.utc)
        invalid = [None, [], {'status':'SUCCESS','command':None},
                   {'status':'SUCCESS','command':{'name':'usage','data':None}}, self.payload(None), self.payload([None])]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                usage.summarize(payload, 'gemini-pro-agent', now)


if __name__ == '__main__': unittest.main()
