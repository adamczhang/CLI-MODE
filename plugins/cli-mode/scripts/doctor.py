"""Read-only discovery of conflicting CLI-MODE installations; never reads auth."""
import json
from pathlib import Path
import shutil
import host
from state import backend_records


def acpx_install():
    """Which ACPX package CLI-MODE would bind: its own copy first, then global npm."""
    import acpx
    try:
        return dict(acpx.runtime_install(), ownedRoot=str(acpx.owned_root()))
    except RuntimeError as exc:
        return dict(error=str(exc), ownedRoot=str(acpx.owned_root()))


def inspect(home=None):
    home = Path(home) if home else host.home()
    conflicts = []
    for name in ('cli-mode', 'cli-mode-agy'):
        path = home / 'skills' / name / 'SKILL.md'
        if path.exists():
            text = path.read_text(encoding='utf-8-sig')
            conflicts.append(dict(path=str(path.parent), kind='standalone-skill',
                                  legacyTerminalWorkflow=('terminal' in text.lower() and 'wizard' in text.lower()),
                                  reason='Standalone skill competes with the namespaced plugin skill.'))
    leftovers = [str(path) for path in (home / 'cli-mode-state', home / 'plugins/cache/cli-mode') if path.exists()]
    backups = home / 'skill-backups'
    if backups.exists():
        leftovers.extend(str(p) for p in backups.glob('cli-mode-*'))
    return dict(conflicts=conflicts, historicalCandidates=leftovers,
                tools={name: shutil.which(name) for name in sorted({'acpx', 'python'} |
                      {binary for item in backend_records() for _, binary in item['prerequisites'] if binary})},
                backends={item['id']: {label: shutil.which(binary or 'acpx')
                          for label, binary in item['prerequisites']} for item in backend_records()},
                acpx=acpx_install(),
                currentPlugin=str(Path(__file__).resolve().parents[1]),
                note='Read-only. Candidates are not proof of disposable data. Do not remove provider runtimes, credentials, or ACPX history.')


if __name__ == '__main__':
    print(json.dumps(inspect(), indent=2))
