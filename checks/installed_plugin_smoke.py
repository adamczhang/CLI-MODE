"""Offline smoke check against the installed CLI-MODE plugin cache."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid


AGENTS = ('agy', 'claude', 'grok-build', 'cursor', 'copilot', 'codex')
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(plugin, workspace, data, thread, name, *args):
    view = data / (name + '-' + uuid.uuid4().hex[:8] + '.html')
    result = subprocess.run([
        sys.executable, str(plugin / 'scripts/controller.py'),
        '--thread', thread, '--workspace', str(workspace), '--data-root', str(data),
        '--menu-output', str(view), name, *args], capture_output=True,
        text=True, encoding='utf-8', timeout=45)
    if result.returncode:
        raise AssertionError(name + ': ' + result.stdout + result.stderr)
    value = json.loads(result.stdout)
    menu = value.get('menuView')
    if menu:
        reference = menu.get('reference') or value.get('reference', '')
        assert reference.startswith('\ue200visualize\ue202') and reference.endswith('\ue201')
        assert Path(menu['path']).is_file()
    return value


def main(plugin, output):
    plugin = plugin.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'plugins/cli-mode'
    source_files = [p for p in source.rglob('*') if p.is_file() and p.suffix != '.pyc']
    mismatches = []
    for path in source_files:
        installed = plugin / path.relative_to(source)
        if not installed.is_file() or digest(path) != digest(installed):
            mismatches.append(str(path.relative_to(source)))
    assert not mismatches, 'Installed files differ: ' + ', '.join(mismatches)
    # Bytecode caches are local artifacts (an install from a working tree can copy them).
    installed_files = [p for p in plugin.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    assert len(installed_files) == len(source_files)
    sys.path.insert(0, str(plugin / 'scripts'))
    from state import Store, route
    rows = []
    for agent in AGENTS:
        workspace = output / agent / 'workspace'
        data = output / agent / 'state'
        workspace.mkdir(parents=True, exist_ok=True)
        data.mkdir(parents=True, exist_ok=True)
        thread = 'installed-smoke-' + agent + '-' + uuid.uuid4().hex
        home = command(plugin, workspace, data, thread, 'frontend', '--agent', 'home')
        assert agent in [choice['value'] for choice in home['pending']['choices']]
        page = command(plugin, workspace, data, thread, 'frontend', '--agent', agent)
        phases = {}
        for phase in ('model', 'effort', 'access'):
            shown = command(plugin, workspace, data, thread, 'options', '--phase', phase)
            phases[phase] = len(shown['choices'])
            assert phases[phase] > 0
        commands = command(plugin, workspace, data, thread, 'commands')
        assert '/cli resume' in commands['activationMenu']
        state = dict(Store(thread, workspace, data).read(), active=True, backend=agent)
        assert route('/cli resume', state)['route'] == 'resume'
        assert route('$CLI RESUME', state)['route'] == 'resume'
        idle = command(plugin, workspace, data, thread, 'resume')
        assert idle['requestIds'] == [] and idle['worker']['worker'] == 'idle'
        rows.append(dict(agent=agent, phases=phases, resumeRoute=True,
                         idleResume=idle['worker']['worker'], menuRendered=bool(page.get('menuView'))))
    report = dict(installedPlugin=str(plugin), verifiedFiles=len(source_files), agents=rows)
    (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    main(args.plugin, args.output)
