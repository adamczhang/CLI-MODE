"""Controller integration for the explicit Windows dependency wizard."""
import json
import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).with_name('setup.ps1')


def call(action, *, approved=False, run_id=None, backend=None):
    if os.name != 'nt':
        raise RuntimeError('The guided installer supports Windows. Install prerequisites separately, then rerun /cli.')
    shell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    args = [str(shell), '-NoProfile', '-NonInteractive', '-File', str(SCRIPT), '-Action', action]
    if approved:
        args.append('-Approved')
    if run_id:
        args += ['-RunId', run_id]
    if backend:
        args += ['-Backend', backend]
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=150, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Setup '+action.lower()+' failed. Check for an existing installer or blocked PowerShell scripts; install manually and rerun /cli if needed.')
    return json.loads(result.stdout.lstrip('\ufeff'))


def refresh_paths():
    if os.name != 'nt':
        return
    import winreg
    paths = [str(Path(os.environ['LOCALAPPDATA'])/'Programs/Python/Python313'),
             str(Path(os.environ['ProgramFiles'])/'nodejs'), os.environ.get('PATH', '')]
    for hive, key in ((winreg.HKEY_CURRENT_USER, 'Environment'),
                      (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
        try:
            with winreg.OpenKey(hive, key) as handle:
                paths.append(os.path.expandvars(winreg.QueryValueEx(handle, 'Path')[0]))
        except OSError:
            pass
    paths += [str(Path(os.environ['LOCALAPPDATA'])/'agy/bin'), str(Path(os.environ['APPDATA'])/'npm')]
    # Called before every ACPX operation. Appending the current PATH plus the
    # registry on every call eventually exceeds cmd.exe/Windows limits.
    unique = []
    seen = set()
    for source in paths:
        for entry in source.split(os.pathsep):
            entry = entry.strip().strip('"')
            if not entry:
                continue
            key = os.path.normcase(os.path.normpath(entry))
            if key not in seen:
                seen.add(key)
                unique.append(entry)
    os.environ['PATH'] = os.pathsep.join(unique)
