"""Native command routing, privacy, conversation continuity, and failure gating."""
import tempfile
import json
from pathlib import Path
import unittest
import concurrent.futures
import subprocess
import sys
import threading
from unittest.mock import patch

from test_controller import FakeBackend
from controller import Controller
from state import Store, route
import native_agy


class NativeTransport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store('native-test', self.root, self.root / 'data')
        self.backend = FakeBackend()
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')

    @patch('native_agy.native_inventory', return_value={'commands': [{'name': 'help'}]})
    def test_unavailable_command_keeps_session_and_files_untouched(self, inventory):
        self.backend.validate_command = lambda owned, text: native_agy.validate_command(text, owned['workspace'])
        before = self.store.read()
        files = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(self.backend, 'start', side_effect=AssertionError('must not dispatch')):
            with self.assertRaisesRegex(RuntimeError, 'does not advertise'):
                self.control.send('/d /record')
        after = self.store.read()
        receipts = after.pop('requests')
        self.assertEqual({r['status'] for r in receipts.values()}, {'rejected'})
        self.assertEqual(after, before)
        self.assertEqual(self.backend.closed, [])
        files.pop(str(self.store.path))
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*')
                          if p.is_file() and p != self.store.path}, files)

    def test_only_help_is_local(self):
        for active in (False, True):
            state = {'active': active, 'pending': None, 'routingMode': 'direct'}
            # Help is the only local command, under either accepted prefix.
            for command in ('/help', '$help'):
                self.assertEqual(route(command, state)['route'], 'help')
            # /commands is not a help alias, ? is not an accepted prefix, and only /d reaches the agent.
            for command in ('/commands', '$commands', '?commands', '?help'):
                self.assertEqual(route(command, state)['route'], 'host')

    def test_native_commands_are_complete_leading_tokens(self):
        for text in ('/teamwork task', '/teamwork-preview task', '/commands', '/usage', '/model'):
            self.assertTrue(native_agy.command_request(text))
        for text in ('example /usage', '`/teamwork task`', '"/usage"', '/cli stop', '/help'):
            self.assertFalse(native_agy.command_request(text))

    def test_teamwork_alias_only_changes_the_command_token(self):
        self.assertEqual(native_agy.expand_alias('  /TEAMWORK  do this\r\n  exactly '),
                         '  /teamwork-preview  do this\r\n  exactly ')
        for text in ('/teamwork-preview task', '/teamworker task', 'example /teamwork', '`/teamwork`', '/usage'):
            self.assertEqual(native_agy.expand_alias(text), text)

    def test_direct_native_commands_use_literal_argv_including_help(self):
        from state import direct_payload
        from unittest.mock import MagicMock
        owned = self.store.read()['owned'][0]
        owned.update(nativeModel='gemini-3.8-flash-high', providerSession='continued-conversation',
                     nativeControl=str(self.root / 'native-control'))
        for request, expected in (
            ('/d /teamwork  task\r\n  literal $value ', '/teamwork-preview  task\r\n  literal $value '),
            ('$d /help', '/help'),
            ('/d /usage', '/usage')):
            with self.subTest(request=request):
                prompt = self.root / 'native-prompt.txt'
                with prompt.open('w', encoding='utf-8', newline='') as stream:
                    stream.write(direct_payload(request))
                process = MagicMock(pid=12345)
                # Run submission synchronously so stdin assertions are deterministic.
                with patch('native_agy.shutil.which', return_value='agy.exe'), \
                     patch('native_agy.subprocess.Popen', return_value=process) as launch, \
                     patch('native_agy.threading.Thread') as thread:
                    thread.side_effect = lambda target, **kwargs: MagicMock(start=target)
                    native_agy.start(owned, prompt, 60)
                argv = launch.call_args.args[0]
                self.assertEqual(argv[argv.index('-p') + 1], expected)
                self.assertEqual(argv[argv.index('--conversation') + 1], 'continued-conversation')
                self.assertNotIn('--input-format', argv)
                self.assertFalse(launch.call_args.kwargs.get('shell', False))
                process.stdin.write.assert_called_once_with('')
                process.stdin.close.assert_called_once()

    @patch('native_agy.prepare', return_value='gemini-3.8-flash-high')
    def test_handoff_closes_acp_once_and_followups_resume_native(self, prepare):
        original = self.store.read()['main']
        events = []
        self.control.send('/d /teamwork-preview  test\r\nkeep whitespace ', output=events.append)
        self.assertEqual(self.backend.closed, [original])
        self.assertEqual(self.store.read()['owned'][0]['providerSession'], 'native-fixture')
        self.assertEqual(self.store.read()['owned'][0]['transport'], 'native')
        self.assertTrue(any(e['type'] == 'context_warning' for e in events))
        self.assertEqual(next(e['text'] for e in events if e['type'] == 'message'),
                         '/teamwork-preview  test\r\nkeep whitespace ')
        self.control.send('/d interview answer', output=lambda e: None)
        self.assertEqual(self.backend.closed, [original])
        self.control.tune('effort')
        self.control.activate('gemini-3.8-flash-low', 'allow')
        self.assertEqual(self.store.read()['owned'][0]['providerSession'], 'native-fixture')
        self.assertTrue(self.control.off()['shutdownComplete'])

    @patch('native_agy.prepare', side_effect=RuntimeError('model unavailable'))
    def test_failed_preflight_keeps_acp_session_untouched(self, prepare):
        before = self.store.read()
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            self.control.send('/d /teamwork task')
        after = self.store.read()
        receipts = after.pop('requests')
        self.assertEqual({r['status'] for r in receipts.values()}, {'rejected'})
        self.assertEqual(after, before)
        self.assertEqual(self.backend.closed, [])

    @patch('native_agy.prepare', return_value='gemini-3.8-flash-high')
    def test_failed_close_gates_dispatch(self, prepare):
        with patch.object(self.backend, 'close', side_effect=RuntimeError('close failed')):
            with self.assertRaisesRegex(RuntimeError, 'close failed'):
                self.control.send('/d /usage')
        self.assertFalse(self.store.read()['active'])
        self.assertEqual(len(self.store.read()['owned']), 1)

    def test_native_events_never_relay_private_steps(self):
        for kind in ('tool', 'thinking', 'unknown', 'user_input'):
            self.assertIsNone(native_agy.public_event({'event': 'step_update',
                'step_update': {'step_type': kind, 'text_delta': 'private'}}))
        event = native_agy.public_event({'event': 'step_update',
            'step_update': {'step_type': 'agent_response', 'text_delta': 'public'}})
        self.assertEqual(event, {'type': 'message', 'text': 'public'})

    @patch('native_agy.prepare', return_value='gemini-3.8-flash-high')
    def test_terminal_native_errors_do_not_leave_uncertain_work(self, prepare):
        self.control.send('/d /usage', output=lambda e: None)
        def process_result(result):
            code = 'print(' + repr(json.dumps({'event': 'result', 'result': result})) + ')'
            return subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding='utf-8')
        with patch.object(self.backend, 'start', side_effect=lambda *a: process_result(
                {'status': 'ERROR', 'error': 'Command needs an interactive terminal'})):
            events = []
            with self.assertRaisesRegex(RuntimeError, 'did not complete'):
                self.control.send('/d /unsupported', output=events.append)
            self.assertTrue(any(e.get('message') == 'Command needs an interactive terminal' for e in events))
            self.assertFalse(self.store.read()['inflight'])
        with patch.object(self.backend, 'start', side_effect=lambda *a: process_result(
                {'status': 'SUCCESS', 'response': ''})):
            self.control.send('/d /empty-result', output=lambda e: None)
            self.assertFalse(self.store.read()['inflight'])

    @patch('native_agy.prepare', return_value='gemini-3.8-flash-high')
    def test_off_cancels_an_inflight_native_turn(self, prepare):
        self.control.send('/d /teamwork-preview test', output=lambda e: None)
        started = threading.Event()
        def start(owned, args, timeout):
            proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            running = native_agy.marker(owned, '.running')
            running.parent.mkdir(parents=True, exist_ok=True)
            running.write_text(str(proc.pid))
            started.set()
            return proc
        with patch.object(self.backend, 'start', side_effect=start), \
             patch.object(self.backend, 'close', side_effect=native_agy.close), \
             concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.control.send, '/d long turn', lambda e: None)
            self.assertTrue(started.wait(3))
            result = self.control.off()
            with self.assertRaisesRegex(RuntimeError, 'canceled'):
                future.result(timeout=5)
            self.assertTrue(result['shutdownComplete'])
            self.assertFalse(result['backgroundTasksVerified'])
            self.assertFalse(self.store.read()['inflight'])


if __name__ == '__main__':
    unittest.main()
