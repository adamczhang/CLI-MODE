"""Real pinned ACPX/queue owner with an offline ACP provider, never an account."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import time
import concurrent.futures
from unittest.mock import patch

from test_controller import PLUGIN, Controller, Store, hook
import acpx
import agy
from state import agent_label


def remove_temp(temp, seconds=10):
    """Delete a test's folder, waiting for the processes it started to let go of their files.

    A turn cut short by a failing test leaves the ACPX owner (Node) shutting down with files open, and Windows
    refuses to delete open files: the folder was left in %TEMP% (2026-09-25) and its cleanup error was reported
    on top of the real failure. Retrying while those processes exit removes it; nothing here ever raises.
    """
    deadline = time.monotonic() + seconds
    while True:
        shutil.rmtree(temp.name, ignore_errors=True)
        if not os.path.exists(temp.name) or time.monotonic() >= deadline:
            break
        time.sleep(.25)
    temp.cleanup()  # Nothing left to do; ignore_cleanup_errors keeps a stubborn leftover from raising.


class SharedRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.install = acpx.runtime_install()
        except RuntimeError as exc:
            raise unittest.SkipTest(str(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cli-mode-runtime-', ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.home = self.root / 'home'
        (self.home / '.acpx').mkdir(parents=True)
        fixture = Path(__file__).parent / 'fixtures/acp-agent.mjs'
        (self.home / '.acpx/config.json').write_text(json.dumps({'agents': {
            'fixture': {'argv': [self.install['node'], str(fixture.resolve())]}}}))
        self.environment = patch.dict(os.environ, {'HOME': str(self.home), 'USERPROFILE': str(self.home)})
        self.environment.start()
        self.backend = agy.Backend()
        self.backend.profile = 'fixture'
        self.owned = dict(name='offline-' + uuid.uuid4().hex, workspace=str(self.workspace),
                          settings={'access': 'allow'}, acpxRuntime=self.install)
        try:
            self.backend.control(self.owned, ['sessions', 'ensure', '--name', self.owned['name']])
        except BaseException:
            self.environment.stop()
            remove_temp(self.temp)
            raise

    def tearDown(self):
        try:
            self.backend.close(self.owned)
        finally:
            self.environment.stop()
            remove_temp(self.temp)

    def start(self, text):
        prompt = self.root / 'prompt.txt'
        prompt.write_text(text, encoding='utf-8', newline='')
        self.owned['requestId'] = uuid.uuid4().hex
        return self.backend.start(self.owned, ['-s', self.owned['name'], '--file', str(prompt)], timeout=15)

    def collect(self, process):
        out, err = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, err + out)
        events = [json.loads(line) for line in out.splitlines()]
        self.assertNotIn('PRIVATE FIXTURE REASONING', out)
        for event in events:
            if event['type'] == 'runtime_session':
                self.owned['acpxRecordId'] = event['recordId']
            if event['type'] == 'runtime_result' and event.get('cursor'):
                self.owned['watchCursor'] = event['cursor']
        return events

    def test_separate_clients_preserve_context_exact_input_and_artifacts(self):
        literal = ' \ufeffliteral $HOME `text`\r\n\tend\n'
        first = self.collect(self.start(literal))
        self.assertEqual(''.join(e['text'] for e in first if e['type'] == 'message'), literal)
        self.assertEqual(len([e for e in first if e['type'] == 'artifact']), 1)
        self.assertEqual(first[-1]['result']['status'], 'completed')
        self.assertTrue(first[-1]['outputComplete'])
        record = self.backend.metadata(self.owned)['acpxRecordId']
        second = self.collect(self.start('count'))
        self.assertEqual(''.join(e['text'] for e in second if e['type'] == 'message'), '2')
        self.assertEqual(self.backend.metadata(self.owned)['acpxRecordId'], record)
        self.assertTrue(second[-1]['outputComplete'])

    def test_recoverable_terminal_error_does_not_fail_completed_turn(self):
        events = self.collect(self.start('recover-terminal'))
        self.assertEqual(events[-1]['result']['status'], 'completed')
        self.assertTrue(events[-1]['outputComplete'])
        self.assertEqual([e['text'] for e in events if e['type'] == 'message'],
                         ['Recovered from terminal error.'])
        self.assertFalse(any(e['type'] == 'error' for e in events))

    def test_activity_streams_before_result_and_excludes_private_payloads(self):
        process = self.start('activity')
        prefix = []
        try:
            while True:
                line = process.stdout.readline()
                self.assertTrue(line, 'bridge exited before tool activity')
                event = json.loads(line)
                prefix.append(event)
                self.assertNotEqual(event['type'], 'runtime_result')
                if event['type'] == 'activity':
                    break
            events = prefix + self.collect(process)
            self.assertNotIn('PRIVATE', json.dumps(events))
            tools = [e for e in events if e['type'] == 'activity']
            self.assertEqual(tools[0]['title'], 'Read fixture source')
            self.assertEqual(tools[-2]['status'], 'completed')
            self.assertEqual(tools[-2]['locations'], [{'path': 'src/fixture.js', 'line': 3}])
            self.assertEqual(tools[-1]['kind'], 'execute')
            self.assertNotIn('title', tools[-1])
            self.assertEqual(events[-1]['result']['status'], 'completed')
            self.assertTrue(events[-1]['outputComplete'])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_activity_and_quiet_share_controller_session_and_exactly_one_prompt(self):
        control = Controller(Store('runtime-activity', self.workspace, self.root / 'controller'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            identity = control.store.read()['owned'][0]['providerSession']
            count_before = json.loads((self.workspace / 'fixture-state.json').read_text())[identity]['count']
            output = []
            result = control.send('/d activity', output=output.append)
            saved = [json.loads(line) for line in Path(result['events']).read_text(encoding='utf-8').splitlines()]
            self.assertEqual(saved, output[1:])
            self.assertNotIn('PRIVATE', json.dumps(saved))
            tools = [e for e in saved if e['type'] == 'activity']
            self.assertEqual([e['status'] for e in tools], ['pending', 'in_progress', 'completed', 'failed'])
            usage = [e for e in saved if e['type'] == 'usage']
            self.assertEqual(len(usage), 1)
            self.assertEqual(usage[0]['used'], 2048)
            self.assertEqual(usage[0]['breakdown'], {'inputTokens': 120, 'outputTokens': 40})
            control.progress('quiet')
            output = []
            result = control.send('/d activity', output=output.append)
            self.assertEqual([e['type'] for e in output], ['dispatched', 'message', 'done'])
            self.assertNotIn('activity', Path(result['events']).read_text(encoding='utf-8'))
            self.assertEqual(control.store.read()['owned'][0]['providerSession'], identity)
            self.assertEqual(json.loads((self.workspace / 'fixture-state.json').read_text())[identity]['count'], count_before + 2)
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_hook_worker_queues_steering_without_host_turn_lifetime(self):
        control = Controller(Store('runtime-worker', self.workspace, self.root / 'worker'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            identity = control.store.read()['owned'][0]['providerSession']
            count_before = json.loads((self.workspace / 'fixture-state.json').read_text())[identity]['count']
            def submit(text):
                event = dict(hook_event_name='UserPromptSubmit', session_id=control.store.thread,
                             cwd=str(self.workspace), prompt=text)
                result = subprocess.run([sys.executable, str(PLUGIN / 'hooks/route.py')],
                    input=json.dumps(event), capture_output=True, text=True, timeout=5,
                    env=dict(os.environ, CLI_MODE_DATA=str(control.store.root)))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn(text, result.stdout)
                return control.store.read()['turnRoute']['requestId']
            with patch.dict(os.environ, {'CLI_MODE_TEST_DISABLE_AUTORUN': '0'}):
                started = time.monotonic()
                first = submit('/d slow')
                self.assertLess(time.monotonic() - started, 1.5)
                second = submit('/d count')
                self.assertEqual(control.store.read()['requests'][second]['status'], 'captured')
                until = time.monotonic() + 25
                while time.monotonic() < until:
                    state = control.store.read()
                    if all(state['requests'][key]['status'] == 'completed' for key in (first, second)):
                        break
                    time.sleep(.1)
                else:
                    self.fail('detached worker did not drain the queued ACPX turns: ' + str(control.queue_status()))
            self.assertEqual(control.observe(first)['events'][0]['text'], 'slow completed')
            self.assertEqual(control.observe(second)['events'][0]['text'], str(count_before + 2))
            self.assertEqual(control.store.read()['owned'][0]['providerSession'], identity)
            self.assertEqual(json.loads((self.workspace / 'fixture-state.json').read_text())[identity]['count'], count_before + 2)
        finally:
            control.off()

    def test_explicit_cancel_settles_active_turn_then_drains_followup(self):
        control = Controller(Store('runtime-cancel-worker', self.workspace, self.root / 'cancel-worker'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            identity = control.store.read()['owned'][0]['providerSession']
            count_before = json.loads((self.workspace / 'fixture-state.json').read_text())[identity]['count']
            def submit(text):
                hook.handle(dict(hook_event_name='UserPromptSubmit', session_id=control.store.thread,
                                 cwd=str(self.workspace), prompt=text), control.store.root)
                return control.store.read()['turnRoute'].get('requestId')
            with patch.dict(os.environ, {'CLI_MODE_TEST_DISABLE_AUTORUN': '0'}):
                first = submit('/d hold')
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    state = control.store.read()
                    count = json.loads((self.workspace / 'fixture-state.json').read_text())[identity]['count']
                    if state['requests'][first]['status'] == 'submitting' and count == count_before + 1:
                        break
                    time.sleep(.1)
                else:
                    self.fail('hold turn did not start')
                second = submit('/d count')
                self.assertEqual(control.store.read()['requests'][first]['status'], 'submitting')
                self.assertEqual(control.store.read()['requests'][second]['status'], 'captured')
                submit('/cli cancel')
                while time.monotonic() < until:
                    state = control.store.read()
                    if state['requests'][first]['status'] == 'canceled' and state['requests'][second]['status'] == 'completed':
                        break
                    time.sleep(.1)
                else:
                    self.fail('cancellation did not release queued follow-up: ' + str(control.queue_status()))
            self.assertEqual(control.observe(second)['events'][0]['text'], str(count_before + 2))
        finally:
            control.off()

    def test_missing_provider_session_does_not_create_a_fresh_conversation(self):
        (self.workspace / 'reject-load').touch()
        before = json.loads((self.workspace / 'fixture-state.json').read_text())
        events = self.collect(self.start('must not dispatch'))
        self.assertEqual(events[-1]['result']['status'], 'failed')
        self.assertEqual(json.loads((self.workspace / 'fixture-state.json').read_text()), before)
        self.assertFalse(any(e['type'] in ('message', 'artifact') for e in events))

    def test_dependency_change_is_rejected_without_using_path_fallback(self):
        wrong = self.root / 'wrong-package'
        wrong.mkdir()
        (wrong / 'package.json').write_text('{"name":"acpx","version":"0.19.0"}')
        with self.assertRaisesRegex(RuntimeError, 'requires ACPX 0.18.0'):
            acpx.runtime_install(dict(self.install, package=str(wrong)))

    def test_cancel_waits_for_terminal_settlement(self):
        process = self.start('hold')
        try:
            seen = []
            while True:
                line = process.stdout.readline()
                self.assertTrue(line, 'bridge exited before accepting the prompt')
                event = json.loads(line)
                seen.append(event)
                if event['type'] == 'prompt_started':
                    break
            self.backend.control(self.owned, ['cancel', '-s', self.owned['name']])
            events = self.collect(process)
            self.assertEqual(events[-1]['result']['status'], 'cancelled')
            self.assertTrue(events[-1]['settled'])
        finally:
            if process.poll() is None:
                self.backend.close(self.owned)
                process.kill()
                process.wait(timeout=5)

    def test_permission_denial_does_not_poison_later_allowed_turn(self):
        self.owned['settings']['access'] = 'prompt'
        first = self.collect(self.start('request-permission'))
        self.assertEqual(first[-1]['result']['status'], 'failed')
        self.owned['settings']['access'] = 'allow'
        second = self.collect(self.start('after denial'))
        self.assertEqual(second[-1]['result']['status'], 'completed')
        self.assertTrue(second[-1]['outputComplete'])

    def test_controller_activation_modes_and_reconfiguration_share_one_session(self):
        control = Controller(Store('runtime-controller', self.workspace, self.root / 'controller'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            state = control.store.read()
            self.assertEqual(state['routingMode'], 'direct')
            identity = state['owned'][0]['providerSession']
            direct = []
            control.send('/d recover-terminal', output=direct.append)
            self.assertEqual([e['text'] for e in direct if e['type'] == 'message'], ['Recovered from terminal error.'])
            control.send('/d ordinary followup', output=lambda e: None)
            control.tune('model')
            control.activate('gemini-3.8-flash-low', 'prompt')
            state = control.store.read()
            self.assertEqual(state['owned'][0]['providerSession'], identity)
            self.assertEqual(state['settings']['access'], 'prompt')
            self.assertEqual(state['settings']['model'], 'gemini-3.8-flash-low')
            self.assertFalse(state['inflight'])
            self.assertEqual(state['owned'][0]['acpxRuntime'], self.install)
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_permission_stop_explains_access_and_keeps_the_queue_usable(self):
        control = Controller(Store('runtime-permission', self.workspace, self.root / 'permission'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'prompt')
            events = []
            with self.assertRaises(RuntimeError):
                control.send('/d request-permission', output=events.append)
            errors = [event for event in events if event['type'] == 'error']
            self.assertEqual([event.get('code') for event in errors], ['PERMISSION_PROMPT_UNAVAILABLE'])
            # The agent's own request is the question: its kind and title, and how to answer.
            self.assertIn(agent_label(control.store.read()) + ' asks to edit files: Edit fixture',
                          errors[0]['message'])
            self.assertIn('/cli approve', errors[0]['message'])
            state = control.store.read()
            self.assertEqual([record['status'] for record in state['requests'].values()], ['failed'])
            self.assertEqual({key: state['owned'][0]['approval'][key] for key in ('kind', 'title')},
                             {'kind': 'edit', 'title': 'Edit fixture'})
            self.assertFalse(state['inflight'])
            after = []
            control.send('/d after denial', output=after.append)
            self.assertEqual([e['text'] for e in after if e['type'] == 'message'], ['after denial'])
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_an_approved_kind_is_allowed_by_acpx_on_the_next_prompt(self):
        control = Controller(Store('runtime-approve', self.workspace, self.root / 'approve'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'prompt')
            with control.store.edit() as state:
                state['owned'][0]['approveAlways'] = ['edit']  # What /cli approve always keeps.
            events = []
            control.send('/d request-permission', output=events.append)
            answer = ''.join(event['text'] for event in events if event['type'] == 'message')
            self.assertEqual(json.loads(answer), {'outcome': {'outcome': 'selected', 'optionId': 'allow'}})
            self.assertFalse([event for event in events if event['type'] == 'error'])
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_a_kind_the_approval_does_not_cover_stops_and_asks_again(self):
        control = Controller(Store('runtime-escalate', self.workspace, self.root / 'escalate'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'prompt')
            with control.store.edit() as state:
                state['owned'][0]['approveAlways'] = ['execute']  # Commands approved; the fixture asks to edit.
            events = []
            with self.assertRaises(RuntimeError):
                control.send('/d request-permission', output=events.append)
            errors = [event for event in events if event['type'] == 'error']
            self.assertEqual([event.get('code') for event in errors], ['PERMISSION_PROMPT_UNAVAILABLE'])
            self.assertIn(' asks to edit files: Edit fixture', errors[0]['message'])
            self.assertEqual(control.store.read()['owned'][0]['approval']['kind'], 'edit')
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def held_bootstrap(self, name):
        """Start activation whose first (ACPX CLI) readiness probe the fixture holds."""
        (self.workspace / 'hold-readiness').touch()
        control = Controller(Store(name, self.workspace, self.root / name), self.backend)
        control.frontend()
        pool = concurrent.futures.ThreadPoolExecutor(1)
        self.addCleanup(pool.shutdown)
        activation = pool.submit(control.activate, 'gemini-3.8-flash-high', 'allow')
        until = time.monotonic() + 20
        while time.monotonic() < until:
            entries = list(control.store.read()['inflight'].values())
            if entries and entries[0].get('origin') == 'readiness' and entries[0].get('pid'):
                break
            time.sleep(.05)
        else:
            self.fail('bootstrap readiness never started')
        time.sleep(.5)  # Let the ACPX CLI submit the probe to the owner.
        return control, activation

    def test_bootstrap_probe_honors_the_bridge_cancel_signal(self):
        control, activation = self.held_bootstrap('bootstrap-cancel')
        try:
            started = time.monotonic()
            with control.store.edit() as state:
                control.store.signal_cancel(state)
            with self.assertRaises(RuntimeError):
                activation.result(timeout=20)
            self.assertLess(time.monotonic() - started, 15)
            self.assertFalse(control.store.read()['active'])
        finally:
            (self.workspace / 'hold-readiness').unlink(missing_ok=True)
            self.assertTrue(control.off()['shutdownComplete'])

    def test_off_during_bootstrap_probe_shuts_down_cleanly(self):
        control, activation = self.held_bootstrap('bootstrap-off')
        (self.workspace / 'hold-readiness').unlink()
        result = control.off()
        with self.assertRaises(RuntimeError):
            activation.result(timeout=20)
        latest = control.store.read()
        self.assertTrue(result['shutdownComplete'] or not latest['owned'], result)
        self.assertFalse(latest['active'])
        self.assertFalse(latest['owned'])

    @unittest.skipUnless(os.name == 'nt', 'Windows job objects')
    def test_killed_submitter_ends_its_bridge_and_leaves_the_turn_with_the_owner(self):
        from controller import operation_running
        control = Controller(Store('killed-submitter', self.workspace, self.root / 'killed'), self.backend)
        control.frontend()
        control.activate('gemini-3.8-flash-high', 'allow')
        owned = control.store.read()['owned'][0]
        prompt = self.root / 'hold.txt'
        prompt.write_text('/d hold', encoding='utf-8')
        submitter = subprocess.Popen([sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'killed-submitter',
                                      '--workspace', str(self.workspace), '--data-root', str(self.root / 'killed'),
                                      'send', '--file', str(prompt)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            until = time.monotonic() + 30
            while time.monotonic() < until:
                entry = next(iter(control.store.read()['inflight'].values()), {})
                if entry.get('phase') == 'started':
                    break
                time.sleep(.05)
            else:
                self.fail('the held turn never started')
            bridge = entry['pid']
            submitter.kill()
            submitter.wait(timeout=10)
            until = time.monotonic() + 10
            while operation_running({'pid': bridge}) and time.monotonic() < until:
                time.sleep(.05)
            self.assertFalse(operation_running({'pid': bridge}), 'bridge outlived its submitter')
            # Detaching never cancels: the owner keeps the admitted turn.
            cancels = lambda: sum(item.get('cancels', 0) for item in json.loads(
                (self.workspace / 'fixture-state.json').read_text()).values())
            time.sleep(1)
            self.assertEqual(cancels(), 0)
            self.assertEqual(self.backend.control(owned, ['status', '-s', owned['name']])['status'], 'alive')
            self.assertEqual(control.ensure_pump()['worker'], 'disabled-for-tests'
                             if os.environ.get('CLI_MODE_TEST_DISABLE_AUTORUN') == '1' else 'blocked')
        finally:
            if submitter.poll() is None:
                submitter.kill()
                submitter.wait(timeout=10)
            self.backend.control(owned, ['cancel', '-s', owned['name']])
            # The probe does see a real cancel (the fixture writes it asynchronously).
            until = time.monotonic() + 10
            seen = None
            while time.monotonic() < until:
                try:
                    seen = cancels()
                except ValueError:
                    seen = None  # Read mid-write.
                if seen == 1:
                    break
                time.sleep(.05)
            self.assertEqual(seen, 1)
            self.assertTrue(control.off()['shutdownComplete'])

    def test_close_uses_the_warm_bridge_without_acpx_processes(self):
        self.collect(self.start('count'))  # Warms this process's bridge.
        with patch.object(self.backend, 'spawn', side_effect=AssertionError('ACPX CLI launched')):
            self.backend.close(self.owned)
        self.assertEqual(self.backend.control(self.owned, ['status', '-s', self.owned['name']])['status'], 'no-session')

    @staticmethod
    def backend_label(control):
        return control.adapter.LABEL

    def wait_idle(self, owned):
        until = time.monotonic() + 15
        while time.monotonic() < until:
            status = self.backend.control(owned, ['status', '-s', owned['name']])
            if status['status'] in ('idle', 'dead'):
                return
            time.sleep(.05)
        self.fail('fixture owner did not expire')

    def test_cold_reconnect_keeps_conversation(self):
        self.backend.owner_ttl = 1
        self.collect(self.start('count'))
        before = self.backend.metadata(self.owned)['acpSessionId']
        self.wait_idle(self.owned)
        events = self.collect(self.start('count'))
        self.assertEqual([e['text'] for e in events if e['type'] == 'message'], ['2'])
        self.assertEqual(self.backend.metadata(self.owned)['acpSessionId'], before)

    def prompts_seen(self):
        return sum(session['count'] for session in
                   json.loads((self.workspace / 'fixture-state.json').read_text()).values())

    def test_a_setting_change_reaches_a_running_owner_without_a_prompt(self):
        control = Controller(Store('warm-settings', self.workspace, self.root / 'controller'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            before = self.prompts_seen()
            control.tune('model')
            control.activate('gemini-3.8-flash-low', 'allow')
            self.assertEqual(self.prompts_seen(), before)  # The owner was running: no wake turn.
            state = control.store.read()
            self.assertTrue(state['active'])
            self.assertEqual(state['settings']['model'], 'gemini-3.8-flash-low')
            self.assertFalse(state['inflight'])
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_an_idle_owner_is_woken_by_one_prompt_then_configured(self):
        self.backend.owner_ttl = 5  # As below: let the first-turn bootstrap settle, then expire.
        control = Controller(Store('idle-settings', self.workspace, self.root / 'controller'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            self.wait_idle(control.store.read()['owned'][0])
            before = self.prompts_seen()
            control.tune('model')
            control.activate('gemini-3.8-flash-low', 'allow')
            # ACPX refused the first setting unsent (no owner), so one readiness turn woke it.
            self.assertEqual(self.prompts_seen(), before + 1)
            state = control.store.read()
            self.assertTrue(state['active'])
            self.assertEqual(state['settings']['model'], 'gemini-3.8-flash-low')
            self.assertFalse(state['inflight'])  # The refusal left nothing uncertain.
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_cold_reconfiguration_refuses_a_missing_provider_conversation(self):
        # Let the first-turn ACPX bootstrap settle before inducing idle expiry.
        self.backend.owner_ttl = 5
        control = Controller(Store('cold-settings', self.workspace, self.root / 'controller'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            self.wait_idle(control.store.read()['owned'][0])
            before = json.loads((self.workspace / 'fixture-state.json').read_text())
            (self.workspace / 'reject-load').touch()
            control.tune('model')
            with self.assertRaises(RuntimeError):
                control.activate('gemini-3.8-flash-low', 'allow')
            self.assertEqual(json.loads((self.workspace / 'fixture-state.json').read_text()), before)
            self.assertFalse(control.store.read()['active'])
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_invalid_cursor_restarts_observation_without_replaying_work(self):
        self.collect(self.start('count'))
        self.owned['watchCursor'] = 'invalid-cursor'
        events = self.collect(self.start('count'))
        self.assertEqual([e['text'] for e in events if e['type'] == 'message'], ['2'])
        self.assertTrue(events[-1]['outputComplete'])
        self.assertNotEqual(self.owned['watchCursor'], 'invalid-cursor')

    def test_owner_loss_never_claims_settlement(self):
        events = self.collect(self.start('lose-owner'))
        self.assertEqual(events[-1]['result']['status'], 'failed')
        self.assertFalse(events[-1]['settled'])
        self.assertFalse(events[-1]['outputComplete'])

    def test_cancel_after_spawn_before_bridge_submission_is_not_lost(self):
        self.assert_cancel_before_submission('prompt')

    def test_cancel_after_control_spawn_before_owner_submission_is_not_lost(self):
        self.assert_cancel_before_submission('control')

    def assert_cancel_before_submission(self, action):
        control = Controller(Store('cancel-preflight', self.workspace, self.root / 'controller'), self.backend)
        try:
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            before = json.loads((self.workspace / 'fixture-state.json').read_text())
            gate = self.root / 'gate'
            release = self.root / 'release'
            preload = self.root / 'delay.mjs'
            preload.write_text(
                "import {existsSync,writeFileSync} from 'node:fs';import {setTimeout as delay} from 'node:timers/promises';"
                "if(process.argv[1]?.endsWith('acpx-runtime.mjs')){writeFileSync(" + json.dumps(str(gate)) + ",'ready');"
                "while(!existsSync(" + json.dumps(str(release)) + "))await delay(10);}", encoding='utf-8')
            with patch.dict(os.environ, {'NODE_OPTIONS': '--import=' + preload.as_uri()}), concurrent.futures.ThreadPoolExecutor() as pool:
                if action == 'prompt':
                    pending = pool.submit(control.send, '/d must not execute', lambda e: None)
                else:
                    state = control.store.read()
                    owned = state['owned'][0]
                    pending = pool.submit(control.control, owned,
                        ['-s', owned['name'], 'set', 'model', 'gemini-3.8-flash-low'], state['generation'])
                try:
                    until = time.monotonic() + 10
                    while not gate.exists() and time.monotonic() < until:
                        time.sleep(.02)
                    self.assertTrue(gate.exists(), 'bridge did not reach gated preflight')
                    control.cancel()
                finally:
                    release.touch()
                with self.assertRaises(RuntimeError):
                    pending.result(timeout=20)
            self.assertEqual(json.loads((self.workspace / 'fixture-state.json').read_text()), before)
            if action == 'prompt':
                self.assertFalse(control.store.read()['inflight'])
                self.assertEqual(next(iter(control.store.read()['requests'].values()))['status'], 'canceled')
            else:
                # A control bridge failure does not prove whether the owner
                # accepted it. Retain custody until explicit close/reconciliation.
                entry = next(iter(control.store.read()['inflight'].values()))
                self.assertFalse(entry['running'])
                self.assertTrue(entry['uncertain'])
        finally:
            self.assertTrue(control.off()['shutdownComplete'])

    def test_unsupported_mcp_configuration_never_silently_loses_tools(self):
        (self.workspace / '.acpxrc.json').write_text(json.dumps({'mcpServers': [
            {'name': 'offline', 'command': self.install['node'], 'args': ['-e', 'process.exit(0)'], 'env': []}]}))
        before = json.loads((self.workspace / 'fixture-state.json').read_text())
        process = self.start('must not run without tools')
        out, _ = process.communicate(timeout=20)
        event = json.loads(out.strip())
        self.assertEqual(event['type'], 'runtime_failure')
        self.assertFalse(event['dispatchStarted'])
        self.assertIn('mcpServers', event['message'])
        self.assertEqual(json.loads((self.workspace / 'fixture-state.json').read_text()), before)


if __name__ == '__main__':
    unittest.main()
