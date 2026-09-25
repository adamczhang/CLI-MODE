"""Public progress filtering, stream assembly and real-pipe controller coverage."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, runtime_process, runtime_result
import acpx
import agy
from controller import Controller
from state import Store


def update(kind, **fields):
    return dict(params=dict(update=dict(sessionUpdate=kind, **fields)))


class Progress(unittest.TestCase):
    def test_only_known_commands_can_complete_without_public_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = FakeBackend()
            control = Controller(Store('empty-command', root, root / 'state'), backend)
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            owned = control.store.read()['owned'][0]
            owned['advertisedCommands'] = ['context']
            generation = control.store.read()['generation']
            def start(*args, **kwargs):
                return runtime_process([runtime_result()])
            with patch.object(backend, 'start', side_effect=start):
                result = control.prompt(owned, '/context', generation, output=lambda event: None)
                self.assertTrue(result['noPublicOutput'])
                for text in ('research something', '/unknown', 'Confirm readiness CLI_MODE_READY_123'):
                    with self.assertRaises(RuntimeError):
                        control.prompt(owned, text, generation, output=lambda event: None)

    def test_canonical_failure_is_not_overridden_by_public_success_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = FakeBackend()
            control = Controller(Store('canonical-failure', root, root / 'state'), backend)
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            events = [dict(type='message', text='Looks successful'),
                      dict(type='runtime_result', result=dict(status='failed', error=dict(message='Checkpoint failed')),
                           settled=True, outputComplete=True)]
            with patch.object(backend, 'start', side_effect=lambda *a, **kw: runtime_process(events)):
                with self.assertRaises(RuntimeError):
                    control.send('/d task', output=lambda e: None)
            self.assertFalse(control.store.read()['inflight'])
            self.assertEqual(next(iter(control.store.read()['requests'].values()))['status'], 'failed')

    def test_successful_work_with_incomplete_output_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = FakeBackend()
            control = Controller(Store('incomplete-output', root, root / 'state'), backend)
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            outcome = runtime_result()
            outcome['outputComplete'] = False
            events = [dict(type='message', text='Partial output'), outcome]
            with patch.object(backend, 'start', side_effect=lambda *a, **kw: runtime_process(events)):
                with self.assertRaises(RuntimeError):
                    control.send('/d task', output=lambda e: None)
            self.assertFalse(control.store.read()['inflight'])
            self.assertEqual(next(iter(control.store.read()['requests'].values()))['status'], 'failed')

    def test_tool_and_thought_events_are_excluded(self):
        for kind in ('agent_thought_chunk', 'tool_call', 'tool_call_update'):
            self.assertIsNone(agy.public_event(update(kind, title='hidden',
                content=dict(type='text', text='hidden'), status='completed')))

    def test_plans_are_validated_and_only_public_fields_retained(self):
        event = update('plan', entries=[dict(content='Build engine', status='in_progress', rawInput='hidden')])
        self.assertEqual(agy.public_event(event), dict(type='plan', entries=[dict(content='Build engine', status='in_progress')]))
        for entries in (None, {}, [None], [dict(content=42, status='pending')], [dict(content='x', status='unknown')]):
            self.assertIsNone(agy.public_event(update('plan', entries=entries)))
        self.assertEqual(agy.public_event(update('plan', entries=[])), dict(type='plan', entries=[]))

    def test_repeated_snapshots_suppressed_but_changes_and_clear_retained(self):
        relay = agy.PublicRelay()
        pending = dict(type='plan', entries=[dict(content='Build engine', status='pending')])
        completed = dict(type='plan', entries=[dict(content='Build engine', status='completed')])
        self.assertEqual(relay.feed(pending), [pending])
        self.assertEqual(relay.feed(pending), [])
        self.assertEqual(relay.feed(completed), [completed])
        self.assertEqual(relay.feed(dict(type='plan', entries=[])), [dict(type='plan', entries=[])])

    def test_text_coalesces_without_altering_repeated_text_or_message_boundaries(self):
        relay = agy.PublicRelay()
        first = dict(type='message', text='Hello ', messageId='a')
        self.assertEqual(relay.feed(first), [])
        self.assertEqual(relay.feed(first), [])
        self.assertEqual(relay.feed(dict(type='message', text='Next', messageId='b')),
                         [dict(type='message', text='Hello Hello ', messageId='a')])
        done = dict(type='done', stopReason='end_turn')
        self.assertEqual(relay.feed(done), [dict(type='message', text='Next', messageId='b'), done])

    def test_partial_text_flushes_during_silence_and_at_eof(self):
        relay = agy.PublicRelay()
        with patch.object(acpx.time, 'monotonic', return_value=10):
            self.assertEqual(relay.feed(dict(type='message', text='Working')), [])
        with patch.object(acpx.time, 'monotonic', return_value=11.1):
            self.assertEqual(relay.due(), [dict(type='message', text='Working')])
        relay.feed(dict(type='message', text=' tail'))
        self.assertEqual(relay.flush(), [dict(type='message', text=' tail')])
        self.assertEqual(relay.flush(), [])

    def test_artifacts_errors_and_newlines_keep_order(self):
        relay = agy.PublicRelay()
        message = dict(type='message', text='  keep\r\nspacing\n')
        self.assertEqual(relay.feed(message), [])  # A short line waits for more text.
        relay.feed(dict(type='message', text='Before image'))
        artifact = dict(type='artifact', content=dict(type='image', data='image-data'))
        self.assertEqual(relay.feed(artifact),
                         [dict(type='message', text='  keep\r\nspacing\nBefore image'), artifact])
        error = dict(type='error', message='Provider failed')
        self.assertEqual(relay.feed(error), [error])

    def test_text_flushes_at_a_line_end_once_a_paragraph_has_built_up(self):
        relay = agy.PublicRelay()
        lines = ['Line %02d of a long answer.\n' % index for index in range(40)]
        flushed = [event for line in lines for event in relay.feed(dict(type='message', text=line))]
        self.assertTrue(1 <= len(flushed) <= 3, len(flushed))
        self.assertTrue(all(len(event['text']) >= agy.PublicRelay.MIN_CHUNK for event in flushed))
        self.assertEqual(''.join(e['text'] for e in flushed + relay.flush()), ''.join(lines))

    def test_filtered_progress_reaches_output_and_log_through_real_pipes(self):
        class Backend(FakeBackend):
            events = None
            def start(self, owned, args, timeout=60):
                if '--file' not in args or self.events is None:
                    return super().start(owned, args, timeout)
                return runtime_process(self.events)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = Backend()
            control = Controller(Store('progress', root, root / 'state'), backend)
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            plan = dict(type='plan', entries=[dict(content='Inspect project', status='in_progress')])
            backend.events = [plan, plan,
                dict(type='agent_thought_chunk', text='PRIVATE'),
                dict(type='tool_call', title='TOOL TITLE'),
                dict(type='message', messageId='a', text='I will '),
                dict(type='message', messageId='a', text='check it.'), runtime_result()]
            output = []
            result = control.send('/d exact request', output=output.append)
            saved = [json.loads(line) for line in Path(result['events']).read_text(encoding='utf-8').splitlines()]
            self.assertEqual(saved, output[1:])  # Dispatch metadata is not provider progress.
            self.assertEqual([e['type'] for e in saved], ['plan', 'message', 'done'])
            self.assertEqual(saved[1]['text'], 'I will check it.')
            self.assertNotIn('PRIVATE', json.dumps(saved))
            self.assertNotIn('TOOL TITLE', json.dumps(saved))


if __name__ == '__main__': unittest.main()
