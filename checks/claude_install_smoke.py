"""Install CLI-MODE into a throwaway Claude Code configuration and check it, at no cost.

Your own Claude Code setup is never touched: each channel gets its own
CLAUDE_CONFIG_DIR, which is deleted afterwards (unless --keep).

Channels:
- repo: `claude plugin marketplace add <this repository>`, as a GitHub install does;
- zip:  dist/cli-mode-claude-<v>.zip, extracted and installed by its install-claude.ps1.

For each, it checks the installed plugin's inventory, that Claude Code
registered exactly CLI-MODE's Claude hooks (never the folder's Codex hooks),
and real prompts that CLI-MODE answers without a model turn: 0 turns and $0,
so no sign-in is needed.

    python checks/package_plugin.py            (build first, for the zip channel)
    python checks/claude_install_smoke.py [--channel repo|zip|all] [--keep]
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
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT / 'plugins' / 'cli-mode'
HOOK_EVENTS = 'SessionStart, UserPromptSubmit, PreToolUse, Stop'
HOOK_HANDLERS = 6  # SessionStart, UserPromptSubmit, PreToolUse x3 (Agent, Bash, PowerShell), Stop.
# Prompts CLI-MODE answers itself, and a phrase each answer must contain. Replies are chat messages by
# default (a model turn, and no sign-in here), so the first prompt switches to instant replies, which
# is itself answered instantly and saved for the prompts after it.
PROMPTS = (
    ('/cli display instant', 'instant replies'),
    ('/cli help', '/cli reset'),
    ('/cli-mode:cli help', '/cli help'),
    ('/cli', 'Setup CLI Agent.'),
    ('$cli queue', '/cli to activate.  Say /cli help to see options'),
    ('/cli mode', 'CLI-MODE: Agent not activated. /CLI to setup'),
    # Every `claude -p` is a new session, and a session that has not used CLI-MODE keeps no state at all
    # (the hook's shortcut for events CLI-MODE has no part in).
    ('/cli reset', 'has no saved state for this session'),
    # Bare /cli and /d for a GitHub install too (the zip's installer already added them).
    ('/cli shortcuts', 'now autocomplete as typed'),
)


CLAUDE = shutil.which('claude')


def claude(env, *args, cwd=None, timeout=180):
    result = subprocess.run([CLAUDE, *args], env=env, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    return result


def install_repo(env, work):
    for args in (('plugin', 'marketplace', 'add', str(PROJECT)), ('plugin', 'install', 'cli-mode@cli-mode')):
        result = claude(env, *args)
        assert result.returncode == 0, ' '.join(args) + ': ' + result.stdout + result.stderr
    return str(PROJECT)


def install_zip(env, work):
    version = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version'].split('+')[0]
    archive = PROJECT / 'dist' / ('cli-mode-claude-' + version + '.zip')
    assert archive.is_file(), 'Build first: python scripts/package_plugin.py (missing ' + archive.name + ')'
    extracted = work / 'extracted'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    destination = work / 'installed marketplace'
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(extracted / 'install-claude.ps1'), '-Destination', str(destination)],
                            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    assert result.returncode == 0 and 'is installed for Claude Code' in result.stdout, result.stdout + result.stderr
    # The bare /cli and /d personal commands (autocomplete offers only /cli-mode:cli otherwise), and a user's
    # own file is never replaced: d.md becomes the user's before the upgrade below.
    commands = Path(env['CLAUDE_CONFIG_DIR']) / 'commands'
    for name in ('cli.md', 'd.md'):
        text = (commands / name).read_bytes()
        assert text.startswith(b'---\n') and b'Installed by CLI-MODE' in text, (name, text[:200])
    own = '---\ndescription: my own d\n---\nMine.\n'
    (commands / 'd.md').write_text(own, encoding='utf-8', newline='\n')
    again = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                            str(extracted / 'install-claude.ps1'), '-Destination', str(destination)],
                           env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    assert again.returncode == 0, 'Upgrade in place failed: ' + again.stdout + again.stderr
    assert (commands / 'd.md').read_text(encoding='utf-8') == own and 'Kept your own' in again.stdout, again.stdout
    return str(destination)


def check_channel(channel, install, keep):
    work = Path(tempfile.mkdtemp(prefix='cli-mode-claude-' + channel + '-'))
    config, project = work / 'claude config', work / 'project'
    project.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith('CLI_MODE_')}
    env.update(CLAUDE_CONFIG_DIR=str(config), MSYS_NO_PATHCONV='1')
    report = dict(channel=channel)
    try:
        report['source'] = install(env, work)
        details = claude(env, 'plugin', 'details', 'cli-mode@cli-mode').stdout
        # Claude Code needs no skill. Codex's lives in codex/skills (its manifest's "skills" path), outside the
        # skills/ folder Claude Code scans, so both channels list only the two commands. (A strict:false entry
        # cannot hide a folder's skills: tested with "skills": [].)
        assert re.search(r'Skills \(2\)\s+cli, d', details), details
        assert re.search(r'Hooks \(4\)\s+' + re.escape(HOOK_EVENTS), details), details
        report['inventory'] = [line.strip() for line in details.splitlines() if line.strip().startswith(('Skills', 'Hooks'))]
        answers = []
        for index, (prompt, expected) in enumerate(PROMPTS):
            args = ['-p', prompt, '--output-format', 'stream-json', '--verbose'] + (['--debug'] if index == 0 else [])
            result = claude(env, *args, cwd=project)
            events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            value = next(event for event in events if event.get('type') == 'result')
            if index == 0:
                # What autocomplete offers: the zip's installer adds the bare personal commands.
                names = next(event for event in events if event.get('subtype') == 'init')['slash_commands']
                bare = {'cli', 'd'} <= set(names)
                assert bare == (channel == 'zip') and {'cli-mode:cli', 'cli-mode:d'} <= set(names), names
                report['slashCommands'] = sorted(name for name in names if 'cli' in name or name == 'd')
            assert value.get('num_turns') == 0 and value.get('total_cost_usd') == 0, (prompt, value)
            assert expected in value['result'], (prompt, value['result'][:400])
            answers.append(dict(prompt=prompt, turns=0, costUsd=0, contains=expected))
            if index == 0:
                log = max((config / 'debug').glob('*.txt'), key=lambda path: path.stat().st_mtime).read_text(
                    encoding='utf-8', errors='replace')
                assert 'Hook load failed' not in log, 'A hook failed to load'
                # Newer Claude Code also loads built-in plugins, which add no hooks in a fresh configuration.
                registered = re.search(r'Registered (\d+) hooks from \d+ plugins?', log)
                assert registered and int(registered.group(1)) == HOOK_HANDLERS, (
                    'Expected exactly Claude\'s ' + str(HOOK_HANDLERS) + ' hook handlers: ' +
                    (registered.group(0) if registered else 'none registered'))
                report['registeredHooks'] = int(registered.group(1))
        report['prompts'] = answers
        # Either route now ends with the same commands: Claude Code lists the bare names too.
        init = claude(env, '-p', '/cli help', '--output-format', 'stream-json', '--verbose', cwd=project).stdout
        names = next(json.loads(line) for line in init.splitlines()
                     if line.startswith('{') and '"subtype":"init"' in line.replace(' ', ''))['slash_commands']
        assert {'cli', 'd', 'cli-mode:cli', 'cli-mode:d'} <= set(names), names
        report['slashCommandsAfterShortcuts'] = sorted(name for name in names if 'cli' in name or name == 'd')
        sessions = list((config / 'plugins/data/cli-mode-cli-mode/sessions').glob('*.json'))
        assert sessions, 'No CLI-MODE state in the plugin data folder'
        report['stateFiles'] = len(sessions)
        report['passed'] = True
    finally:
        if keep:
            report['kept'] = str(work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--channel', choices=('repo', 'zip', 'all'), default='all')
    parser.add_argument('--keep', action='store_true', help='Keep the throwaway configurations for inspection.')
    parser.add_argument('--claude', help='A Claude Code binary to test with, such as the desktop app\'s own '
                                         '(%%APPDATA%%\\Claude\\claude-code\\<version>\\claude.exe). Default: claude on PATH.')
    args = parser.parse_args()
    global CLAUDE
    CLAUDE = args.claude or CLAUDE
    if not CLAUDE:
        raise SystemExit('Claude Code (claude) is not on PATH.')
    version = subprocess.run([CLAUDE, '--version'], capture_output=True, text=True).stdout.split()[0]
    channels = {'repo': install_repo, 'zip': install_zip}
    selected = channels if args.channel == 'all' else {args.channel: channels[args.channel]}
    reports = [check_channel(name, install, args.keep) for name, install in selected.items()]
    print(json.dumps(dict(claudeCode=version, channels=reports), indent=2))


if __name__ == '__main__':
    main()
