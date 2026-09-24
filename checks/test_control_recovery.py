"""Durable control ownership across checkpoints, cancellation and lost replies."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import Controller, FakeBackend, Store


class ControlRecovery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store('control-recovery', self.root, self.root / 'data')
        self.backend = FakeBackend()
        self.controller = Controller(self.store, self.backend)
        self.controller.frontend()
        self.state = self.controller.activate('gemini-3.8-flash-high', 'allow')
        self.owned = self.state['owned'][0]
        self.args = ['-s', self.owned['name'], 'set', 'model', 'fixture-new-model']

    def run_control(self):
        return self.controller.control(self.owned, self.args, self.state['generation'])

    def test_admission_checkpoint_failure_never_launches(self):
        before = len(self.backend.calls)
        with patch('state.os.replace', side_effect=OSError('checkpoint failed')):
            with self.assertRaisesRegex(OSError, 'checkpoint failed'):
                self.run_control()
        self.assertEqual(len(self.backend.calls), before)
        self.assertFalse(self.store.read()['inflight'])

    def test_post_spawn_checkpoint_failure_retains_custody_and_stops_submitter(self):
        replace, start = os.replace, self.backend.start
        checkpoints, children = [], []
        def fail_second(source, target):
            checkpoints.append(target)
            if len(checkpoints) == 2:
                raise OSError('post-spawn checkpoint failed')
            replace(source, target)
        def launched(*args, **kwargs):
            start(*args, **kwargs)  # Provider-side change happens before the reply.
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            children.append(process)
            return process
        with patch('state.os.replace', side_effect=fail_second), patch.object(self.backend, 'start', side_effect=launched):
            with self.assertRaisesRegex(OSError, 'post-spawn checkpoint failed'):
                self.run_control()
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(self.backend.records[self.owned['name']]['acpx']['current_model_id'], 'fixture-new-model')
        operation, entry = next(iter(self.store.read()['inflight'].items()))
        self.assertFalse(entry['running'])
        self.assertTrue(entry['uncertain'])
        with self.assertRaisesRegex(RuntimeError, 'pending/uncertain'):
            self.run_control()
        self.controller.acknowledge(operation)
        self.assertFalse(self.store.read()['inflight'])

    def test_lost_reply_blocks_reconfiguration_until_reconciled(self):
        self.backend.fail = True
        with self.assertRaisesRegex(RuntimeError, 'auth failed'):
            self.run_control()
        self.assertEqual(self.backend.records[self.owned['name']]['acpx']['current_model_id'], 'fixture-new-model')
        entry = next(iter(self.store.read()['inflight'].values()))
        self.assertEqual(entry['control'], self.args)
        self.assertFalse(entry['running'])
        with self.assertRaisesRegex(RuntimeError, 'Settle current work'):
            self.controller.tune('model')
        self.assertTrue(self.controller.off()['shutdownComplete'])

    def test_cancel_between_checkpoint_and_spawn_prevents_control(self):
        edit = self.store.edit
        signaled = False
        @contextmanager
        def cancel_after_admission():
            nonlocal signaled
            with edit() as state:
                yield state
            if not signaled and any(op['kind'] == 'control' for op in self.store.read()['inflight'].values()):
                signaled = True
                self.controller.cancel()
        before = len(self.backend.calls)
        with patch.object(self.store, 'edit', cancel_after_admission):
            with self.assertRaisesRegex(RuntimeError, 'canceled before dispatch'):
                self.run_control()
        self.assertTrue(signaled)
        self.assertEqual(len(self.backend.calls), before)
        self.assertFalse(self.store.read()['inflight'])
        self.assertFalse(list((self.store.root / 'requests').rglob('*.cancel')))

    def test_control_admission_blocks_another_control_until_settlement(self):
        def contender():
            with self.assertRaisesRegex(RuntimeError, 'pending/uncertain'):
                self.run_control()
        self.backend.after_start = contender
        self.run_control()
        self.assertFalse(self.store.read()['inflight'])

    def test_off_retains_live_control_until_its_submitter_unwinds(self):
        outcomes = []
        self.backend.after_start = lambda: outcomes.append(self.controller.off())
        self.run_control()
        self.assertFalse(outcomes[0]['shutdownComplete'])
        self.assertTrue(self.controller.off()['shutdownComplete'])
        self.assertFalse(self.store.read()['active'])


if __name__ == '__main__':
    unittest.main()
