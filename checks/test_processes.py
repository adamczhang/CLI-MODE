"""A killed submitter never leaves its child running; the child's own children survive."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_controller import PLUGIN
from controller import operation_running

# The submitter binds a child, the child starts a detached grandchild (like the
# ACPX session owner) and both report their PIDs. Then the submitter is killed.
SUBMITTER = r'''
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import processes
child = subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[3]])
print('bound', processes.bind(child), child.pid, flush=True)
time.sleep(60)
'''
CHILD = r'''
import subprocess, sys, time
flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], creationflags=flags)
open(sys.argv[1], 'w').write(str(owner.pid))
time.sleep(60)
'''


def wait_for(predicate, seconds=10):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(.05)
    return False


@unittest.skipUnless(os.name == 'nt', 'Windows job objects')
class SubmitterChildren(unittest.TestCase):
    def test_killing_the_submitter_ends_its_child_but_not_the_session_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            owner_file = Path(folder) / 'owner.pid'
            submitter = subprocess.Popen([sys.executable, '-c', SUBMITTER, str(PLUGIN / 'scripts'), CHILD,
                                          str(owner_file)], stdout=subprocess.PIPE, text=True)
            try:
                _, bound, child = submitter.stdout.readline().split()
                self.assertEqual(bound, 'True')
                child = int(child)
                self.assertTrue(wait_for(owner_file.exists), 'grandchild never started')
                owner = int(owner_file.read_text())
                submitter.kill()  # Abnormal exit: no cleanup code runs.
                submitter.wait(timeout=10)
                self.assertTrue(wait_for(lambda: not operation_running({'pid': child})),
                                'bound child outlived its submitter')
                self.assertTrue(operation_running({'pid': owner}), 'breakaway grandchild was killed')
            finally:
                submitter.kill()
                submitter.wait(timeout=10)
                submitter.stdout.close()
                if owner_file.exists():
                    subprocess.run(['taskkill', '/PID', owner_file.read_text(), '/F'], capture_output=True)

    def test_non_process_values_are_ignored(self):
        import processes
        self.assertFalse(processes.bind(object()))


if __name__ == '__main__':
    unittest.main()
