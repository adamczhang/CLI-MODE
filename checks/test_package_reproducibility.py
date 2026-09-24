"""A release must reproduce independently of checkout line endings."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_controller import PLUGIN

BUILDER = Path(__file__).resolve().parents[1] / 'scripts/package_plugin.py'
spec = importlib.util.spec_from_file_location('package_builder', BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Reproducibility(unittest.TestCase):
    def test_binary_assets_are_not_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, data in [('icon.png', b'\r\nimage'), ('data.bin', b'\0\r\n'),
                               ('unknown.bin', b'\xff\r\n')]:
                path = Path(folder) / name
                path.write_bytes(data)
                self.assertEqual(builder.package_bytes(path), data)

    @unittest.skipUnless((PLUGIN / 'claude').is_dir(), 'Building needs the source tree, not the Codex package')
    def test_full_package_is_identical_from_lf_and_crlf_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plugin = root / 'plugins/cli-mode'
            shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (root / 'scripts').mkdir()
            shutil.copyfile(BUILDER, root / 'scripts/package_plugin.py')
            shutil.copyfile(BUILDER.with_name('install-claude.ps1'), root / 'scripts/install-claude.ps1')
            shutil.copytree(BUILDER.parents[1] / '.claude-plugin', root / '.claude-plugin')
            text_files = []
            for path in [*plugin.rglob('*'), root / 'scripts/install-claude.ps1', root / '.claude-plugin/marketplace.json']:
                if not path.is_file() or path.suffix.lower() in {'.jpg', '.png', '.zip'}:
                    continue
                data = path.read_bytes()
                if b'\0' in data:
                    continue
                try:
                    data.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                canonical = data.replace(b'\r\n', b'\n')
                path.write_bytes(canonical)
                text_files.append((path, canonical))
            def build():
                subprocess.run([sys.executable, str(root / 'scripts/package_plugin.py'), '--no-validate'],
                               check=True, capture_output=True, timeout=60)
                # Both installers and the unpacked Claude Code plugin.
                return {p.relative_to(root / 'dist').as_posix(): p.read_bytes()
                        for p in (root / 'dist').rglob('*') if p.is_file()}
            lf = build()
            for path, canonical in text_files:
                path.write_bytes(canonical.replace(b'\n', b'\r\n'))
            self.assertEqual(build(), lf)
