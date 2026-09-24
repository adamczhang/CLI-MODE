"""Install CLI-MODE from this repository into a throwaway Codex home and check it.

It confirms that the Claude Code files added for the second installer change
nothing for Codex: Codex still reads .agents/plugins/marketplace.json (not the
repository's .claude-plugin/marketplace.json), installs the same plugin folder
with its Codex hooks, and the installed copy passes installed_plugin_smoke.py.
Your own Codex home is never touched; the throwaway one is deleted (unless --keep).

    python checks/codex_install_smoke.py [--keep]
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT / 'plugins' / 'cli-mode'


def codex(env, *args):
    result = subprocess.run([shutil.which('codex'), *args], env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=300)
    assert result.returncode == 0, ' '.join(args) + ': ' + result.stdout + result.stderr
    return result.stdout + result.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--keep', action='store_true', help='Keep the throwaway Codex home for inspection.')
    args = parser.parse_args()
    if not shutil.which('codex'):
        raise SystemExit('Codex CLI (codex) is not on PATH.')
    work = Path(tempfile.mkdtemp(prefix='cli-mode-codex-install-'))
    env = {key: value for key, value in os.environ.items() if not key.startswith('CLI_MODE_')}
    env['CODEX_HOME'] = str(work / 'codex home')
    Path(env['CODEX_HOME']).mkdir()  # Codex requires the folder to exist.
    report = {}
    try:
        codex(env, 'plugin', 'marketplace', 'add', str(PROJECT))
        listing = codex(env, 'plugin', 'list')
        manifest = re.search(r'Marketplace `cli-mode`\s*\n(.+marketplace\.json)', listing)
        assert manifest, listing
        report['marketplaceManifest'] = manifest.group(1).strip()
        assert manifest.group(1).replace('\\', '/').endswith('.agents/plugins/marketplace.json'), (
            'Codex read ' + manifest.group(1) + ' instead of .agents/plugins/marketplace.json')
        added = codex(env, 'plugin', 'add', 'cli-mode@cli-mode')
        root = re.search(r'Installed plugin root: (.+)', added)
        assert root, added
        installed = Path(root.group(1).strip())
        report['installedPlugin'] = str(installed)
        # The Codex hooks, exactly; the Claude files ride along unused.
        assert (installed / 'hooks/hooks.json').read_bytes() == (PLUGIN / 'hooks/hooks.json').read_bytes()
        assert json.loads((installed / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['name'] == 'cli-mode'
        assert not (installed / '.claude-plugin').exists()
        report['claudeFilesPresentButUnused'] = (installed / 'claude/hooks.json').is_file()
        # installed_plugin_smoke.py runs as a Codex task with Full Access would, like the offline tests.
        task = dict(env, CODEX_PERMISSION_PROFILE=':danger-full-access')
        smoke = subprocess.run([sys.executable, str(PROJECT / 'checks/installed_plugin_smoke.py'),
                                '--plugin', str(installed), '--output', str(work / 'installed smoke')],
                               env=task, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        assert smoke.returncode == 0, smoke.stdout + smoke.stderr
        evidence = json.loads((work / 'installed smoke/evidence.json').read_text(encoding='utf-8'))
        report['installedSmoke'] = dict(verifiedFiles=evidence['verifiedFiles'],
                                        agents=[row['agent'] for row in evidence['agents']])
        report['passed'] = True
    finally:
        if args.keep:
            report['kept'] = str(work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
