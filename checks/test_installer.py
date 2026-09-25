"""Approval gates, recovery, real bootstrap scans and prompt-free auth protocol."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend
from controller import Controller
from state import Store
import installer
import setup_auth

spec = importlib.util.spec_from_file_location('acp_login', PLUGIN/'scripts/acp-login.py')
acp_login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acp_login)


@unittest.skipUnless(os.name == 'nt', 'Windows job objects')
class ManagedLauncherJob(unittest.TestCase):
    """What a served agent starts ends with the managed launcher. Antigravity's ACP server starts an
    inner server and exits without stopping it; Windows kept that pair running (19 pairs, 2026-09-23)."""
    def orphan_survives(self, join):
        # A server that starts its own child and exits, as agy_acp_server.exe does; the launcher then exits.
        server = ('import subprocess, sys; d = subprocess.DEVNULL; child = subprocess.Popen([sys.executable, "-c", '
                  '"import time; time.sleep(60)"], stdin=d, stdout=d, stderr=d); print(child.pid, flush=True)')
        launcher = ('import importlib.util, subprocess, sys\n'
                    'spec = importlib.util.spec_from_file_location("acp_login", sys.argv[1])\n'
                    'module = importlib.util.module_from_spec(spec)\n'
                    'spec.loader.exec_module(module)\n'
                    + ('assert module.join_kill_on_close_job()\n' if join else '') +
                    'server = subprocess.Popen([sys.executable, "-c", sys.argv[2]], stdin=subprocess.DEVNULL, '
                    'stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)\n'
                    'print(server.stdout.readline().strip(), flush=True)\n')
        done = subprocess.run([sys.executable, '-c', launcher, str(PLUGIN / 'scripts/acp-login.py'), server],
                              capture_output=True, text=True, timeout=30)
        orphan = int(done.stdout.strip())
        time.sleep(1)
        alive = str(orphan) in subprocess.run(['tasklist', '/FI', 'PID eq %d' % orphan, '/NH'],
                                              capture_output=True, text=True).stdout
        if alive:
            subprocess.run(['taskkill', '/PID', str(orphan), '/F'], capture_output=True)
        return alive

    def test_without_the_job_windows_keeps_the_orphan(self):
        self.assertTrue(self.orphan_survives(join=False))  # The leak, as it was.

    def test_the_job_ends_everything_the_server_started(self):
        self.assertFalse(self.orphan_survives(join=True))


@unittest.skipUnless(os.name == 'nt', 'Windows refuses to delete open files')
class ManagedLauncherTemp(unittest.TestCase):
    """Antigravity's server unpacks 1.25 GB per start and, ended by the job, never deletes it
    (278 folders, 347 GB, 2026-09-24). Each launcher's server unpacks into a folder of its own,
    and the next launcher removes every folder whose launcher is gone."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name) / 'tmp'

    def folder(self, held=True):
        folder, lock = acp_login.private_temp(self.base)
        (folder / '_MEI12345').mkdir()
        (folder / '_MEI12345' / 'python.dll').write_bytes(b'x' * 1024)
        if held:
            self.addCleanup(lock.close)
        else:
            lock.close()
        return folder

    def test_only_folders_of_ended_launchers_are_stale(self):
        running, ended, current = self.folder(), self.folder(held=False), self.folder()
        self.assertEqual(acp_login.stale(self.base, current), [ended])
        self.assertTrue((self.base / (running.name + '.lock')).exists())
        self.assertFalse((self.base / (ended.name + '.lock')).exists())

    def test_a_killed_launchers_folder_is_removed_by_the_next(self):
        launcher = ('import importlib.util, sys, time\n'
                    'from pathlib import Path\n'
                    'spec = importlib.util.spec_from_file_location("acp_login", sys.argv[1])\n'
                    'module = importlib.util.module_from_spec(spec)\n'
                    'spec.loader.exec_module(module)\n'
                    'folder, lock = module.private_temp(Path(sys.argv[2]))\n'
                    '(folder / "_MEI1" ).mkdir(); (folder / "_MEI1" / "base_library.zip").write_bytes(b"x" * 4096)\n'
                    'print(folder, flush=True)\n'
                    'time.sleep(60)\n')
        process = subprocess.Popen([sys.executable, '-c', launcher, str(PLUGIN / 'scripts/acp-login.py'),
                                    str(self.base)], stdout=subprocess.PIPE, text=True)
        killed = Path(process.stdout.readline().strip())
        current = self.folder()
        self.assertEqual(acp_login.stale(self.base, current), [], 'a running launcher keeps its folder')
        process.kill()  # TerminateProcess, as the job does: nothing in the launcher runs.
        process.wait(timeout=10)
        process.stdout.close()
        acp_login.remove_stale(self.base, current).join(timeout=30)
        self.assertFalse(killed.exists())
        self.assertTrue(current.exists())

    def test_leftover_locks_are_removed_unless_held(self):
        self.base.mkdir(parents=True)
        (self.base / 'gone.lock').write_text('')
        held = open(self.base / 'starting.lock', 'x')  # A launcher about to make its folder.
        self.addCleanup(held.close)
        acp_login.stale(self.base, None)
        self.assertFalse((self.base / 'gone.lock').exists())
        self.assertTrue((self.base / 'starting.lock').exists())

    def test_the_server_gets_its_own_temp(self):
        root = self.base.parent
        seen = {}

        def serve(command, env):
            seen.update(env)
            return 0
        with patch.object(acp_login, '__file__', str(root / 'acp-login.py')), \
                patch.object(acp_login, 'join_kill_on_close_job'), \
                patch.object(acp_login.subprocess, 'call', side_effect=serve), \
                patch.object(sys, 'argv', ['acp-login.py']):
            with self.assertRaises(SystemExit):
                acp_login.main()
        folder = Path(seen['TEMP'])
        self.assertEqual(seen['TMP'], seen['TEMP'])
        self.assertEqual(folder.parent, root.resolve() / 'tmp')
        self.assertEqual(len(folder.name), 8)  # Short: Antigravity's deepest unpacked path is 120 characters.
        self.assertTrue(folder.is_dir())


class Installer(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows PATH refresh')
    def test_repeated_path_refresh_is_idempotent_and_launchable(self):
        with patch.dict(os.environ):
            installer.refresh_paths()
            expected = os.environ['PATH']
            for _ in range(30):
                installer.refresh_paths()
            self.assertEqual(os.environ['PATH'], expected)
            self.assertLess(len(expected), 8191)
            result = subprocess.run([os.environ['COMSPEC'], '/D', '/C', 'pwsh -NoProfile -Command "exit 0"'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = Controller(Store('installer', self.root, self.root/'state'), FakeBackend())

    def test_permission_and_active_work_gate_popup(self):
        with patch('installer.call') as call:
            with self.assertRaisesRegex(ValueError, 'permission'):
                self.control.setup_start()
            with patch('frontends.access_readiness', return_value={'ready': False}):
                with self.assertRaisesRegex(RuntimeError, 'Full Access'):
                    self.control.setup_start(True)
            with self.control.store.edit() as state:
                state['active'] = True
            with patch('frontends.access_readiness', return_value={'ready': True}):
                with self.assertRaisesRegex(RuntimeError, 'Stop active'):
                    self.control.setup_start(True)
            call.assert_not_called()

    def start(self):
        with patch('frontends.access_readiness', return_value={'ready': True}), \
             patch('installer.call', return_value={'runId': 'a'*32, 'status': 'starting'}):
            self.control.setup_start(True)

    def test_completion_rechecks_and_returns_menu_without_activation(self):
        self.start()
        with patch('installer.call', return_value={'status': 'complete'}), \
             patch.object(self.control, 'first_time_check', return_value={'activationMenu': 'Agent Settings'}) as check:
            result = self.control.setup_status()
        check.assert_called_once()
        self.assertEqual(result['setup']['activationMenu'], 'Agent Settings')
        self.assertFalse(self.control.store.read()['active'])

    def test_failed_and_interrupted_do_not_mark_setup_ready(self):
        self.start()
        for status in ('running', 'failed', 'interrupted', 'canceled'):
            with patch('installer.call', return_value={'status': status}), \
                 patch.object(self.control, 'first_time_check') as check:
                self.assertEqual(self.control.setup_status()['status'], status)
                check.assert_not_called()

    def test_cancel_gates_before_waiting_for_installer_and_does_not_resume(self):
        self.start()
        def cancel(*args, **kwargs):
            self.assertIsNone(self.control.store.read()['pending'])
            self.assertFalse(self.control.store.read()['active'])
            return {'status': 'running', 'cancelRequested': True}
        with patch('installer.call', side_effect=cancel):
            result = self.control.off()
        self.assertTrue(result['installerCancellation']['cancelRequested'])
        with self.assertRaisesRegex(RuntimeError, 'No installer'):
            self.control.setup_status()

    def test_completed_stale_run_cannot_restore_canceled_menu(self):
        self.start()
        def finish(*args, **kwargs):
            self.control.disable()
            return {'status': 'complete'}
        with patch('installer.call', side_effect=finish):
            self.assertFalse(self.control.setup_status()['resumed'])
        self.assertIsNone(self.control.store.read()['pending'])

    @unittest.skipUnless(os.name == 'nt', 'Windows bootstrap')
    def test_powershell_bootstrap_scans_without_python_or_installs(self):
        script = str(PLUGIN/'scripts/setup.ps1').replace("'", "''")
        env = dict(os.environ, USERPROFILE=str(self.root), LOCALAPPDATA=str(self.root/'local'))
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            "function Get-Command { param($Name) return $null }; & '"+script+"' -Action Scan"],
            env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        scan = json.loads(result.stdout)
        # Shared prerequisites plus the Antigravity CLI and its separate ACP runtime.
        self.assertEqual([item['name'] for item in scan['checks']],
            ['Python', 'Node.js', 'npm', 'npx', 'ACPX',
             'Antigravity CLI', 'Antigravity ACP'])
        self.assertTrue(all(not item['installed'] for item in scan['checks']))
        self.assertIn('run /cli again', scan['manual'])
        self.assertFalse((self.root/'local').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows bootstrap')
    def test_popup_requires_approval_and_rejects_foreign_run(self):
        root = self.root/'setup'
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
            str(PLUGIN/'scripts/setup.ps1'), '-SetupRoot', str(root)]
        result = subprocess.run(command+['-Action', 'Start'], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('approval is required', result.stderr)
        root.mkdir(exist_ok=True)
        (root/'status.json').write_text(json.dumps({'runId': 'a'*32, 'status': 'running', 'pid': os.getpid()}))
        result = subprocess.run(command+['-Action', 'Cancel', '-RunId', 'b'*32],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((root/'status.json.cancel').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows bootstrap')
    def test_duplicate_start_rejected_and_closed_window_is_interrupted(self):
        root = self.root/'setup'
        root.mkdir()
        status = root/'status.json'
        status.write_text(json.dumps({'runId': 'a'*32, 'status': 'running',
            'pid': os.getpid(), 'message': 'Running'}))
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
            str(PLUGIN/'scripts/setup.ps1'), '-SetupRoot', str(root)]
        result = subprocess.run(command+['-Action', 'Start', '-Approved'],
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already running', result.stderr)
        status.write_text(json.dumps({'runId': 'a'*32, 'status': 'running',
            'pid': 2000000000, 'message': 'Running'}))
        result = subprocess.run(command+['-Action', 'Status', '-RunId', 'a'*32],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'interrupted')

    def test_native_verification_never_accepts_model_generated_usage(self):
        with patch('setup_auth.shutil.which', return_value='agy'), patch('setup_auth.subprocess.run') as run:
            for turns in (0, 1):
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'status': 'SUCCESS',
                    'num_turns': turns, 'command': {'name': 'usage'}}))
                if turns:
                    with self.assertRaises(RuntimeError):
                        setup_auth.verify_native()
                else:
                    setup_auth.verify_native()
            self.assertEqual(run.call_args.args[0][-2:], ['-p', '/usage'])

    def test_authentication_negotiates_advertised_method_without_task_prompt(self):
        fake = self.root/'fake.py'
        history = self.root/'methods.jsonl'
        fake.write_text('''import json,sys
for line in sys.stdin:
 event=json.loads(line)
 with open(sys.argv[1],'a') as log: log.write(json.dumps(event)+'\\n')
 method=event['method']
 result={'authMethods':[{'id':'oauth-personal','name':'Google'}]} if method=='initialize' else {'sessionId':'fixture'} if method=='session/new' else {}
 print(json.dumps({'jsonrpc':'2.0','id':event['id'],'result':result}),flush=True)
''', encoding='utf-8')
        acp_login.login([sys.executable, str(fake), str(history)], os.environ.copy(), self.root)
        events = [json.loads(line) for line in history.read_text().splitlines()]
        self.assertEqual([e['method'] for e in events], ['initialize', 'authenticate', 'session/new'])
        manifest = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
        self.assertEqual(events[0]['params']['clientInfo']['version'], manifest['version'])
        self.assertEqual(events[1]['params']['methodId'], 'oauth-personal')

    def test_unsupported_terminal_auth_never_calls_authenticate(self):
        with patch.object(acp_login, 'RPC') as rpc:
            rpc.return_value.request.return_value = {'authMethods': [{'id': 'login', 'type': 'terminal'}]}
            with self.assertRaisesRegex(RuntimeError, 'interactive ACP client'):
                acp_login.login(['fixture'], {}, self.root)
            self.assertEqual(rpc.return_value.request.call_count, 1)
            rpc.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
