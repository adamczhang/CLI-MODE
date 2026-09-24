"""Run offline tests against a newly extracted archive with fresh local state.

This simulates file layout/state, not installation or hook trust in Codex Desktop.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    project = Path(__file__).resolve().parents[1]
    version = json.loads((project / 'plugins/cli-mode/.codex-plugin/plugin.json').read_text())['version']
    archive = project / 'dist' / ('cli-mode-codex-' + version + '.zip')
    expected = archive.with_suffix('.zip.sha256').read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    folder = Path(tempfile.mkdtemp(prefix='cli-mode-fresh-package-'))
    plugin, workspace = folder / 'installed simulation/cli-mode', folder / 'empty workspace'
    plugin.mkdir(parents=True)
    workspace.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (plugin / info.filename).resolve()
            if not target.is_relative_to(plugin.resolve()):
                raise ValueError('Unsafe archive member: ' + info.filename)
        bundle.extractall(plugin)
    assert sorted(p.relative_to(plugin).as_posix() for p in plugin.rglob('SKILL.md')) == ['codex/skills/cli-mode/SKILL.md']
    registry_path = plugin / 'codex/skills/cli-mode/references/backends.json'
    for backend in json.loads(registry_path.read_text(encoding='utf-8'))['backends']:
        guide = (registry_path.parent / backend['entrypoint']).resolve()
        assert guide.is_relative_to(plugin.resolve()) and guide.is_file()
        assert guide.name == 'backend.md'
    env = dict(os.environ, CODEX_HOME=str(folder / 'fresh home'), CLI_MODE_DATA=str(folder / 'fresh data'),
               CLI_MODE_TEST_PLUGIN=str(plugin), CODEX_THREAD_ID='fresh-package-test')
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(project / 'checks'), '-p', 'test_*.py', '-v'],
                            cwd=workspace, env=env, capture_output=True, text=True, timeout=300)
    report = dict(archive=archive.name, sha256=expected, exitCode=result.returncode,
                  freshExtraction=True, freshHome=True, emptyWorkspace=True, liveProviders=False,
                  installedHostVerified=False, output=result.stdout + result.stderr)
    output = folder / 'evidence.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(report['output'])
    print('Evidence: ' + str(output))
    if result.returncode: raise SystemExit(result.returncode)


if __name__ == '__main__': main()
