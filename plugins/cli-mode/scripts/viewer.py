"""The agent viewer: a PowerShell window that shows each turn as it runs.

It is read-only. The window follows the public events file every turn already
writes (requests/<session>/operations/<op>.jsonl) and draws agent text, tool
activity, plans and the result. It never sends anything to the agent, so
closing it at any time is safe. /cli view on|off is saved for every session
and is off by default; while it is on, each turn reopens the window if it was
closed. PowerShell 7 draws it when installed; Windows PowerShell 5.1 otherwise.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

SCRIPT = Path(__file__).with_name('viewer.ps1')
CHOICES = ('on', 'off')
# The window touches its heartbeat about once a second; older than this, it is gone.
STALE = 4


def settings_path(root):
    return Path(root) / 'viewer.json'


def enabled(root):
    try:
        return json.loads(settings_path(root).read_text(encoding='utf-8')).get('view') == 'on'
    except (OSError, ValueError, AttributeError):
        return False


def save(root, choice):
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'view': choice}) + '\n', encoding='utf-8')
    until = time.monotonic() + 1
    while True:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:  # Windows: the open window is reading it this instant.
            if time.monotonic() >= until:
                raise
            time.sleep(.05)


def folder(store):
    return store.root / 'requests' / store.key / 'operations'


def heartbeat(store):
    return store.root / 'requests' / store.key / 'viewer.alive'


def running(store):
    try:
        return time.time() - heartbeat(store).stat().st_mtime < STALE
    except OSError:
        return False


def shell():
    """PowerShell 7 when installed, else the built-in Windows PowerShell."""
    found = shutil.which('pwsh')
    if found:
        return found
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramW6432')):
        candidate = Path(base or '') / 'PowerShell' / '7' / 'pwsh.exe'
        if base and candidate.is_file():
            return str(candidate)
    builtin = Path(os.environ.get('SystemRoot') or r'C:\Windows') / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return str(builtin) if builtin.is_file() else shutil.which('powershell')


def launch(store, label):
    """Open the window for this session unless it is already open. Returns a status word."""
    if os.name != 'nt':
        return 'unsupported'
    if running(store):
        return 'running'
    executable = shell()
    if not executable:
        raise RuntimeError('PowerShell was not found, so the viewer window cannot open.')
    target = folder(store)
    target.mkdir(parents=True, exist_ok=True)
    beat = heartbeat(store)
    beat.touch()  # Counts as open while the window starts, so two turns never open two windows.
    command = [executable, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
               '-Folder', str(target), '-Heartbeat', str(beat), '-Settings', str(settings_path(store.root)),
               '-Agent', label, '-Workspace', str(store.workspace)]
    # A console of its own, outside any job that ends the process that opened it,
    # so the window stays open after a hook or queue worker exits.
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(command, creationflags=flags, close_fds=True)  # The job forbids breakaway.
    return 'opened'


def ensure(store, label):
    """Called before each turn: reopen the window when the viewer is on. Never fails a turn."""
    try:
        if enabled(store.root):
            launch(store, label)
    except (OSError, RuntimeError, ValueError):
        pass
