"""The opt-in live probe must call the current relay APIs without spending quota."""
from pathlib import Path
import unittest
from unittest.mock import create_autospec

from live_parity_probe import Controller, relay_for_host


class LiveProbeRelay(unittest.TestCase):
    def test_codex_uses_the_inline_relay_signature(self):
        control = create_autospec(Controller, instance=True)
        relay_for_host(control, 'request', 12, Path('evidence'), True)
        control.relay.assert_called_once_with('request', 12, wait=1, view_dir=Path('evidence/views'))
        control.relay_chain.assert_not_called()

    def test_claude_uses_chain_instead_of_removed_views_argument(self):
        control = create_autospec(Controller, instance=True)
        relay_for_host(control, 'request', 12, Path('evidence'), False)
        control.relay_chain.assert_called_once_with(['request'], 12, wait=1)
        control.relay.assert_not_called()
