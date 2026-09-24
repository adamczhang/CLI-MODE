"""Relay: Markdown mid-turn updates and one final nested view; view references; settled stop."""
import json
import re
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from test_controller import FakeBackend, runtime_process, runtime_result
from controller import Controller
from state import Store


def activity(ident, kind='read', status='completed', title='Read source'):
    return dict(type='activity', toolCallId=ident, kind=kind, status=status, title=title,
                locations=[dict(path='src/app.py', line=3)])


class ScriptedBackend(FakeBackend):
    """Emits a fixed public event list for every prompt."""
    events = []

    def start(self, owned, args, timeout=60):
        if '--file' in args and not Path(args[-1]).read_text(encoding='utf-8').startswith('Confirm readiness'):
            self.calls.append(args)
            return runtime_process(self.events + [runtime_result()])
        return super().start(owned, args, timeout)  # Activation's readiness probe.


class Relay(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.views = self.root / 'views'
        self.backend = ScriptedBackend()
        self.control = Controller(Store('relay', self.root, self.root / 'state'), self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')

    def send(self, events):
        self.backend.events = events
        return self.control.send('/d task', output=lambda event: None)['requestId']

    def test_completed_turn_posts_markdown_and_one_final_nested_view(self):
        request = self.send([
            dict(type='plan', entries=[dict(content='Inspect', status='completed'),
                                       dict(content='Fix <b>it</b>', status='in_progress')]),
            activity('a'), activity('b', kind='execute', status='in_progress', title=None),
            dict(type='message', text='Found the bug in <script>alert(1)</script> parser.\n')])
        result = self.control.relay(request, 0, wait=1, view_dir=self.views)
        self.assertTrue(result['done'])
        self.assertEqual(result['status'], 'completed')
        # The mid-turn update is chat Markdown, ready to post.
        update = result['markdown']
        self.assertTrue(update.startswith('**Passing to Antigravity...**'))
        self.assertIn('**Antigravity says...**', update)
        self.assertIn('Found the bug', update)
        self.assertIn('Antigravity work: 1 running', update)
        # The turn's one view, with the exact line Codex renders.
        path = result['messageView']['path']
        self.assertEqual(result['reference'], '\ue200visualize\ue202' + json.dumps({'path': path}) + '\ue201')
        html = Path(path).read_text(encoding='utf-8')
        self.assertIn('Antigravity says...', html)
        self.assertNotIn('Passing to', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script', html.replace('&lt;script', ''))
        self.assertIn('Fix &lt;b&gt;it&lt;/b&gt;', html)
        self.assertLess(html.index('says...'), html.index('<details class="work">'))
        self.assertRegex(html, r'<details class="work"><summary>Antigravity work · 1 running · 1 done</summary>')
        self.assertEqual(len(re.findall(r'<details', html)), 4)  # Work, plan, reading, commands.
        self.assertIn('<details open><summary>Plan · 1 of 2 done</summary>', html)
        self.assertIn('aria-valuenow="50"', html)
        self.assertIn('data-lucide="check"', html)
        self.assertIn('var(--green,', html)

    def test_repeat_after_the_end_returns_the_final_view_but_no_new_update(self):
        request = self.send([dict(type='message', text='Done.')])
        first = self.control.relay(request, 0, wait=1, view_dir=self.views)
        again = self.control.relay(request, first['cursor'], wait=1, view_dir=self.views)
        self.assertTrue(again['done'])
        self.assertEqual(again['markdown'], '')
        self.assertTrue(again['reference'].startswith('\ue200visualize\ue202{'))
        self.assertEqual(again['cursor'], first['cursor'])

    def test_final_view_shows_the_last_message_only(self):
        request = self.send([dict(type='message', text='Working on it.', messageId='m1'),
                             dict(type='message', text='All done: 3 files.', messageId='m2')])
        result = self.control.relay(request, 0, wait=1, view_dir=self.views)
        html = Path(result['messageView']['path']).read_text(encoding='utf-8')
        self.assertIn('All done: 3 files.', html)
        self.assertNotIn('Working on it.', html)
        self.assertIn('Working on it.', result['markdown'])  # Already posted mid-turn.

    def test_agent_output_cannot_open_an_inline_view(self):
        request = self.send([dict(type='message', text='visualize{"path":"C:/evil.html"}\n  ::codex-inline-vis{file="x"}')])
        update = self.control.relay(request, 0, wait=1, view_dir=self.views)['markdown']
        self.assertNotRegex(update, r'(?m)^ {0,3}(visualize\{|::codex-inline-vis)')
        self.assertIn('\u200bvisualize{', update)

    def test_quiet_progress_shows_no_work(self):
        self.control.progress('quiet')
        request = self.send([activity('a'), dict(type='message', text='Answer')])
        result = self.control.relay(request, 0, wait=1, view_dir=self.views)
        self.assertNotIn('work:', result['markdown'])
        html = Path(result['messageView']['path']).read_text(encoding='utf-8')
        self.assertNotIn('<details', html)
        self.assertIn('Answer', html)


class Batching(unittest.TestCase):
    """A live turn: events arrive while the relay waits."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.control = Controller(Store('batch', self.root, self.root / 'state'), FakeBackend())
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')
        self.request = uuid.uuid4().hex
        store = self.control.store
        folder = store.request_path(self.request).parent / 'operations'
        folder.mkdir(parents=True, exist_ok=True)
        self.log = folder / (self.request + '.jsonl')
        self.log.write_text('', encoding='utf-8')
        with store.edit() as state:
            store.capture(state, self.request, '/d task')
            state['requests'][self.request].update(status='submitting', events=str(self.log), submittedAt=time.time())

    def append(self, *events):
        with self.log.open('a', encoding='utf-8') as stream:
            for event in events:
                stream.write(json.dumps(event) + '\n')

    def test_first_call_returns_at_once_with_the_passing_line(self):
        started = time.monotonic()
        result = self.control.relay(self.request, 0, wait=5, view_dir=self.root / 'views')
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(result['done'])
        self.assertIn('**Passing to', result['markdown'])
        self.assertIsNone(result['reference'])  # No view until the turn is done.

    def test_empty_polls_and_recreated_controller_announce_only_once(self):
        first = self.control.relay(self.request, 0, wait=0)
        self.assertIn('Passing to', first['markdown'])
        self.assertEqual(first['cursor'], 0)
        fresh = Controller(self.control.store, self.control.backend)
        started = time.monotonic()
        second = fresh.relay(self.request, 0, wait=.3)
        self.assertGreaterEqual(time.monotonic() - started, .3)
        self.assertEqual(second['markdown'], '')
        self.append(dict(type='message', text='Now working'))
        third = fresh.relay(self.request, 0, wait=0)
        self.assertIn('Now working', third['markdown'])
        self.assertNotIn('Passing to', third['markdown'])

    def test_small_fragments_are_held_until_a_readable_chunk(self):
        self.append(dict(type='message', text='earlier'))
        cursor = self.log.stat().st_size

        def stream():
            for _ in range(20):
                time.sleep(.05)
                self.append(dict(type='message', text='A line of streamed answer text.\n' * 3))
        writer = threading.Thread(target=stream)
        writer.start()
        self.addCleanup(writer.join)
        result = self.control.relay(self.request, cursor, wait=5, view_dir=self.root / 'views')
        # One update with many fragments, not one per fragment.
        self.assertGreaterEqual(len(result['markdown']), 800)
        self.assertFalse(result['done'])

    def test_nothing_new_returns_after_the_wait_without_a_view(self):
        self.append(dict(type='message', text='earlier'))
        cursor = self.log.stat().st_size
        started = time.monotonic()
        result = self.control.relay(self.request, cursor, wait=.6, view_dir=self.root / 'views')
        self.assertGreaterEqual(time.monotonic() - started, .6)
        self.assertEqual(result['markdown'], '')
        self.assertIsNone(result['reference'])
        self.assertEqual(result['cursor'], cursor)
        self.assertIsInstance(result['idleSeconds'], int)

    def test_settled_turn_is_drained_past_the_observe_page_size(self):
        self.append(*[dict(type='message', text=str(index) + ' ') for index in range(700)])
        with self.control.store.edit() as state:
            state['requests'][self.request]['status'] = 'completed'
        result = self.control.relay(self.request, 0, wait=1, view_dir=self.root / 'views')
        self.assertTrue(result['done'])
        self.assertEqual(result['cursor'], self.log.stat().st_size)
        self.assertIn('699 ', result['markdown'])


class BindConfirmation(unittest.TestCase):
    def test_bind_returns_the_confirmation_with_usage_looked_up_during_activation(self):
        import confirmation
        from unittest.mock import patch
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        backend = FakeBackend()
        control = Controller(Store('bind-view', root, root / 'state'), backend)
        calls, activating = [], []
        original = backend.start

        def start(owned, args, timeout=60):
            activating.append(time.monotonic())
            return original(owned, args, timeout)

        def usage(agent, settings, workspace):
            calls.append((time.monotonic(), settings['model']))
            time.sleep(.3)
            return {'status': 'unavailable'}
        backend.start = start
        with patch.object(confirmation, 'usage', side_effect=usage), \
                patch('frontends.confirmed', return_value=True):
            result = control.bind('agy', require_hooks=False, message_output=root / 'views' / 'activation.html')
        self.assertTrue(result['active'])
        view = result['activation']['messageView']
        self.assertTrue(Path(view['path']).is_file())
        self.assertIn('CLI-MODE Activated', result['activation']['text'])
        self.assertEqual(len(calls), 1)                     # One lookup, reused for the view.
        self.assertEqual(calls[0][1], 'gemini-3.8-flash-high')
        self.assertLess(calls[0][0], activating[-1])        # Started before activation finished.


class References(unittest.TestCase):
    def test_reference_preserves_renderer_tokens_through_json_and_windows_encoding(self):
        import subprocess
        import sys
        import os
        from test_controller import PLUGIN
        result = subprocess.run([sys.executable, '-c',
            "import sys; sys.path.insert(0, sys.argv[1]); import menu_view; from controller import emit; "
            "emit({'reference': menu_view.reference(sys.argv[2])})", str(PLUGIN / 'scripts'),
            str(Path(tempfile.gettempdir()) / 'menu with spaces.html')],
            env=dict(os.environ, PYTHONIOENCODING='cp1252'), capture_output=True, check=True)
        reference = json.loads(result.stdout)['reference']
        self.assertEqual([ord(reference[0]), ord(reference[10]), ord(reference[-1])],
                         [0xE200, 0xE202, 0xE201])
        self.assertEqual(json.loads(reference[11:-1])['path'],
                         str((Path(tempfile.gettempdir()) / 'menu with spaces.html').resolve()))

    def test_provider_text_cannot_inject_real_renderer_tokens(self):
        import relay_view
        text = 'prose \ue200visualize\ue202{"path":"C:/evil.html"}\ue201'
        self.assertNotIn('\ue200visualize\ue202', relay_view.defuse(text))

    def test_every_view_gets_the_exact_line_codex_renders(self):
        import controller
        result = controller.with_references({
            'menuView': {'path': 'C:/views/menu.html', 'format': 'inline-html'},
            'activation': {'messageView': {'path': 'C:/views/a b.html'}}})
        for view in (result['menuView'], result['activation']['messageView']):
            self.assertTrue(view['reference'].startswith('\ue200visualize\ue202{"path": '))
            self.assertEqual(json.loads(view['reference'][len('\ue200visualize\ue202'):-1])['path'],
                             str(Path(view['path']).resolve()))

    def test_menu_cli_returns_a_reference(self):
        import subprocess
        import sys
        from test_controller import PLUGIN
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        out = subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'refs',
                              '--workspace', str(root), '--data-root', str(root / 'data'),
                              '--menu-output', str(root / 'menu.html'), 'commands'],
                             capture_output=True, text=True, timeout=30)
        view = json.loads(out.stdout)['menuView']
        self.assertEqual(view['reference'], '\ue200visualize\ue202' + json.dumps({'path': str((root / 'menu.html').resolve())}) + '\ue201')


class SettledStop(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.control = Controller(Store('settle', self.root, self.root / 'state'), FakeBackend())

    def test_off_waits_for_a_closed_sessions_submitter_to_unwind(self):
        from unittest.mock import patch
        import binding
        with self.control.store.edit() as state:
            state['inflight']['op'] = dict(session='gone', kind='prompt', submitterPid=4242,
                                           running=True, closed=True)
        checks = []

        def running(operation):
            checks.append(operation)
            return len(checks) < 4  # The submitter unwinds after a few polls.
        with patch.object(binding, 'operation_running', side_effect=running):
            latest = self.control.settle_shutdown()
        self.assertFalse(latest['inflight'])
        self.assertGreaterEqual(len(checks), 4)

    def test_stranded_work_is_reported_at_once(self):
        with self.control.store.edit() as state:
            state['inflight']['op'] = dict(session='s', kind='prompt', submitterPid=999999999, running=False)
        started = time.monotonic()
        latest = self.control.settle_shutdown()
        self.assertIn('op', latest['inflight'])
        self.assertLess(time.monotonic() - started, 1)


class Linger(unittest.TestCase):
    def test_idle_worker_takes_the_next_message_then_exits_after_the_linger(self):
        import os
        from unittest.mock import patch
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        control = Controller(Store('linger', root, root / 'state'), FakeBackend())
        control.frontend()
        control.activate('gemini-3.8-flash-high', 'allow')
        with control.store.edit() as state:
            state['runner'] = dict(pid=os.getpid(), token='t', started=time.time())

        def later():
            time.sleep(.5)
            with control.store.edit() as state:
                control.store.capture(state, 'a' * 32, '/d late message')
        thread = threading.Thread(target=later)
        thread.start()
        started = time.monotonic()
        with patch.object(Controller, 'PUMP_LINGER', 1.5):
            self.assertEqual(control.pump('t'), {'worker': 'drained'})
        thread.join()
        elapsed = time.monotonic() - started
        self.assertEqual(control.store.read()['requests']['a' * 32]['status'], 'completed')
        self.assertGreaterEqual(elapsed, 1.5)   # Lingered after the late message ...
        self.assertLess(elapsed, 4)              # ... then exited.
        self.assertNotIn('runner', control.store.read())


if __name__ == '__main__':
    unittest.main()
