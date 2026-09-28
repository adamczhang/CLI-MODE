"""The live harness's own checks, on recorded-shape event logs (no Claude Code, no quota)."""
import unittest

import test_controller  # noqa: F401 (puts the plugin's scripts on the path, as for every check)
from claude_background_live import folded_answers
from presentation import plain_strong, strong


def says(label, words):
    return strong(label + ' says...', True) + '\n\n' + words


def turn(*texts):
    """A turn as Claude Code's stream-json reports it: assistant text blocks, then its result."""
    return [dict(type='assistant', message=dict(content=[dict(type='text', text=text)])) for text in texts] + [
        dict(type='result', result=texts[-1] if texts else '')]


class FoldedAnswers(unittest.TestCase):
    def test_answers_in_the_last_message_of_their_turn_pass(self):
        # Live, 2026-09-25 (after the fix): Codex finished mid-turn and the last message carried both answers.
        events = turn(says('Grok GRO-HF', 'PERIWINKLE') + '\n\n' + says('Codex COD-KM', 'PERIWINKLE'))
        events += turn(says('Codex COD-KM', 'done'))
        self.assertEqual(folded_answers(events, plain_strong), [])

    def test_an_answer_posted_before_the_last_message_fails(self):
        # Live, 2026-09-25: a run "passed" with one post per relay; the desktop app folds the first out of view.
        events = turn(says('Codex COD-B7', 'PERIWINKLE'), says('Grok GRO-8Y', 'PERIWINKLE'))
        self.assertEqual(folded_answers(events, plain_strong),
                         ['folded: Codex COD-B7 says... posted before the last message of its turn'])

    def test_other_text_before_the_last_message_is_fine(self):
        events = turn('Checking the queue.', says('Grok GRO-HF', 'ok'))
        self.assertEqual(folded_answers(events, plain_strong), [])



class FakeHost:
    """A headless session that never answers (a timed hunt still going at its budget), or exits early."""

    def __init__(self, exits=False):
        import threading
        from types import SimpleNamespace
        self.events, self.lock, self.stopped = [], threading.Lock(), False
        self.process = SimpleNamespace(poll=lambda: 1 if exits else None)

    def mark(self):
        return 0

    def results(self):
        return []

    def send(self, prompt):
        self.sent = prompt

    def wait(self, count, timeout, done=None):
        import time
        time.sleep(0 if self.process.poll() is not None else min(timeout, 5))
        return False

    def stop(self):
        self.stopped = True


class TimedCutoff(unittest.TestCase):
    """Adam Bench's timed mode: the harness ends the run at its budget (the session with all it started, and every
    AUTO agent), then grades the project as it stands, told the real elapsed time. A run past its budget is a
    result, not an error."""

    def run_of(self, host, tmp):
        import claude_usage_live as live
        run = object.__new__(live.Run)
        run.name, run.mode, run.options, run.session, run.cut = 'B10T20', 'native', {'agent': None}, None, False
        run.workspace, run.out, run.host, run.seen, run.reader, run.started = tmp, tmp, host, set(), None, 0
        run.agent_before = None
        return run

    def test_a_timed_set_has_its_budget_and_budget_gives_every_task_one(self):
        from unittest.mock import patch
        import claude_usage_live as live
        self.assertEqual(live.time_budget('B10T20'), 1200)
        self.assertIsNone(live.time_budget('B10'))
        with patch.object(live, 'BUDGET', 15):
            self.assertEqual(live.time_budget('H5'), 900)
            self.assertEqual(live.time_budget('B10T20'), 1200)  # Its own budget stays.

    def test_at_its_budget_the_run_is_stopped_then_graded_with_the_time_it_ran(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import claude_usage_live as live
        host = FakeHost()
        graded = {}

        def check(name, workspace, reply, answers, elapsed=None):
            graded.update(stopped=host.stopped, elapsed=elapsed)  # Graded only after everything stopped.
            return dict(ok=True, passed=3, failed=7, summary='fixed 3/10'), {'hidden': '3/10'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(live, 'time_budget', return_value=1), \
                patch.object(live, 'CUTOFF_SETTLE', 0), patch.object(live, 'check', side_effect=check), \
                patch.object(live.adambench, 'keep'):
            run = self.run_of(host, Path(tmp))
            with patch.object(live.Run, 'stop_agents') as agents:
                result = run.ask('B10T20', 'hunt for 20 minutes', {}, 3600)
            agents.assert_called_once()  # Every AUTO agent stops too.
        self.assertTrue(host.stopped)
        self.assertEqual(graded, dict(stopped=True, elapsed=1))
        self.assertEqual(result['cutoff'], dict(budgetSeconds=1, stoppedAtBudget=True, elapsedSeconds=1))
        self.assertEqual(result['minutes'], round(1 / 60, 2))
        self.assertEqual(result['checks'], {'hidden': '3/10'})
        run.finish()  # A cut-off run's session is gone: nothing more is sent to it.

    def test_a_session_that_exits_before_its_budget_is_still_an_error(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import claude_usage_live as live
        with tempfile.TemporaryDirectory() as tmp, patch.object(live, 'time_budget', return_value=60):
            run = self.run_of(FakeHost(exits=True), Path(tmp))
            with self.assertRaisesRegex(RuntimeError, 'exited mid-run'):
                run.ask('B10T20', 'hunt', {}, 3600)

    def test_stop_ends_the_session_and_what_it_started(self):
        # The cutoff must reach the session's own commands and background tasks, not just the session.
        import os
        import subprocess
        import sys
        import tempfile
        import time
        from pathlib import Path
        from claude_background_live import Session
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'child.pid'
            code = ('import subprocess, sys, time, pathlib; '
                    'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]); '
                    'pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(120)')
            session = object.__new__(Session)
            session.process = subprocess.Popen([sys.executable, '-c', code, str(marker)],
                                               start_new_session=os.name != 'nt')
            for _ in range(100):
                if marker.exists() and marker.read_text():
                    break
                time.sleep(.1)
            child = int(marker.read_text())
            session.stop()
            self.assertIsNotNone(session.process.poll())
            time.sleep(1)
            if os.name == 'nt':
                listing = subprocess.run(['tasklist', '/FI', 'PID eq ' + str(child)], capture_output=True, text=True)
                self.assertNotIn(str(child), listing.stdout)
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            session.stop()  # Twice is harmless.


if __name__ == '__main__':
    unittest.main()
