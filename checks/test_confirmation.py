"""Shared activation, attribution and public-stream contracts across all providers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import Controller, FakeBackend, Store, PLUGIN
import adapters
import confirmation
import menu_view
import native_agy
from state import backend_ids


class Confirmation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_all_six_share_activation_fallback_and_colors(self):
        for agent in backend_ids():
            adapter = adapters.module(agent)
            settings = adapter.selection(self.root, **adapter.DEFAULTS)
            text = confirmation.activation(agent, settings, {})
            self.assertTrue(text.startswith('**CLI-MODE Activated**\n\n**Model:** '))
            self.assertTrue(text.endswith('**Utilization:** Usage not available through CLI'))
            self.assertIn('**Question:** `/help`\n\n', text)
            path = menu_view.write_message(text, self.root / (agent + '.html'), adapter.LABEL, 'activation')
            html = Path(path).read_text(encoding='utf-8')
            self.assertIn('{color:' + menu_view.GREEN + ';}', html)
            self.assertIn('font-family:ui-monospace', html)
            self.assertNotIn('says...', html)

    def test_usage_windows_and_failure_reasons_are_normalized(self):
        summary = {'windows': [dict(window='five_hour', utilization='25% used', resetRemaining='0 days 2 hours'),
                               dict(window='weekly', utilization='10% used', resetRemaining='4 days 1 hours')]}
        result = confirmation.utilization(summary)
        self.assertEqual(result, 'Five hour: 25% used | resets in 0 days 2 hours\n' +
                         ' ' * 13 + 'Weekly: 10% used | resets in 4 days 1 hours')
        for extra in ({'status': 'unavailable'}, {'accountMatched': False}, {'account': {'billedTo': 'api-key'}}, {'windows': 'invalid'}):
            self.assertEqual(confirmation.utilization(dict(summary, **extra)), confirmation.UNAVAILABLE)

    def test_unsupported_usage_never_starts_a_process(self):
        with patch('confirmation.subprocess.run', side_effect=AssertionError('No quota query supported')):
            for agent in ('grok-build', 'cursor', 'copilot', 'codex'):
                self.assertEqual(confirmation.usage(agent, {'model': 'unused'}, str(self.root)), {'status': 'unavailable'})

    def test_supported_usage_uses_task_workspace_and_handles_failure(self):
        with patch('confirmation.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({'status': 'unavailable'})
            for agent in ('agy', 'claude'):
                confirmation.usage(agent, {'model': 'accepted-model'}, str(self.root))
                self.assertEqual(run.call_args.kwargs['cwd'], str(self.root))
                self.assertEqual(run.call_args.args[0][-2:], ['--model', 'accepted-model'])
            run.side_effect = OSError('unavailable')
            self.assertEqual(confirmation.usage('claude', {'model': 'x'}, str(self.root))['status'], 'unavailable')

    def test_only_verified_current_activation_can_be_rendered(self):
        store = Store('confirmation', self.root, self.root / 'state')
        c = Controller(store, FakeBackend())
        output = self.root / 'activation.html'
        with self.assertRaises(RuntimeError):
            c.activation_message(output)
        c.frontend('codex')
        c.activate(agent='codex', **adapters.module('codex').DEFAULTS)
        self.assertIn(confirmation.UNAVAILABLE, c.activation_message(output)['text'])
        def stopped(*args):
            c.off()
            return {}
        with patch('confirmation.usage', side_effect=stopped):
            with self.assertRaisesRegex(RuntimeError, 'Agent changed'):
                c.activation_message(self.root / 'stale.html')
        self.assertFalse((self.root / 'stale.html').exists())

    def test_cli_generates_verified_confirmation_and_canonical_passing_name(self):
        store = Store('confirmation-cli', self.root, self.root / 'state')
        c = Controller(store, FakeBackend())
        c.frontend('codex')
        c.activate(agent='codex', **adapters.module('codex').DEFAULTS)
        output = self.root / 'message.html'
        base = [sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'confirmation-cli',
                '--workspace', str(self.root), '--message-output', str(output)]
        env = dict(os.environ, CLI_MODE_DATA=str(store.root))
        result = subprocess.run(base + ['activation-message'], env=env, capture_output=True, check=True)
        self.assertIn(confirmation.UNAVAILABLE, json.loads(result.stdout)['text'])
        source = self.root / 'wrong-label.txt'
        source.write_text('Passing to the wrong agent...', encoding='utf-8')
        subprocess.run(base + ['format-message', '--kind', 'passing', '--agent', 'codex', '--file', str(source)],
                       env=env, capture_output=True, check=True)
        self.assertIn('Passing to Codex...', output.read_text(encoding='utf-8'))
        self.assertNotIn('wrong agent', output.read_text(encoding='utf-8'))

    def test_all_adapters_use_workspace_and_filter_private_events(self):
        for agent in backend_ids():
            adapter = adapters.module(agent)
            owned = {'workspace': str(self.root), 'settings': adapter.selection(self.root, **adapter.DEFAULTS)}
            with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
                argv = adapter.Backend().command(owned, ['sessions', 'list'])
            self.assertEqual(argv[argv.index('--cwd') + 1], str(self.root))
            for kind in ('agent_thought_chunk', 'tool_call', 'tool_call_update'):
                self.assertIsNone(adapter.public_event({'params': {'update': {'sessionUpdate': kind,
                    'content': {'type': 'text', 'text': 'PRIVATE'}}}}))
            public = adapter.public_event({'params': {'update': {'sessionUpdate': 'agent_message_chunk',
                    'content': {'type': 'text', 'text': 'I will check the configuration.'}}}})
            self.assertEqual(public['text'], 'I will check the configuration.')
        for kind in ('thought', 'tool_call', 'agent_thought'):
            self.assertIsNone(native_agy.public_event({'event': 'step_update', 'step_update':
                {'step_type': kind, 'text_delta': 'PRIVATE'}}))

    def test_all_six_have_matching_names_and_provider_cannot_impersonate_activation(self):
        for agent in backend_ids():
            adapter = adapters.module(agent)
            self.assertEqual(adapter.PASSING, 'Passing to ' + adapter.LABEL + '...')
            html = Path(menu_view.write_message('**CLI-MODE Activated**', self.root / (agent + '.html'),
                                               adapter.LABEL, 'agent')).read_text(encoding='utf-8')
            self.assertIn(adapter.LABEL + ' says...', html)
            self.assertIn('color:inherit;', html)
            self.assertNotIn('font-size:14px', html)


if __name__ == '__main__':
    unittest.main()
