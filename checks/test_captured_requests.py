"""Hook-to-provider fidelity and durable submission admission."""
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from test_controller import Controller, FakeBackend, Store, hook


class CapturedRequests(unittest.TestCase):
    def test_state_read_retries_transient_atomic_replace_denial(self):
        original = Path.read_text
        attempts = []
        def transient(path, *args, **kwargs):
            if path == self.store.path and not attempts:
                attempts.append(True)
                raise PermissionError('Windows replace sharing conflict')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', transient):
            self.assertTrue(self.store.read()['active'])
        self.assertEqual(len(attempts), 1)

    def test_state_write_retries_transient_atomic_replace_denial(self):
        import state as state_module
        original = state_module.os.replace
        attempts = []
        def transient(source, destination):
            if destination == self.store.path and not attempts:
                attempts.append(True)
                raise PermissionError('Windows replace sharing conflict')
            return original(source, destination)
        with patch.object(state_module.os, 'replace', transient):
            with self.store.edit() as state:
                state['transientWriteProbe'] = True
        self.assertEqual(len(attempts), 1)
        self.assertTrue(self.store.read()['transientWriteProbe'])

    def test_two_messages_keep_both_captures_in_order(self):
        first, _ = self.capture('/d first long-running request')
        second, _ = self.capture('/d follow-up while the first is running')
        state = self.store.read()
        self.assertEqual(state['requests'][first]['status'], 'captured')
        self.assertEqual(state['requests'][second]['status'], 'captured')
        self.assertTrue(self.store.request_path(first).exists())
        self.assertTrue(self.store.request_path(second).exists())
        with self.assertRaisesRegex(RuntimeError, 'earlier captured request'):
            self.control.send_request(second)
        self.control.send_request(first, output=lambda event: None)
        result = self.control.send_request(second, output=lambda event: None)
        self.assertEqual(self.control.observe(second)['receipt']['status'], 'completed')
        self.assertEqual(self.control.observe(second)['events'][0]['text'], 'follow-up while the first is running')
        self.assertEqual(result['stopReason'], 'end_turn')

    def test_a_request_captured_before_passthrough_was_removed_sends_as_captured(self):
        first, _ = self.capture('/d first')
        second = 'b' * 32
        with self.store.edit() as state:  # Saved by an older version, in Passthrough mode.
            self.store.capture(state, second, 'second')
            state['requests'][second]['routingMode'] = 'passthrough'
        first_events, second_events = [], []
        self.control.send_request(first, output=first_events.append)
        self.control.send_request(second, output=second_events.append)
        self.assertEqual([e['text'] for e in first_events if e['type'] == 'message'], ['first'])
        self.assertEqual([e['text'] for e in second_events if e['type'] == 'message'], ['second'])

    def test_observe_cursor_never_resubmits(self):
        request_id, _ = self.capture('/d one observable turn')
        self.control.send_request(request_id, output=lambda event: None)
        before = len(self.backend.calls)
        first = self.control.observe(request_id, limit=1)
        second = self.control.observe(request_id, cursor=first['cursor'])
        self.assertEqual(first['events'][0]['type'], 'message')
        self.assertEqual(second['events'][-1]['type'], 'done')
        self.assertEqual(self.control.observe(request_id, cursor=second['cursor'])['events'], [])
        self.assertEqual(len(self.backend.calls), before)

    def test_observe_reports_wait_age_without_claiming_provider_progress(self):
        request_id, _ = self.capture('/d a silent turn')
        before = len(self.backend.calls)
        with self.store.edit() as state:
            state['requests'][request_id]['capturedAt'] = time.time() - 70
        queued = self.control.observe(request_id)
        self.assertGreaterEqual(queued['statusAgeSeconds'], 69)
        self.assertIsNone(queued['withoutPublicUpdateSeconds'])
        self.assertEqual(queued['workerState'], 'idle')
        path = self.store.request_path(request_id).parent / 'operations' / (uuid.uuid4().hex + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'message', 'text': 'working'}) + '\n', encoding='utf-8')
        old = time.time() - 45
        os.utime(path, (old, old))
        with self.store.edit() as state:
            state['requests'][request_id].update(status='submitting', submittedAt=time.time() - 90,
                                                 events=str(path))
        observed = self.control.observe(request_id)
        self.assertEqual(observed['events'][0]['text'], 'working')
        self.assertGreaterEqual(observed['statusAgeSeconds'], 89)
        self.assertGreaterEqual(observed['withoutPublicUpdateSeconds'], 44)
        self.assertGreaterEqual(self.control.queue_status()['requests'][-1]['statusAgeSeconds'], 89)
        self.assertEqual(len(self.backend.calls), before)

    def test_dead_worker_blocks_replay_until_explicit_reconciliation(self):
        first, _ = self.capture('/d possibly delivered')
        second, _ = self.capture('/d must wait')
        operation = 'c' * 32
        with self.store.edit() as state:
            state['requests'][first].update(status='submitting', operation=operation,
                                            submitterPid=99999999)
            state['inflight'][operation] = dict(session=state['main'], kind='prompt',
                phase='dispatching', requestId=first, submitterPid=99999999, running=True)
            state['runner'] = dict(pid=99999999, token='d' * 32)
        with patch.dict('os.environ', {'CLI_MODE_TEST_DISABLE_AUTORUN': '0'}):
            result = self.control.ensure_pump()
        self.assertEqual(result['worker'], 'blocked')
        self.assertEqual(self.store.read()['requests'][first]['status'], 'uncertain')
        self.assertEqual(self.store.read()['requests'][second]['status'], 'captured')
        self.assertFalse(self.store.read()['inflight'][operation]['running'])
        with patch.dict('os.environ', {'CLI_MODE_TEST_DISABLE_AUTORUN': '0'}):
            _, context = self.capture('/cli resume')
        self.assertIn('blocked by an unresolved provider turn', str(context))
        self.assertNotIn('relay --request ' + second, str(context))
        self.control.acknowledge(operation)

    def test_queue_limit_returns_local_hint_without_capturing_extra_prompt(self):
        with self.store.edit() as state:
            for _ in range(32):
                self.store.capture(state, uuid.uuid4().hex, 'queued')
        _, result = self.capture('/d private over capacity')
        state = self.store.read()
        self.assertEqual(sum(record['status'] == 'captured' for record in state['requests'].values()), 32)
        self.assertEqual(state['turnRoute']['route'], 'hint')
        self.assertIn('queue is full', str(result))
        self.assertNotIn('private over capacity', str(result))

    def test_queue_controls_do_not_supersede_waiting_request(self):
        request_id, _ = self.capture('/d waiting input')
        _, queue_context = self.capture('/cli queue')
        _, resume_context = self.capture('/cli resume')
        self.assertIn(' queue`', str(queue_context))
        self.assertIn('relay --request ' + request_id, str(resume_context))
        self.assertIn('Do not submit or reconstruct a prompt', str(resume_context))
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'captured')
        self.assertTrue(self.store.request_path(request_id).exists())

    def test_resume_monitors_existing_receipts_without_resubmitting(self):
        from state import route
        first, _ = self.capture('/d first request')
        second, _ = self.capture('/d second request')
        before = len(self.backend.calls)
        with patch.object(self.control, 'ensure_pump', return_value={'worker': 'running'}):
            result = self.control.resume_monitoring()
        self.assertEqual(result['requestIds'], [first, second])
        self.assertEqual(result['statuses'], {first: 'captured', second: 'captured'})
        self.assertEqual(len(self.backend.calls), before)
        for agent in ('agy', 'claude', 'grok-build', 'cursor', 'copilot', 'codex'):
            state = dict(self.store.read(), backend=agent)
            self.assertEqual(route('/cli resume', state), {'route': 'resume'})
            self.assertEqual(route('$CLI RESUME', state), {'route': 'resume'})
        self.control.send_request(first, output=lambda event: None)
        with patch.object(self.control, 'ensure_pump', return_value={'worker': 'running'}):
            result = self.control.resume_monitoring()
        self.assertEqual(result['requestIds'], [second])
        self.assertEqual(result['latestRequestId'], second)
        self.control.send_request(second, output=lambda event: None)
        before = len(self.backend.calls)
        with patch.object(self.control, 'ensure_pump', return_value={'worker': 'idle'}):
            result = self.control.resume_monitoring()
        self.assertEqual(result['requestIds'], [])
        self.assertEqual(result['latestRequestId'], second)
        self.assertEqual(result['latestStatus'], 'completed')
        self.assertEqual(len(self.backend.calls), before)

    def test_resume_reports_worker_launch_failure_without_replaying(self):
        request_id, _ = self.capture('/d waiting request')
        before = len(self.backend.calls)
        with patch.object(Controller, 'ensure_pump', side_effect=OSError('worker launch denied')):
            result = self.control.resume_monitoring()
            _, context = self.capture('/cli resume')
        self.assertEqual(result['worker']['worker'], 'start-failed')
        self.assertTrue(result['blocked'])
        self.assertEqual(result['requestIds'], [request_id])
        self.assertIn('worker launch denied', str(context))
        self.assertIn('Do not replay or submit them', str(context))
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'captured')
        self.assertEqual(len(self.backend.calls), before)

    def test_cancel_when_idle_does_not_affect_queued_followup(self):
        request_id, _ = self.capture('/d waiting')
        with patch.object(self.backend, 'control', side_effect=AssertionError('session-wide cancel')):
            result = self.control.cancel()
        self.assertFalse(result['canceled'])
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'captured')

    def test_observe_keeps_cursor_before_incomplete_event_line(self):
        request_id, _ = self.capture('/d cursor fixture')
        path = self.store.request_path(request_id).parent / 'operations' / (uuid.uuid4().hex + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        first = json.dumps({'type': 'message', 'text': 'first'}).encode() + b'\n'
        second = json.dumps({'type': 'done', 'stopReason': 'end_turn'}).encode() + b'\n'
        path.write_bytes(first + second[:12])
        with self.store.edit() as state:
            state['requests'][request_id]['events'] = str(path)
        observed = self.control.observe(request_id)
        self.assertEqual(observed['cursor'], len(first))
        with path.open('ab') as out:
            out.write(second[12:])
        resumed = self.control.observe(request_id, cursor=observed['cursor'])
        self.assertEqual(resumed['events'][0]['type'], 'done')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store('capture-test', self.root, self.root / 'data')
        self.backend = FakeBackend()
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')

    def capture(self, text):
        context = hook.handle(dict(hook_event_name='UserPromptSubmit', session_id=self.store.thread,
                                   cwd=str(self.root), prompt=text), self.store.root)
        return self.store.read()['turnRoute'].get('requestId'), context

    def test_exact_input_and_completed_receipt_survive_new_turn(self):
        text = '\ufeff  Literal $HOME `code` 🐈\r\n\tkeep\n\n' + 'long prompt ' * 500
        request_id, context = self.capture('/d ' + text)
        self.assertNotIn('long prompt', str(context))
        self.assertNotIn('long prompt', self.store.path.read_text())
        events = []
        result = self.control.send_request(request_id, output=events.append)
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'message'), text)
        self.assertFalse(self.store.request_path(request_id).exists())
        self.capture('/d next prompt')
        before = len(self.backend.calls)
        receipt = Controller(self.store, self.backend).send_request(request_id)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['result'], {k: v for k, v in result.items() if k != 'requestId'})
        self.assertEqual(len(self.backend.calls), before)

    def test_direct_prefix_removed_once_and_no_file_bypass(self):
        text = ' \t$d\r\n /d literal\r\n\t'
        request_id, _ = self.capture(text)
        with self.assertRaisesRegex(RuntimeError, 'captured input'):
            self.control.send('rewritten')
        events = []
        # Prefix fidelity is independent of provider slash-command expansion.
        # Do not launch the installed Antigravity CLI from this offline test.
        with patch('native_commands.name_of', return_value=None):
            self.control.send_request(request_id, output=events.append)
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'message'), ' /d literal\r\n\t')

    def test_local_control_preserves_queued_payload_and_off_discards_it(self):
        request_id, _ = self.capture('/d send after help')
        self.capture('/help')
        self.assertTrue(self.store.request_path(request_id).exists())
        before = len(self.backend.calls)
        self.control.send_request(request_id, output=lambda event: None)
        self.assertGreater(len(self.backend.calls), before)
        self.capture('X')  # Close help before capturing another ordinary task.
        request_id, _ = self.capture('/d also do not send')
        self.control.off()
        self.assertFalse(self.store.request_path(request_id).exists())

    def test_two_submitters_share_one_admission(self):
        request_id, _ = self.capture('/d once')
        admitted, release = threading.Event(), threading.Event()
        real_send = self.control._send
        def delayed(*args, **kwargs):
            admitted.set()
            if not release.wait(5):
                raise RuntimeError('test admission timeout')
            return real_send(*args, **kwargs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(self.control, '_send', side_effect=delayed):
                first = pool.submit(self.control.send_request, request_id, lambda e: None)
                try:
                    self.assertTrue(admitted.wait(5))
                    before = len(self.backend.calls)
                    receipt = Controller(self.store, self.backend).send_request(request_id)
                    self.assertEqual(receipt['status'], 'submitting')
                    self.assertEqual(len(self.backend.calls), before)
                finally:
                    release.set()
                first.result(timeout=10)

    def test_failed_submission_is_not_retried_and_payload_is_removed(self):
        request_id, _ = self.capture('/d uncertain work')
        def lost_after_dispatch(*args, **kwargs):
            with self.store.edit() as state:
                state['inflight'][state['requests'][request_id]['operation']]['phase'] = 'dispatching'
            raise RuntimeError('lost connection')
        with patch.object(self.control, '_send', side_effect=lost_after_dispatch):
            with self.assertRaisesRegex(RuntimeError, 'lost connection'):
                self.control.send_request(request_id)
        self.assertFalse(self.store.request_path(request_id).exists())
        before = len(self.backend.calls)
        self.assertEqual(self.control.send_request(request_id)['status'], 'uncertain')
        following, _ = self.capture('/d new work')
        with self.assertRaisesRegex(RuntimeError, 'pending/uncertain'):
            self.control.send_request(following)
        self.assertEqual(len(self.backend.calls), before)

    def test_invalid_or_cross_conversation_ids_never_read_paths(self):
        request_id, _ = self.capture('/d private')
        other = Controller(Store('other', self.root, self.store.root), self.backend)
        with self.assertRaisesRegex(RuntimeError, 'not found'):
            other.send_request(request_id)
        with self.assertRaises(ValueError):
            self.control.send_request('../payload')

    def test_binding_changes_during_preflight_prevent_dispatch(self):
        request_id, _ = self.capture('/d must not launch')
        original = self.backend.metadata
        def stop_during_metadata(owned):
            self.control.disable()
            return original(owned)
        before = len(self.backend.calls)
        with patch.object(self.backend, 'metadata', side_effect=stop_during_metadata):
            with self.assertRaises(RuntimeError):
                self.control.send_request(request_id)
        self.assertEqual(len(self.backend.calls), before)
        self.assertFalse(self.store.request_path(request_id).exists())

    def test_new_prompt_during_admission_keeps_its_own_route(self):
        first, _ = self.capture('/d first')
        second = []
        original = self.backend.metadata
        def capture_followup(owned):
            if not second:
                second.append(self.capture('/d second')[0])
            return original(owned)
        with patch.object(self.backend, 'metadata', side_effect=capture_followup):
            self.control.send_request(first, output=lambda e: None)
        self.assertEqual(self.store.read()['turnRoute']['requestId'], second[0])
        events = []
        self.control.send_request(second[0], output=events.append)
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'message'), 'second')

    def test_settings_blocked_from_admission_through_final_metadata(self):
        request_id, _ = self.capture('/d keep accepted settings')
        original = self.backend.metadata
        inspections = []
        def inspect(owned):
            inspections.append(owned['settings']['access'])
            for change in (lambda: self.control.tune('access'), self.control.settings_menu,
                           self.control.frontend):
                with self.assertRaisesRegex(RuntimeError, 'Settle current work'):
                    change()
            return original(owned)
        with patch.object(self.backend, 'metadata', side_effect=inspect):
            self.control.send_request(request_id, output=lambda e: None)
        self.assertEqual(inspections, ['allow', 'allow'])
        self.assertEqual(self.store.read()['inflight'], {})
        self.control.tune('access')

    def test_local_rejection_is_terminal_and_does_not_block_followup(self):
        import native_commands
        self.backend.validate_command = lambda owned, text: native_commands.validate(['help'], text, 'fixture')
        request_id, _ = self.capture('/d /unavailable')
        before = len(self.backend.calls)
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
            self.control.send_request(request_id)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'rejected')
        self.assertEqual(self.store.read()['inflight'], {})
        following, _ = self.capture('/d valid next request')
        self.control.send_request(following, output=lambda e: None)

    def test_a_rejected_request_says_why_it_was_not_sent(self):
        import native_commands
        self.backend.validate_command = lambda owned, text: native_commands.validate(['help'], text, 'fixture')
        request_id, _ = self.capture('/d /unavailable')
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable') as raised:
            self.control.send_request(request_id)
        self.assertEqual(self.store.read()['requests'][request_id]['rejectedReason'], str(raised.exception))
        result = self.control.relay(request_id, wait=0, view_dir=self.root / 'views')
        shown = json.dumps(result)
        self.assertIn('Not sent.', shown)
        self.assertIn('Unknown or unavailable', shown)
        self.assertIn('Not sent. Unknown or unavailable', self.control.relay_text(request_id, wait=0)['text'])

    def test_file_cannot_bypass_uncertain_request_after_host_turn(self):
        request_id, _ = self.capture('/d interrupted')
        def interrupted(*args, **kwargs):
            with self.store.edit() as state:
                state['inflight'][state['requests'][request_id]['operation']]['phase'] = 'dispatching'
            raise RuntimeError('interrupted')
        with patch.object(self.control, '_send', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.control.send_request(request_id)
        self.capture('host-only followup')
        with self.assertRaisesRegex(RuntimeError, 'pending/uncertain'):
            self.control.send('/d file bypass')
        operation = self.store.read()['requests'][request_id]['operation']
        self.control.acknowledge(operation)
        self.control.send('/d after explicit reconciliation', output=lambda e: None)

    def test_cancel_during_preflight_never_sends_prompt(self):
        request_id, _ = self.capture('/d cancel before starting')
        original = self.backend.metadata
        def cancel(owned):
            self.assertEqual(self.control.cancel()['method'], 'operation-signal')
            return original(owned)
        before = len(self.backend.calls)
        with patch.object(self.backend, 'metadata', side_effect=cancel):
            with self.assertRaises(RuntimeError):
                self.control.send_request(request_id)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'canceled')
        self.assertFalse(self.store.read()['inflight'])

    def test_failed_capture_commit_removes_payload(self):
        with patch('state.os.replace', side_effect=OSError('checkpoint failed')):
            with self.assertRaisesRegex(OSError, 'checkpoint failed'):
                self.capture('/d must not be orphaned')
        folder = self.store.root / 'requests' / self.store.key
        self.assertEqual(list(folder.glob('*.txt')), [])
        self.assertFalse(self.store.read().get('requests'))

    def test_crash_orphan_is_recovered_without_touching_another_conversation(self):
        orphan = self.store.request_path('a' * 32)
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text('crashed capture')
        other = Store('other-conversation', self.root, self.store.root)
        with other.edit() as state:
            other.capture(state, 'b' * 32, 'other capture')
        self.control.off()
        self.assertFalse(orphan.exists())
        self.assertTrue(other.request_path('b' * 32).exists())

    def test_failed_supersession_preserves_previously_captured_payload(self):
        first, _ = self.capture('/d original')
        with patch('state.os.replace', side_effect=OSError('checkpoint failed')):
            with self.assertRaises(OSError):
                self.capture('/d replacement')
        self.assertEqual(self.store.request_path(first).read_text(), '/d original')
        self.assertEqual(self.store.read()['requests'][first]['status'], 'captured')
        self.assertEqual(len(list(self.store.request_path(first).parent.glob('*.txt'))), 1)

    def test_completed_turn_is_not_uncertain_when_metadata_read_fails(self):
        request_id, _ = self.capture('/d completed task')
        original = self.backend.metadata
        calls = []
        def metadata(owned):
            calls.append(owned['name'])
            if len(calls) == 2:
                raise RuntimeError('inspection failed')
            return original(owned)
        with patch.object(self.backend, 'metadata', side_effect=metadata):
            result = self.control.send_request(request_id, output=lambda e: None)
        self.assertIn('metadataWarning', result)
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'completed')
        self.assertFalse(self.store.read()['inflight'])

    def test_routing_change_after_dispatch_checkpoint_preserves_accepted_prompt(self):
        request_id, _ = self.capture('/d checkpoint race')
        original = self.store.edit
        switched = []
        @contextmanager
        def change_after_checkpoint():
            with original() as state:
                yield state
            if not switched and any(op.get('phase') == 'dispatching' and not op.get('pid')
                                    for op in self.store.read()['inflight'].values()):
                switched.append(True)
                with original() as state:
                    state['routingMode'] = 'direct'
        with patch.object(self.store, 'edit', change_after_checkpoint):
            self.control.send_request(request_id, output=lambda event: None)
        self.assertTrue(switched)
        self.assertEqual(self.store.read()['routingMode'], 'direct')
        self.assertEqual(self.store.read()['requests'][request_id]['status'], 'completed')
        self.assertFalse(self.store.read()['inflight'])

    def test_prompt_spawn_failure_remains_reconcilable(self):
        request_id, _ = self.capture('/d spawn might have started')
        with patch.object(self.backend, 'start', side_effect=OSError('start failed')):
            with self.assertRaises(OSError):
                self.control.send_request(request_id)
        state = self.store.read()
        self.assertEqual(state['requests'][request_id]['status'], 'uncertain')
        operation = state['requests'][request_id]['operation']
        self.assertFalse(state['inflight'][operation]['running'])
        self.control.acknowledge(operation)
        self.assertFalse(self.store.read()['inflight'])

    def test_legacy_uncertain_receipt_gets_an_inspectable_recovery_operation(self):
        request_id, _ = self.capture('/d legacy receipt')
        with self.store.edit() as state:
            state['requests'][request_id]['status'] = 'uncertain'
        recovered = self.store.read()
        self.assertEqual(recovered['requests'][request_id]['operation'], request_id)
        self.assertFalse(recovered['inflight'][request_id]['running'])
        self.control.acknowledge(request_id)
        self.assertFalse(self.store.read()['inflight'])
