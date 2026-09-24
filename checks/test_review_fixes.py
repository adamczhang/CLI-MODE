"""Regressions for the 2026-09-24 whole-codebase review (shared and Codex code)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend, hook
from controller import Controller
from state import Store


class HookInput(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_codex_hook_reads_utf8_whatever_the_console_code_page(self):
        # Windows Python reads stdin as the ANSI code page (cp1252) unless told otherwise.
        event = dict(session_id='utf8', cwd=str(self.root), hook_event_name='UserPromptSubmit',
                     prompt='/cli café 来')
        env = dict(os.environ, PYTHONIOENCODING='cp1252', CLI_MODE_DATA=str(self.root / 'data'))
        done = subprocess.run([sys.executable, str(PLUGIN / 'hooks/route.py')], input=json.dumps(event, ensure_ascii=False).encode('utf-8'),
                              capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8', 'replace'))
        self.assertIn('hookSpecificOutput', json.loads(done.stdout))


class Routing(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store('review', self.root, self.root / 'data')
        self.backend = FakeBackend()
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')

    def handle(self, prompt, **fields):
        event = dict(session_id='review', cwd=str(self.root), hook_event_name='UserPromptSubmit', prompt=prompt)
        event.update(fields)
        return hook.handle(event, self.store.root)

    def test_a_busy_queue_worker_does_not_fail_the_captured_prompt(self):
        self.control.mode('direct')
        with patch.object(Controller, 'ensure_pump', side_effect=RuntimeError('CLI-MODE state is busy; retry.')):
            self.handle('/d a task')  # Captured already: failing here would invite sending it twice.
        state = self.store.read()
        self.assertEqual([record['status'] for record in state['requests'].values()], ['captured'])

    def test_stopping_during_setup_cancels_the_installer(self):
        with self.store.edit() as state:
            state['pending'] = dict(onboarding='installing', installerRun='run-1')
        routed = self.handle('/cli stop')
        self.assertIn('OFF', json.dumps(routed))  # The hook disabled routing, as the host then runs off.
        with patch('binding.installer.call', return_value={'status': 'cancelled'}) as call:
            result = self.control.off()
        call.assert_called_once_with('Cancel', run_id='run-1')
        self.assertEqual(result['installerCancellation'], {'status': 'cancelled'})
        self.assertNotIn('installerRun', json.dumps(self.store.read()))  # Only this off cancels it.

    def test_compaction_does_not_cancel_again(self):
        self.control.mode('direct')
        with patch.object(Controller, 'cancel', return_value={'canceled': True}) as cancel:
            self.handle('/cli cancel')
            hook.handle(dict(session_id='review', cwd=str(self.root), hook_event_name='SessionStart',
                             source='compact'), self.store.root)
        self.assertEqual(cancel.call_count, 1)  # A later queued turn must not be canceled by the restore.

    def test_the_usage_prefetch_never_keeps_a_failed_activation_waiting_to_exit(self):
        import threading
        release = threading.Event()
        self.addCleanup(release.set)
        with patch('confirmation.usage', side_effect=lambda *args: release.wait(30) and 'summary'):
            prefetched = self.control.prefetch_usage('agy', 'gemini-3.8-flash-high', 'allow')
            self.assertIsNotNone(prefetched)
            # Python joins non-daemon threads at exit: a failed activation would wait for the lookup.
            waiting = [thread for thread in threading.enumerate()
                       if thread is not threading.main_thread() and not thread.daemon]
            self.assertEqual(waiting, [])
            release.set()
            self.assertEqual(prefetched[2].result(timeout=5), 'summary')

    def test_a_prompt_reads_the_state_file_once(self):
        self.control.mode('direct')
        with patch.object(Store, 'read', autospec=True, side_effect=Store.read) as read:
            self.handle('/cli queue')
        self.assertEqual(read.call_count, 1)  # Inside edit(), under the lock.

    def test_importing_the_router_does_not_render_help(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import help_view; '
                'help_view.render = lambda *a, **k: sys.exit("rendered at import"); import state')
        done = subprocess.run([sys.executable, '-c', code, str(PLUGIN / 'scripts')], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

if __name__ == '__main__':
    unittest.main()
