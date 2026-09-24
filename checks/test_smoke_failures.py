"""Run live-check entrypoints with fixture providers to verify failure reporting."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


class SmokeProvider:
    def __init__(self, store, backend=None):
        self.store = store
        self.backend = self
        self.marker = ''

    def frontend(self): pass

    def activate(self, model, access):
        with self.store.edit() as state:
            state['active'] = True
            state['owned'] = [{'name': 'fixture'}]
        return {'settings': {}, 'owned': [{'name': 'fixture'}], 'main': 'fixture'}

    def send(self, text, output=None):
        match = re.search(r'(?:orchid|idle)-[a-z0-9]+', text)
        if match: self.marker = match.group()
        if 'answer.txt' in text:
            (Path(self.store.workspace) / 'answer.txt').write_text('artifact proof 42')
        output({'type': 'message', 'text': self.marker})
        return {}

    def control(self, owned, args): return {'status': 'dead'}
    def off(self): return {'active': False, 'shutdownComplete': False, 'failures': ['fixture close failure']}


class SmokeFailures(unittest.TestCase):
    def test_live_checks_fail_when_shutdown_is_incomplete(self):
        for name in ('live_smoke', 'idle_smoke'):
            with self.subTest(script=name), tempfile.TemporaryDirectory() as folder:
                spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(module, 'Controller', SmokeProvider))
                    stack.enter_context(patch.object(module.tempfile, 'mkdtemp', return_value=folder))
                    stack.enter_context(patch('sys.argv', [name + '.py']))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    if name == 'idle_smoke':
                        stack.enter_context(patch.object(module.time, 'sleep'))
                    with self.assertRaisesRegex(RuntimeError, 'shutdown is incomplete'):
                        module.main()
                evidence = json.loads((Path(folder) / 'evidence.json').read_text())
                self.assertFalse(evidence['success'])
                self.assertFalse(evidence['shutdown']['shutdownComplete'])
                self.assertNotIn('error', evidence)  # All earlier fixture checks succeeded.


if __name__ == '__main__': unittest.main()
