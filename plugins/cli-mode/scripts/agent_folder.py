"""Each agent's working folder: where it saves files that are not edits to the project, and what a turn saved.

Every agent works in the project folder. A coding task changes the project's own files, which the change receipt
(changes.py) reports from git. Any other file an agent creates (notes, reports, assets, drafts) goes in
`Agent_Working_Folder/<NAME>/`, one flat folder per agent name, so the agent finds its earlier files and both the
user and the host know where to look. A `.gitignore` of `*` inside `Agent_Working_Folder/` keeps all of it out of
git (and out of the git receipt) without touching the project's own `.gitignore`. What a turn saved there comes
from listing the agent's folder before and after the turn: fast for large media, per agent even while several
work at once, and available outside git too.
"""
import os
from pathlib import Path
import re

ROOT = 'Agent_Working_Folder'
LIMIT = 5000  # Files listed per folder; past it a turn's saved files are summarised, not listed.
PATHS_KEPT = 50
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]*$')


def relative(name):
    """The folder as the agent and the user see it, relative to the project: `Agent_Working_Folder/ART`."""
    return ROOT + '/' + name


def path(workspace, name):
    """Where the agent's folder is (whether or not it exists yet), or None for no usable name.

    Names are CLI-MODE agent names (letters, digits, one dash), so they are safe folder names; anything else is
    refused rather than cleaned up into a different folder.
    """
    if not workspace or not name or not NAME.match(name):
        return None
    return Path(workspace) / ROOT / name


IGNORE = '# Agents\' working files (CLI-MODE): kept out of git.\n*\n'


def keep_ignored(workspace):
    """Put back a deleted `.gitignore` in an existing `Agent_Working_Folder/`, before a turn's git snapshot.

    Otherwise that snapshot would count the agents' files, the next one (after ensure() restored the file) would
    not, and the change receipt would report them all as removed.
    """
    root = Path(workspace) / ROOT if workspace else None
    if root is not None and root.is_dir() and not (root / '.gitignore').exists():
        try:
            (root / '.gitignore').write_text(IGNORE, encoding='utf-8')
        except OSError:
            pass


def ensure(workspace, name):
    """Create the agent's folder (and the ignore file) and return it, or None when it can't be made.

    Called only when a task is about to name the folder, so an agent's own command, or a turn refused before
    it is sent, leaves the project untouched.
    """
    folder = path(workspace, name)
    if folder is None:
        return None
    try:
        folder.parent.mkdir(exist_ok=True)
        ignore = folder.parent / '.gitignore'
        if not ignore.exists():
            ignore.write_text(IGNORE, encoding='utf-8')
        folder.mkdir(exist_ok=True)
        return folder
    except OSError:
        return None


def instruction(name):
    """The line added to a task when it is sent. The sender skips it for an agent's own slash command, which must
    go exactly as typed (dispatch's `provider_command`)."""
    return ('\n\n---\nCLI-MODE: your working folder is `' + relative(name) + '/` in this project. If this task is '
            'coding, change the project\'s files as asked. Save any other file you create (notes, reports, assets, '
            'drafts, downloads) in your working folder, not elsewhere in the project, and name the files you saved '
            'in your answer.')


def listing(folder):
    """{relative path: (size, modified)} for the files under `folder`, or None when it can't be read.

    A folder that doesn't exist yet lists as empty. The count stops at LIMIT: `truncated` is then True and
    compare() reports a summary instead of paths.
    """
    if folder is None:
        return None
    files = {}
    try:
        for top, dirs, names in os.walk(folder):
            dirs.sort()
            for entry in sorted(names):
                path = Path(top) / entry
                try:
                    stat = path.stat()
                except OSError:
                    continue  # Removed while listing.
                files[path.relative_to(folder).as_posix()] = (stat.st_size, stat.st_mtime_ns)
                if len(files) >= LIMIT:
                    return dict(files=files, truncated=True)
    except OSError:
        return None
    return dict(files=files, truncated=False)


def compare(name, before, after):
    """What a turn saved in the agent's folder, or None when nothing changed (or it couldn't be read)."""
    if before is None or after is None:
        return None
    old, new = before['files'], after['files']
    if before['truncated'] or after['truncated']:
        # Past LIMIT a listing is cut off, so a file beyond the cut would read as removed: say only that it changed.
        return dict(folder=relative(name), partial=True, saved=0, removed=0, files=0, paths=[]) if old != new else None
    paths = ([dict(path=path, status='new') for path in new if path not in old] +
             [dict(path=path, status='changed') for path in new if path in old and new[path] != old[path]] +
             [dict(path=path, status='removed') for path in old if path not in new])
    if not paths:
        return None
    return dict(folder=relative(name), saved=sum(item['status'] != 'removed' for item in paths),
                removed=sum(item['status'] == 'removed' for item in paths), files=len(paths),
                paths=sorted(paths, key=lambda item: item['path'])[:PATHS_KEPT], partial=False)


def summary(label, saved):
    """One plain line for the end of a background row: `ART saved 3 files in Agent_Working_Folder/ART/.`"""
    if not saved:
        return ''
    return label + ' ' + counts(saved) + ' in ' + saved['folder'] + '/.'


def counts(saved):
    """`saved 3 files`, `saved 2 files and removed 1`, `removed 1 file`, or, past LIMIT, `changed files`."""
    def many(number):
        return str(number) + (' file' if number == 1 else ' files')
    if saved.get('partial'):
        return 'changed files'
    if not saved['saved']:
        return 'removed ' + many(saved['removed'])
    return 'saved ' + many(saved['saved']) + (' and removed ' + str(saved['removed']) if saved['removed'] else '')
