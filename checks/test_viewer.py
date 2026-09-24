"""The agent viewer: /cli view on|off, off by default, and the PowerShell window it opens."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend
from controller import Controller
from state import Store, route
import viewer


class Setting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'workspace').mkdir()
        self.store = Store('viewer', root / 'workspace', root / 'state')
        self.control = Controller(self.store, FakeBackend(), agent='claude')

    def tearDown(self):
        self.temp.cleanup()

    def test_commands_route_locally_in_every_state(self):
        state = self.store.read()
        for active in (False, True):
            state['active'] = active
            self.assertEqual(route('/cli view on', state), {'route': 'view', 'choice': 'on'})
            self.assertEqual(route('$cli view OFF', state), {'route': 'view', 'choice': 'off'})
            self.assertEqual(route('/cli view', state), {'route': 'view', 'choice': ''})
            self.assertEqual(route('/cli view maybe', state)['route'], 'hint')

    def test_off_by_default_and_saved_for_every_session(self):
        self.assertFalse(viewer.enabled(self.store.root))
        with self.store.edit() as state:
            state['backend'] = 'claude'  # The window names the active agent.
        with patch('viewer.launch', return_value='opened') as launch:
            result = self.control.view('on')
        launch.assert_called_once_with(self.store, self.control.adapter.LABEL)
        self.assertEqual(result['view'], 'on')
        self.assertIn('PowerShell window', result['message'])
        other = Store('another-session', self.store.workspace, self.store.root)
        self.assertTrue(viewer.enabled(other.root))
        with patch('viewer.launch') as launch:
            result = self.control.view('off')
        launch.assert_not_called()
        self.assertEqual(result['view'], 'off')
        self.assertFalse(viewer.enabled(self.store.root))
        self.assertEqual(self.store.read()['turnRoute'], {'route': 'view-result'})

    def test_a_turn_reopens_the_window_only_while_on(self):
        with patch('viewer.launch') as launch:
            viewer.ensure(self.store, 'Claude Code')
            launch.assert_not_called()
            viewer.save(self.store.root, 'on')
            viewer.ensure(self.store, 'Claude Code')
            launch.assert_called_once_with(self.store, 'Claude Code')
        # A missing PowerShell never fails the turn.
        with patch('viewer.launch', side_effect=RuntimeError('PowerShell was not found.')):
            viewer.ensure(self.store, 'Claude Code')

    def test_an_open_window_is_not_opened_twice(self):
        with patch('viewer.os.name', 'nt'), patch('viewer.subprocess.Popen') as popen:
            viewer.heartbeat(self.store).parent.mkdir(parents=True, exist_ok=True)
            viewer.heartbeat(self.store).touch()
            self.assertEqual(viewer.launch(self.store, 'Claude Code'), 'running')
            popen.assert_not_called()


@unittest.skipUnless(viewer.shell(), 'PowerShell is not installed')
class Window(unittest.TestCase):
    def test_draws_a_turn_and_closes_when_turned_off(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'operations'
            folder.mkdir()
            settings, beat = root / 'viewer.json', root / 'viewer.alive'
            settings.write_text(json.dumps({'view': 'on'}), encoding='utf-8')
            events = [
                dict(type='plan', entries=[dict(content='Reproduce the failure', status='in_progress')]),
                dict(type='message', messageId='m1', text='Looking at `viewer.py` first.\n'),
                dict(type='activity', toolCallId='t1', kind='read', status='completed', title='Read viewer.py',
                     locations=[dict(path='scripts/viewer.py', line=3)]),
                dict(type='message', messageId='m2', text='## Result\n\n- **Fixed** it\n'),
                # Copilot, Grok and Antigravity send no message IDs; agents may send CRLF, tables and escapes.
                dict(type='message', text='| Loader | Reload |\r\n|---|:-:|\r\n| \x1b]0;title\x07`yaml` | yes |\r\n'),
                dict(type='message', text='| env | no |\r\n\r\nDone.\r\n'),
                dict(type='usage', used=1200, size=200000),
                dict(type='done', stopReason='end_turn')]
            (folder / 'op.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
            process = subprocess.Popen(
                [viewer.shell(), '-NoLogo', '-NoProfile', '-File', str(PLUGIN / 'scripts/viewer.ps1'), '-Folder', str(folder),
                 '-Heartbeat', str(beat), '-Settings', str(settings), '-Agent', 'Claude Code'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace')
            try:
                until = time.monotonic() + 30
                while not beat.exists() and time.monotonic() < until:
                    time.sleep(.1)
                time.sleep(2)
                settings.write_text(json.dumps({'view': 'off'}), encoding='utf-8')
                out, err = process.communicate(timeout=30)
            finally:
                if process.poll() is None:
                    process.kill()
            self.assertEqual(process.returncode, 0, err)
            self.assertNotIn('\x1b]0;', out)  # The agent's escape sequence never reaches the console.
            text = __import__('re').sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', out)
            for expected in ('agent viewer', '● Claude Code', 'Plan  0/1', '▸ Reproduce the failure',
                             'Looking at viewer.py first.', '✓ Read', 'scripts/viewer.py:3', 'Result',
                             '• Fixed it', 'Loader │ Reload', 'yaml   │ yes', 'env    │ no', '✓ Done', 'context 1,200 / 200,000 (1%)', 'Viewer turned off'):
                self.assertIn(expected, text)
            self.assertFalse(beat.exists())


if __name__ == '__main__':
    unittest.main()
