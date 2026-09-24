"""Closure after partial activation must neither strand nor lose ownership."""
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from test_controller import PLUGIN  # Honors CLI_MODE_TEST_PLUGIN in fresh-package runs.
from acpx import AcpxBackend
import agy
import cursor_agent
import codex_cli


class Cleanup(unittest.TestCase):
    def test_actual_start_rejects_unsupported_saved_access_before_spawning(self):
        cases = [(agy.Backend(), {'access': 'auto-edit'}, 'auto-edit'),
                 (cursor_agent.Backend(), {'access': 'prompt'}, 'only allow'),
                 (codex_cli.Backend(), {'access': 'prompt', 'mode': 'read-only'}, 'Full access only')]
        for backend, settings, message in cases:
            with self.subTest(backend=backend.profile), patch('acpx.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError, message):
                    backend.start({'settings': settings}, ['--file', 'never-read.txt'])
                spawn.assert_not_called()

    def test_absent_session_after_failed_creation_is_closed(self):
        backend = AcpxBackend()
        backend.control = Mock(side_effect=[RuntimeError('no cancel target'),
                                           RuntimeError('creation failed'), {'status': 'no-session'}])
        backend.close({'name': 'owned'})
        self.assertEqual(backend.control.call_args.args[1], ['status', '-s', 'owned'])

    def test_failed_close_does_not_hide_existing_or_unknown_owner(self):
        for status in ('running', 'unknown', 'dead'):
            with self.subTest(status=status):
                backend = AcpxBackend()
                backend.control = Mock(side_effect=[{}, RuntimeError('close failed'), {'status': status}])
                with self.assertRaisesRegex(RuntimeError, 'close failed'):
                    backend.close({'name': 'owned'})


if __name__ == '__main__':
    unittest.main()
