"""What an agent's turn changed in its folder: a git snapshot before and after, and their difference.

A snapshot is a git tree of the folder as it is on disk, tracked and untracked files alike (ignored files are
left out). It is written through a temporary copy of the repository's index, so the user's staging area never
changes; the only trace is the unreferenced objects git keeps until its own garbage collection. Outside a git
repository, or when git is unavailable or slow, there is no snapshot and so no receipt: a turn is never held up
or failed by this.

Agents that share a folder work side by side, so a receipt says what changed in the folder while the agent
worked, which may include another agent's edits.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

TIMEOUT = 20
PATHS_KEPT = 50
DIFF_MAX = 24000  # Stays under a text host's tool-output limit, as relays do.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _git(workspace, *args, env=None, text=True):
    result = subprocess.run(['git', '-C', str(workspace), *args], capture_output=True, text=text, timeout=TIMEOUT,
                            env=env, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                            **(dict(encoding='utf-8', errors='replace') if text else {}))
    if result.returncode:
        raise RuntimeError('git ' + args[0] + ' failed')
    return result.stdout


def snapshot(workspace):
    """The folder's current content as a git tree ID, or None when it can't be taken."""
    folder = tempfile.mkdtemp(prefix='cli-mode-index-')
    try:
        index = Path(_git(workspace, 'rev-parse', '--path-format=absolute', '--git-path', 'index').strip())
        temporary = Path(folder) / 'index'
        if index.is_file():
            shutil.copyfile(index, temporary)  # Unchanged files keep their cached hashes: only edits are read.
        env = dict(os.environ, GIT_INDEX_FILE=str(temporary))
        _git(workspace, 'add', '--all', '--', '.', env=env)
        # Only this folder's part of the repository: the agent works here.
        prefix = _git(workspace, 'rev-parse', '--show-prefix').strip()
        return _git(workspace, 'write-tree', *(['--prefix=' + prefix] if prefix else []), env=env).strip() or None
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def compare(workspace, before, after):
    """The receipt of one turn: files changed with lines added and removed, and the two snapshots."""
    if not before or not after:
        return None
    try:
        out = _git(workspace, 'diff-tree', '-r', '--numstat', '-z', '--no-renames', before, after)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    files = []
    for entry in filter(None, out.split('\0')):
        added, removed, path = (entry.split('\t', 2) + ['', ''])[:3]
        files.append(dict(path=path, added=int(added) if added.isdigit() else None,
                          removed=int(removed) if removed.isdigit() else None))  # None: a binary file.
    return dict(before=before, after=after, files=len(files), paths=files[:PATHS_KEPT],
                added=sum(item['added'] or 0 for item in files), removed=sum(item['removed'] or 0 for item in files))


def diff_text(workspace, receipt):
    """The turn's full diff as unified text, cut at DIFF_MAX with a note; None if it can't be read."""
    if not receipt or not receipt.get('before') or not receipt.get('after'):
        return None
    try:
        out = _git(workspace, 'diff', '--no-color', '--no-renames', receipt['before'], receipt['after'])
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    if len(out) > DIFF_MAX:
        cut = out.rfind('\n', 0, DIFF_MAX)
        rest = out[cut:].count('\n')
        out = out[:cut] + '\n... (' + str(rest) + ' more lines; see git diff ' + receipt['before'][:12] + ' ' + \
            receipt['after'][:12] + ')'
    return out
