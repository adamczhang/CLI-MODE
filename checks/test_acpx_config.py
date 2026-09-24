"""The bridge reads credentials only from files ACPX reports and refuses drift."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_controller import PLUGIN

MODULE = (PLUGIN / 'scripts/acpx-config.mjs').resolve().as_uri()
NODE = shutil.which('node')


def shared(config, files):
    """Run sharedConfig(config) with an in-memory reader; return result or error."""
    script = ('import {sharedConfig} from ' + json.dumps(MODULE) + ';\n'
              'const files = ' + json.dumps(files) + ';\n'
              'const reads = [];\n'
              'try {\n'
              '  const result = await sharedConfig(' + json.dumps(config) + ', async path => {\n'
              '    reads.push(path); return files[path] ?? {}; });\n'
              '  console.log(JSON.stringify({result, reads}));\n'
              '} catch (error) { console.log(JSON.stringify({error: error.message, reads})); }\n')
    out = subprocess.run([NODE, '--input-type=module', '-e', script], capture_output=True,
                         text=True, timeout=20, check=True).stdout
    return json.loads(out)


def show(auth_methods, loaded_project=True):
    return {'authMethods': auth_methods, 'paths': {'global': 'G', 'project': 'P'},
            'loaded': {'global': True, 'project': loaded_project}}


@unittest.skipUnless(NODE, 'Node.js is required')
class SharedConfig(unittest.TestCase):
    def test_project_overrides_global_when_acpx_agrees(self):
        value = shared(show(['a', 'b']), {'G': {'auth': {'a': 'global', 'b': 'global'}},
                                          'P': {'auth': {'b': 'project'}}})
        self.assertEqual(value['result']['authCredentials'], {'a': 'global', 'b': 'project'})

    def test_files_acpx_did_not_load_are_never_read(self):
        value = shared(show(['a'], loaded_project=False),
                       {'G': {'auth': {'a': 'x'}}, 'P': {'auth': {'stale': 'y'}}})
        self.assertEqual(value['reads'], ['G'])
        self.assertEqual(value['result']['authCredentials'], {'a': 'x'})

    def test_disagreement_with_acpx_refuses_to_guess(self):
        for methods in (['a'], ['a', 'b', 'c'], None):
            with self.subTest(methods=methods):
                value = shared(show(methods), {'G': {'auth': {'a': 'x', 'b': 'y'}}})
                self.assertIn('different auth methods', value['error'])

    def test_invalid_values_and_mcp_servers_are_rejected(self):
        self.assertIn('Invalid ACPX auth', shared(show(['a']), {'G': {'auth': {'a': ' '}}})['error'])
        value = shared(show([]), {'G': {'mcpServers': [{'name': 'x'}]}})
        self.assertIn('mcpServers', value['error'])
        # A project list, even empty, replaces the global one.
        self.assertEqual(shared(show([]), {'G': {'mcpServers': [{'name': 'x'}]}, 'P': {'mcpServers': []}})
                         ['result'], {'authCredentials': {}})

    def test_real_acpx_config_show_matches_what_the_bridge_reads(self):
        import acpx
        try:
            install = acpx.runtime_install()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as folder:
            home, workspace = Path(folder) / 'home', Path(folder) / 'workspace'
            (home / '.acpx').mkdir(parents=True)
            workspace.mkdir()
            (home / '.acpx/config.json').write_text(json.dumps({'auth': {'one': 'global-secret', 'two': 'g'}}))
            (workspace / '.acpxrc.json').write_text(json.dumps({'auth': {'two': 'project-secret'}}))
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
            config = json.loads(subprocess.run(
                [install['node'], str(Path(install['package']) / 'dist/cli.js'), '--cwd', str(workspace),
                 '--format', 'json', 'config', 'show'], env=env, capture_output=True, text=True,
                timeout=30, check=True).stdout)
            self.assertNotIn('secret', json.dumps(config))
            script = ('import {sharedConfig} from ' + json.dumps(MODULE) + ';\n'
                      'console.log(JSON.stringify(await sharedConfig(' + json.dumps(config) + ')));')
            out = subprocess.run([install['node'], '--input-type=module', '-e', script], env=env,
                                 capture_output=True, text=True, timeout=20, check=True).stdout
            self.assertEqual(json.loads(out)['authCredentials'], {'one': 'global-secret', 'two': 'project-secret'})


if __name__ == '__main__':
    unittest.main()
