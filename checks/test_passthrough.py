"""Whole-prompt fidelity, one-session ownership and legacy cleanup regressions."""
import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, hook, PLUGIN
import controller
from controller import Controller
from state import Store, route
import agy


class Passthrough(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store('passthrough', self.root, self.root / 'data')
        self.backend = FakeBackend()
        self.control = Controller(self.store, self.backend)
        self.control.mode('passthrough')  # This fixture exercises unprefixed forwarding.
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')

    def event(self, **fields):
        return dict(hook_event_name='UserPromptSubmit', session_id='passthrough', cwd=str(self.root), **fields)

    def test_whole_requests_share_one_session_and_cannot_replay_after_compaction(self):
        initial = self.store.read()['main']
        for text in ('  Create a complete 3D Rubik\'s cube game.\r\n  Keep indentation.\n',
                     'Please orchestrate this project in parallel using many workers.',
                     '$CLI-MODE-UNKNOWN leave this whole message alone'):
            self.assertEqual(route(text, self.store.read())['route'], 'delegate')
            hook.handle(self.event(prompt=text), self.store.root)
            output = []
            self.control.send_request(self.store.read()['turnRoute']['requestId'], output=output.append)
            self.assertEqual(''.join(e['text'] for e in output if e['type'] == 'message'), text)
            restored = hook.handle(dict(self.event(), hook_event_name='SessionStart', source='compact'), self.store.root)
            self.assertIn('already dispatched', restored['hookSpecificOutput']['additionalContext'])
            with self.assertRaisesRegex(RuntimeError, 'already dispatched'):
                self.control.send(text)
            self.assertEqual(self.store.read()['main'], initial)
            self.assertEqual(len(self.store.read()['owned']), 1)

    def test_legacy_workers_block_dispatch_and_activation_but_off_cleans_all(self):
        with self.store.edit() as state:
            worker = deepcopy(state['owned'][0])
            worker.update(name='legacy-worker', role='worker')
            state['owned'].append(worker)
        before = len(self.backend.calls)
        with self.assertRaisesRegex(RuntimeError, 'Legacy'):
            self.control.send('do work')
        self.control.frontend()
        with self.assertRaisesRegex(RuntimeError, 'Unfinished session cleanup'):
            self.control.activate('gemini-3.8-flash-high', 'allow')
        self.assertEqual(len(self.backend.calls), before)
        self.assertTrue(self.control.off()['shutdownComplete'])
        self.assertIn('legacy-worker', self.backend.closed)
        self.assertFalse(self.store.read()['owned'])

    def test_off_during_reconfiguration_cleans_late_reused_session(self):
        main = self.store.read()['main']
        before = len(self.backend.calls)
        self.control.frontend()
        live = {main}
        close = self.backend.close
        def tracked_close(owned):
            live.discard(owned['name'])
            close(owned)
        def finish_ensure_after_off():
            self.control.off()
            live.add(main)  # Simulate an admitted ensure completing after off.
        self.backend.close = tracked_close
        self.backend.after_start = finish_ensure_after_off
        with self.assertRaisesRegex(RuntimeError, 'canceled'):
            self.control.activate('gemini-pro-agent', 'prompt')
        # Only the already-admitted ensure starts. Later controls fail their
        # generation check; the admitted control performs late cleanup itself.
        self.assertEqual(len(self.backend.calls), before + 1)
        self.assertEqual(self.backend.closed.count(main), 2)
        self.assertFalse(live)
        self.assertFalse(self.store.read()['active'])
        self.assertFalse(self.store.read()['owned'])
        self.assertFalse(self.store.read()['inflight'])
        self.assertTrue(self.control.off()['shutdownComplete'])

    def test_send_cli_preserves_file_line_endings_and_has_no_worker_controls(self):
        payload = '  text\r\n    indented\n$literal\r\n '
        source = self.root / 'prompt.txt'
        source.write_bytes(payload.encode('utf-8'))
        argv = ['controller', '--thread', 'passthrough', '--workspace', str(self.root), 'send', '--file', str(source)]
        with patch.object(sys, 'argv', argv), patch.object(controller, 'Controller') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.send.return_value = {}
            controller.main()
            factory.return_value.send.assert_called_once_with(payload)
        for args in (['worker'], ['close-worker'], ['send', '--file', str(source), '--session', 'other']):
            result = subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'), *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(os.name == 'nt', 'PowerShell refresh integration')
    def test_refresh_uses_existing_main_without_create_or_close(self):
        self.control.tune('model')
        data = agy.catalog(self.store.root)
        record = dict(acpx=dict(available_models=[m['id'] for m in data['models']],
            available_model_names={m['id']: m['name'] for m in data['models']},
            current_model_id=data['advertisedCurrentModelId'], config_options=data['configOptions']))
        metadata = self.root / 'metadata.json'
        metadata.write_text(json.dumps(record), encoding='utf-8')
        log = self.root / 'calls.txt'
        binary = self.root / 'bin'
        binary.mkdir()
        (binary / 'acpx.ps1').write_text(
            "throw 'PATH must not replace the bound ACPX package'\n", encoding='utf-8')
        package = binary / 'node_modules/acpx'
        (package / 'dist').mkdir(parents=True)
        (package / 'package.json').write_text('{"name":"acpx","version":"0.18.0"}', encoding='utf-8')
        (package / 'dist/runtime.js').touch()
        (package / 'dist/cli.js').write_text(
            "const fs=require('fs');const args=process.argv.slice(2);"
            "fs.appendFileSync(process.env.TEST_ACPX_LOG,args.join(' ')+'\\n');"
            "if(args.includes('new')||args.includes('close'))process.exit(9);"
            "process.stdout.write(fs.readFileSync(process.env.TEST_ACPX_METADATA));", encoding='utf-8')
        with self.store.edit() as state:
            state['owned'][0]['acpxRuntime'] = dict(node=shutil.which('node'), package=str(package), version='0.18.0')
        env = dict(os.environ, CLI_MODE_DATA=str(self.store.root), TEST_ACPX_LOG=str(log),
                   TEST_ACPX_METADATA=str(metadata), PATH=str(binary) + os.pathsep + os.environ['PATH'])
        result = subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'passthrough',
            '--workspace', str(self.root), 'refresh'], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding='utf-8')
        self.assertIn('sessions show ' + self.store.read()['main'], calls)
        self.assertNotIn('sessions new', calls)
        self.assertNotIn('sessions close', calls)


if __name__ == '__main__': unittest.main()
