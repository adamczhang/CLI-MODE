"""AGENTS.md (Codex) and CLAUDE.md (Claude Code) guide agents working in this repository.

They describe the architecture of one release. So that an agent never follows a stale map, each says which
version it was written for, and this test fails when that differs from the plugin's version (a release
reviews both files) or when a path either file names no longer exists (a rename or removal updates them).
"""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins' / 'cli-mode'
DOCS = ('AGENTS.md', 'CLAUDE.md')
WRITTEN_FOR = re.compile(r'^> \*\*Written for CLI-MODE (\d+\.\d+\.\d+)\*\*', re.MULTILINE)
# A backticked repository path: letters, digits and ._/- with at least one "/", optionally followed by
# ":function()". Placeholders (<v>, {a,b}, *), home or environment paths and sibling folders are prose.
PATH = re.compile(r'`(\.?[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-]+)+/?)(?::[A-Za-z_.()]+)?`')
# A backticked bare file name (`presentation.py`, `SKILL.md`): it must exist somewhere in the repository.
NAME = re.compile(r'`([A-Za-z0-9_][A-Za-z0-9_\-]*\.(?:py|mjs|cjs|ps1|json|md|yaml|txt))`')
SKIPPED = ('.git', 'dist', '__pycache__', 'node_modules')


def plugin_version():
    manifest = json.loads((PLUGIN / '.codex-plugin' / 'plugin.json').read_text(encoding='utf-8'))
    return manifest['version'].split('+')[0]  # Local builds add +codex.<stamp>.


class AgentDocs(unittest.TestCase):
    def test_each_guide_is_written_for_the_current_version(self):
        for name in DOCS:
            with self.subTest(name):
                found = WRITTEN_FOR.findall((ROOT / name).read_text(encoding='utf-8'))
                self.assertEqual(found, [plugin_version()],
                                 name + ' was written for another version: review it against the code and update '
                                 'its "Written for" line.')

    def test_every_path_a_guide_names_exists(self):
        for name in DOCS:
            paths = PATH.findall((ROOT / name).read_text(encoding='utf-8'))
            self.assertGreater(len(paths), 20, name)  # The pattern still finds the map.
            for path in paths:
                with self.subTest(name, path=path):
                    self.assertTrue((ROOT / path).exists() or (PLUGIN / path).exists(),
                                    name + ' names ' + path + ', which no longer exists.')

    def test_every_file_name_a_guide_mentions_exists(self):
        present = {path.name for path in ROOT.rglob('*')
                   if path.is_file() and not any(part in SKIPPED for part in path.relative_to(ROOT).parts)}
        for name in DOCS:
            names = NAME.findall((ROOT / name).read_text(encoding='utf-8'))
            self.assertGreater(len(names), 20, name)
            for file_name in names:
                with self.subTest(name, file=file_name):
                    self.assertIn(file_name, present, name + ' mentions ' + file_name + ', which no longer exists.')


if __name__ == '__main__':
    unittest.main()
