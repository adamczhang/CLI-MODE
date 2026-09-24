"""Two installers from one tree (P4): the Codex zip stays the Codex plugin, the
Claude zip is a marketplace around the same folder, and the checked-in Claude
marketplace always matches the source."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from test_controller import PLUGIN

PROJECT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT / 'scripts/package_plugin.py'
spec = importlib.util.spec_from_file_location('package_builder_p4', BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
CODEX_FILES = (Path(__file__).resolve().parent / 'fixtures/codex-package-files.txt').read_text(encoding='utf-8').split()
# checks/package_smoke.py runs this suite against the extracted Codex zip, which has no Claude files.
SOURCE_TREE = (PLUGIN / 'claude').is_dir()


@unittest.skipUnless(SOURCE_TREE, 'Claude files are not in the Codex package')
class Build(unittest.TestCase):
    """One build of a copy of this tree, shared by the checks below."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        shutil.copytree(PLUGIN, root / 'plugins/cli-mode', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (root / 'scripts').mkdir()
        for name in ('package_plugin.py', 'install-claude.ps1'):
            shutil.copyfile(PROJECT / 'scripts' / name, root / 'scripts' / name)
        shutil.copytree(PROJECT / '.claude-plugin', root / '.claude-plugin')
        subprocess.run([sys.executable, str(root / 'scripts/package_plugin.py'), '--no-validate'],
                       check=True, capture_output=True, timeout=60)
        cls.root, cls.dist = root, root / 'dist'
        manifest = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
        cls.version = manifest['version']
        cls.codex = zipfile.ZipFile(cls.dist / ('cli-mode-codex-' + cls.version + '.zip'))
        cls.claude = zipfile.ZipFile(cls.dist / ('cli-mode-claude-' + builder.claude_version(cls.version) + '.zip'))

    @classmethod
    def tearDownClass(cls):
        cls.codex.close()
        cls.claude.close()
        cls.temp.cleanup()

    def test_codex_zip_is_exactly_the_codex_plugin(self):
        # Any new plugin file must be assigned to one installer or both on purpose.
        self.assertEqual(sorted(self.codex.namelist()), CODEX_FILES)
        self.assertNotIn('hooks/claude.py', self.codex.namelist())
        self.assertFalse([name for name in self.codex.namelist() if name.startswith('claude/')])

    def test_claude_zip_is_a_marketplace_around_the_same_folder(self):
        names = set(self.claude.namelist())
        for required in ('.claude-plugin/marketplace.json', 'install-claude.ps1', 'plugins/cli-mode/hooks/claude.py',
                         'plugins/cli-mode/hooks/route.py', 'plugins/cli-mode/claude/hooks.json',
                         'plugins/cli-mode/claude/commands/cli.md', 'plugins/cli-mode/claude/commands/d.md',
                         'plugins/cli-mode/scripts/controller.py',
                         # The runtime's agent registry lives beside the Codex skill and ships to both.
                         'plugins/cli-mode/codex/skills/cli-mode/references/backends.json',
                         # acp-login.py reads its version from the Codex manifest.
                         'plugins/cli-mode/.codex-plugin/plugin.json'):
            self.assertIn(required, names)
        for codex_only in ('plugins/cli-mode/hooks/hooks.json', 'plugins/cli-mode/codex/skills/cli-mode/agents/openai.yaml',
                           # No skill: it would sit in every Claude Code session's context, and the hook says it all.
                           'plugins/cli-mode/codex/skills/cli-mode/SKILL.md'):
            self.assertNotIn(codex_only, names)  # Claude never loads Codex's hooks, even by mistake.
        shared = {name for name in CODEX_FILES if not builder.only(name, builder.CODEX_ONLY)}
        self.assertEqual({name[len('plugins/cli-mode/'):] for name in names if name.startswith('plugins/')},
                         shared | {'hooks/claude.py', 'scripts/claude_shortcuts.py', 'claude/hooks.json', 'claude/commands/cli.md',
                                   'claude/commands/d.md',
                                   '.claude-plugin/plugin.json'})
        # Without Codex's hooks file, the zip is a standard plugin: its plugin.json names it (the desktop app
        # otherwise names a plugin after its versioned cache folder) and points at Claude's hooks and commands.
        manifest = json.loads(self.claude.read('plugins/cli-mode/.claude-plugin/plugin.json'))
        self.assertEqual((manifest['name'], manifest['hooks'], manifest['commands']),
                         ('cli-mode', './claude/hooks.json', './claude/commands/'))
        entry = json.loads(self.claude.read('.claude-plugin/marketplace.json'))['plugins'][0]
        self.assertEqual((entry['name'], entry['source'], entry['version']), ('cli-mode', './plugins/cli-mode', manifest['version']))
        for key in ('strict', 'hooks', 'commands'):
            self.assertNotIn(key, entry)  # A second definition would conflict with plugin.json.
        self.claude.read('install-claude.ps1').decode('ascii')  # Windows PowerShell 5.1 reads ASCII safely.

    def test_marketplace_entry_defines_the_whole_claude_plugin(self):
        market = json.loads((PROJECT / '.claude-plugin/marketplace.json').read_text(encoding='utf-8'))
        entry = market['plugins'][0]
        self.assertEqual((market['name'], entry['name'], entry['source'], entry['strict']),
                         ('cli-mode', 'cli-mode', './plugins/cli-mode', False))
        self.assertEqual(entry['commands'], ['./claude/commands/'])
        self.assertEqual(entry['hooks'],
                         json.loads((PLUGIN / 'claude/hooks.json').read_text(encoding='utf-8'))['hooks'])
        self.assertEqual(entry['version'], self.version.split('+')[0])
        self.assertNotIn('+', entry['version'])

    def test_unpacked_dev_plugin_names_its_hooks_and_commands(self):
        dev = self.dist / 'claude-dev/cli-mode'
        manifest = json.loads((dev / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
        self.assertEqual((manifest['hooks'], manifest['commands']), ('./claude/hooks.json', './claude/commands/'))
        self.assertFalse((dev / 'hooks/hooks.json').exists())
        self.assertTrue((dev / 'hooks/claude.py').is_file())


@unittest.skipUnless(SOURCE_TREE, 'Claude files are not in the Codex package')
class Checks(unittest.TestCase):
    def test_checked_in_marketplace_is_in_sync(self):
        result = subprocess.run([sys.executable, str(BUILDER), '--check'], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_claude_hook_rules_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin = Path(folder) / 'cli-mode'
            shutil.copytree(PLUGIN / 'claude', plugin / 'claude')
            (plugin / 'hooks').mkdir()
            (plugin / 'hooks/claude.py').write_text('', encoding='utf-8')
            builder.check_claude(plugin)
            config = json.loads((plugin / 'claude/hooks.json').read_text(encoding='utf-8'))
            broken = [
                lambda c: c['hooks'].pop('Stop'),
                lambda c: c['hooks']['Stop'][0]['hooks'][0].update(command='python "${CLAUDE_PLUGIN_ROOT}/hooks/claude.py"', args=None),
                lambda c: c['hooks']['Stop'][0]['hooks'][0].update(args=['${CLAUDE_PLUGIN_ROOT}/hooks/route.py']),
                lambda c: c['hooks']['Stop'][0]['hooks'][0].update(commandWindows='x'),
            ]
            for change in broken:
                variant = json.loads(json.dumps(config))
                change(variant)
                (plugin / 'claude/hooks.json').write_text(json.dumps(variant), encoding='utf-8')
                with self.subTest(change=change), self.assertRaises(ValueError):
                    builder.check_claude(plugin)
            (plugin / 'claude/hooks.json').write_text(json.dumps(config), encoding='utf-8')
            command = plugin / 'claude/commands/d.md'
            command.write_text(command.read_text(encoding='utf-8').replace('disable-model-invocation: true', ''),
                               encoding='utf-8')
            with self.assertRaises(ValueError):
                builder.check_claude(plugin)


if __name__ == '__main__':
    unittest.main()
