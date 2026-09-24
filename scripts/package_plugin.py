"""Build CLI-MODE's two installers from one source tree, without installing or publishing.

    python scripts/package_plugin.py           build dist/ (fails if the Claude marketplace is stale)
    python scripts/package_plugin.py --sync    rewrite .claude-plugin/marketplace.json, then build
    python scripts/package_plugin.py --check   only check that marketplace file

Outputs, all from plugins/cli-mode:
- dist/cli-mode-codex-<v>.zip: the Codex plugin (Claude-only files left out).
- dist/cli-mode-claude-<v>.zip: a one-plugin Claude Code marketplace wrapping the
  same folder (Codex-only files left out), with install-claude.ps1.
- dist/claude-dev/cli-mode/: an unpacked Claude Code plugin for
  `claude --plugin-dir`, for local testing only.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile


# Codex has run Windows hooks through cmd.exe (`cmd.exe /C "<command>"`) and, from 0.155, through
# PowerShell. The command parses the same in both: one double-quoted Python argument, with no $, %,
# backtick or double quote inside it. Python reads PLUGIN_ROOT itself, so plugin paths with spaces or
# '&' work.
WINDOWS_HOOK = ('python -c "import os,runpy;runpy.run_path(os.environ[\'PLUGIN_ROOT\']'
                '+\'/hooks/route.py\',run_name=\'__main__\')"')
# Claude Code runs the hook directly (exec form), so a path needs no quoting.
CLAUDE_HOOK = dict(type='command', command='python', args=['${CLAUDE_PLUGIN_ROOT}/hooks/claude.py'])
CLAUDE_EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'Stop'}
CLAUDE_COMMANDS = {'cli.md', 'd.md'}
# Files only one host uses, as paths relative to the plugin folder. Claude Code needs no skill: the hook
# gives Claude the whole procedure each turn, and SKILL.md would put an always-on skill in every session.
# The rest of codex/skills/cli-mode ships to both: the runtime reads its agent registry (references/backends.json).
CLAUDE_ONLY = ('claude/', 'hooks/claude.py', 'scripts/claude_shortcuts.py')
CODEX_ONLY = ('hooks/hooks.json', 'codex/skills/cli-mode/agents/openai.yaml', 'codex/skills/cli-mode/SKILL.md')
PUBLISHER = {'name': 'CLI-MODE Project'}
HOMEPAGE = 'https://github.com/adamczhang/CLI-MODE'
DESCRIPTION = ('Persistent CLI-agent routing through ACPX with queued forwarding and public activity: Antigravity, '
               'Claude Code, Grok Build, Cursor, GitHub Copilot and Codex CLI.')
DATE = (2026, 1, 1, 0, 0, 0)


def package_bytes(path):
    """Match the repository's LF policy even from a Windows CRLF working tree."""
    data = path.read_bytes()
    if path.suffix.lower() in {'.jpg', '.png', '.zip'} or b'\0' in data:
        return data
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return data
    return data.replace(b'\r\n', b'\n')


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


def only(relative, prefixes):
    return any(relative == prefix or (prefix.endswith('/') and relative.startswith(prefix)) for prefix in prefixes)


def check_codex(plugin):
    """The Codex plugin: manifest, backend registry, one skill and its three hooks."""
    manifest = json.loads((plugin / '.codex-plugin' / 'plugin.json').read_text(encoding='utf-8'))
    if manifest['name'] != plugin.name:
        raise ValueError('Plugin directory/name mismatch')
    version = manifest['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?', version):
        raise ValueError('Unsafe or invalid archive version')
    registry_path = plugin / 'codex' / 'skills' / 'cli-mode' / 'references' / 'backends.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    if registry.get('schemaVersion') != 1:
        raise ValueError('Unsupported backend contract version')
    sys.path.insert(0, str(plugin / 'scripts'))
    import adapters
    for backend in registry['backends']:
        adapters.descriptor(backend['id'])
    seen = set()
    for backend in registry['backends']:
        if backend['id'] in seen:
            raise ValueError('Duplicate backend ID')
        seen.add(backend['id'])
        entrypoint = (registry_path.parent / backend['entrypoint']).resolve()
        if not entrypoint.is_relative_to(plugin.resolve()) or not entrypoint.is_file():
            raise ValueError('Missing or external backend entrypoint')
        if not re.fullmatch(r'[a-z][a-z0-9-]*', backend['id']):
            raise ValueError('Invalid backend ID')
        if entrypoint != (plugin / 'backends' / backend['id'] / 'backend.md').resolve():
            raise ValueError('Backend entrypoint must be its internal guide')
    actual_skills = {p.relative_to(plugin).as_posix() for p in plugin.rglob('SKILL.md')}
    if actual_skills != {'codex/skills/cli-mode/SKILL.md'}:
        raise ValueError('CLI-MODE must be the only packaged skill')
    hooks = json.loads((plugin / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
    if set(hooks) != {'SessionStart', 'UserPromptSubmit', 'PreToolUse'}:
        raise ValueError('Required CLI-MODE routing hooks are missing or unexpected')
    for groups in hooks.values():
        for group in groups:
            for handler in group['hooks']:
                if handler.get('commandWindows') != WINDOWS_HOOK:
                    raise ValueError('Windows hook command must be the quote-free Python launcher')
                targets = re.findall(r'\$\{PLUGIN_ROOT\}/([^"\s]+)', handler['command'])
                if not targets:
                    raise ValueError('Hook must resolve its script through PLUGIN_ROOT')
                for target in targets:
                    resolved = (plugin / target).resolve()
                    if not resolved.is_relative_to(plugin.resolve()) or not resolved.is_file():
                        raise ValueError('Hook script missing or external')
    return version, len(seen)


def check_claude(plugin):
    """Claude Code's hooks and commands: exec-form Python on hooks/claude.py, nothing Codex-only."""
    config = json.loads((plugin / 'claude' / 'hooks.json').read_text(encoding='utf-8'))
    hooks = config['hooks']
    if set(hooks) != CLAUDE_EVENTS:
        raise ValueError('Claude Code hooks must be exactly ' + ', '.join(sorted(CLAUDE_EVENTS)))
    for groups in hooks.values():
        for group in groups:
            for handler in group['hooks']:
                if {key: handler.get(key) for key in CLAUDE_HOOK} != CLAUDE_HOOK:
                    raise ValueError('Every Claude Code hook must run python on hooks/claude.py in exec form')
    if not (plugin / 'hooks' / 'claude.py').is_file():
        raise ValueError('Claude Code hook script is missing')
    text = json.dumps(config)
    if 'PLUGIN_ROOT}' in text.replace('CLAUDE_PLUGIN_ROOT}', '') or 'commandWindows' in text:
        raise ValueError('Claude Code hooks cannot use Codex hook fields')
    commands = plugin / 'claude' / 'commands'
    if {path.name for path in commands.iterdir()} != CLAUDE_COMMANDS:
        raise ValueError('Claude Code commands must be exactly ' + ', '.join(sorted(CLAUDE_COMMANDS)))
    for path in commands.iterdir():
        if 'disable-model-invocation: true' not in path.read_text(encoding='utf-8'):
            raise ValueError(path.name + ' must only run when the user types it')
    return hooks


def claude_version(version):
    """Claude Code compares versions as given; local build metadata stays with Codex."""
    return version.split('+', 1)[0]


def claude_marketplace(version, hooks):
    """The repository-root marketplace that installs plugins/cli-mode into Claude Code.

    `strict: false` makes this entry the plugin's whole definition, so Claude Code
    loads these hooks and commands and not the folder's Codex hooks/hooks.json.
    A marketplace entry needs its hooks inline.
    """
    return {
        'name': 'cli-mode',
        'owner': PUBLISHER,
        'description': 'CLI-MODE for Claude Code: use six CLI agents through ACPX from inside Claude Code.',
        'plugins': [{
            'name': 'cli-mode',
            'displayName': 'CLI-MODE',
            'version': claude_version(version),
            'description': DESCRIPTION,
            'author': PUBLISHER,
            'homepage': HOMEPAGE,
            'repository': HOMEPAGE,
            'license': 'MIT',
            'keywords': ['acp', 'acpx', 'agents', 'antigravity', 'claude', 'grok', 'cursor', 'copilot', 'codex'],
            'source': './plugins/cli-mode',
            'strict': False,
            'commands': ['./claude/commands/'],
            'hooks': hooks,
        }],
    }


def dev_manifest(version):
    """plugin.json for builds without Codex's hooks file: the Claude zip and the --plugin-dir build.

    A plugin with its own plugin.json keeps its name everywhere; the desktop app
    names a plugin without one after its versioned cache folder (e.g. "0.1.16").
    """
    return {'name': 'cli-mode', 'displayName': 'CLI-MODE', 'version': claude_version(version),
            'description': DESCRIPTION, 'author': PUBLISHER, 'homepage': HOMEPAGE, 'repository': HOMEPAGE,
            'license': 'MIT', 'hooks': './claude/hooks.json', 'commands': './claude/commands/'}


def zip_marketplace(version):
    """The Claude zip's marketplace: a standard entry, since its plugin.json defines the plugin."""
    entry = claude_marketplace(version, {})
    plugin = entry['plugins'][0]
    for key in ('strict', 'commands', 'hooks'):
        del plugin[key]
    return entry


def plugin_files(plugin):
    files = []
    for path in sorted(plugin.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Package cannot contain symlink: {path}')
        if not path.is_file():
            continue
        relative = path.relative_to(plugin)
        if '__pycache__' in relative.parts or 'node_modules' in relative.parts or path.suffix in {'.pyc', '.tmp'}:
            continue
        if path.name in {'.env', 'auth.json', 'credentials.json'}:
            raise ValueError('Runtime credentials do not belong in a plugin')
        files.append((path, relative.as_posix()))
    return files


def write_zip(archive, entries):
    """Entries are (archive name, bytes); fixed timestamps make builds reproducible."""
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('Archive integrity check failed')
        for name, data in entries:
            if bundle.read(name) != data:
                raise ValueError('Archive entry differs from its source: ' + name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8', newline='\n')
    return digest


def validate_with_claude(path):
    """Claude Code's own validator, when it is installed. It checks schemas, not
    whether hooks load at runtime; checks/claude_install_smoke.py covers that."""
    claude = shutil.which('claude')
    if not claude:
        print('Note: claude is not on PATH; skipped claude plugin validate for ' + str(path))
        return
    result = subprocess.run([claude, 'plugin', 'validate', str(path)], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120)
    if result.returncode:
        raise ValueError('claude plugin validate failed for ' + str(path) + ':\n' + result.stdout + result.stderr)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    project = Path(__file__).resolve().parents[1]
    plugin = project / 'plugins' / 'cli-mode'
    version, backends = check_codex(plugin)
    hooks = check_claude(plugin)
    marketplace = json_bytes(claude_marketplace(version, hooks))
    root_marketplace = project / '.claude-plugin' / 'marketplace.json'
    if '--sync' in argv:
        root_marketplace.parent.mkdir(exist_ok=True)
        root_marketplace.write_bytes(marketplace)
    current = root_marketplace.read_bytes().replace(b'\r\n', b'\n') if root_marketplace.is_file() else None
    if current != marketplace:
        raise SystemExit('.claude-plugin/marketplace.json is out of date; run: python scripts/package_plugin.py --sync')
    if '--check' in argv:
        print('.claude-plugin/marketplace.json is in sync (Claude Code version ' + claude_version(version) + ').')
        return

    files = plugin_files(plugin)
    output = project / 'dist'
    output.mkdir(exist_ok=True)

    codex_entries = [(name, package_bytes(path)) for path, name in files if not only(name, CLAUDE_ONLY)]
    codex = output / f'cli-mode-codex-{version}.zip'
    codex_digest = write_zip(codex, codex_entries)

    claude_files = [(path, name) for path, name in files if not only(name, CODEX_ONLY)]
    installer = project / 'scripts' / 'install-claude.ps1'
    claude_entries = ([('.claude-plugin/marketplace.json', json_bytes(zip_marketplace(version))),
                       ('install-claude.ps1', package_bytes(installer)),
                       ('plugins/cli-mode/.claude-plugin/plugin.json', json_bytes(dev_manifest(version)))]
                      + [('plugins/cli-mode/' + name, package_bytes(path)) for path, name in claude_files])
    claude = output / f'cli-mode-claude-{claude_version(version)}.zip'
    claude_digest = write_zip(claude, claude_entries)

    dev = output / 'claude-dev' / 'cli-mode'
    if dev.exists():
        shutil.rmtree(dev)
    for path, name in claude_files:
        target = dev / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(package_bytes(path))
    (dev / '.claude-plugin').mkdir()
    (dev / '.claude-plugin' / 'plugin.json').write_bytes(json_bytes(dev_manifest(version)))

    if '--no-validate' not in argv:
        validate_with_claude(root_marketplace)
        validate_with_claude(dev)
    print(f'Codex:  {codex.name}  {len(codex_entries)} files; {backends} backend(s)  SHA256 {codex_digest}')
    print(f'Claude: {claude.name}  {len(claude_entries)} files  SHA256 {claude_digest}')
    print(f'Claude dev plugin: {dev}')


if __name__ == '__main__':
    main()
