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
        ensure_root(workspace)
        folder.mkdir(exist_ok=True)
        return folder
    except OSError:
        return None


BRIEF = 'BRIEF.md'


def brief_path(workspace):
    """The project brief every agent reads: `Agent_Working_Folder/BRIEF.md`, shared by all agents and hosts."""
    return Path(workspace) / ROOT / BRIEF


# The brief's three parts, in this order: the points you add (/cli brief-add); the host's notes, one dated entry
# per agent started, never replaced; and the agents running now, rewritten by CLI-MODE before every task.
POINTS, HOST, TEAM = ('## Points (added with /cli brief-add)', '## From the host',
                      '## Agents running now (kept current by CLI-MODE)')
BRIEF_HEAD = '# Project brief\n\nEvery agent working in this project reads this first.\n'


def read_brief(workspace):
    """(points, host notes as lines, agent lines) from the brief; empty when there is none.

    A brief written before its sections existed is only points: its `- ` lines.
    """
    try:
        text = brief_path(workspace).read_text(encoding='utf-8')
    except OSError:
        return [], [], []
    parts, current = {POINTS: [], HOST: [], TEAM: []}, POINTS
    for line in text.splitlines():
        if line.startswith('## '):
            current = line if line in parts else None  # A heading of CLI-MODE's own, or text to leave out.
        elif current is not None and not line.startswith('# ') and line != BRIEF_HEAD.splitlines()[-1]:
            parts[current].append(line)
    points = [line[2:] for line in parts[POINTS] if line.startswith('- ')]
    host = parts[HOST]
    while host and not host[0].strip():
        host = host[1:]
    while host and not host[-1].strip():
        host = host[:-1]
    return points, host, [line for line in parts[TEAM] if line.startswith('- ')]


def write_brief(workspace, points, host, team):
    """Write the brief's three parts (only those with anything in them); no parts at all remove the file."""
    path = brief_path(workspace)
    if not points and not host and not team:
        path.unlink(missing_ok=True)
        return
    ensure_root(workspace)
    text = BRIEF_HEAD
    if points:
        text += '\n' + POINTS + '\n\n' + ''.join('- ' + line + '\n' for line in points)
    if host:
        text += '\n' + HOST + '\n\n' + '\n'.join(host) + '\n'
    if team:
        text += '\n' + TEAM + '\n\n' + '\n'.join(team) + '\n'
    path.write_text(text, encoding='utf-8')


def brief_lines(workspace):
    """The brief's points (added with /cli brief-add), or [] when there are none."""
    return read_brief(workspace)[0]


def add_brief(workspace, text):
    """Add one point to the brief, creating it (and the git-ignored folder) if needed; the points after it."""
    points, host, team = read_brief(workspace)
    points = points + [' '.join(text.split())]
    write_brief(workspace, points, host, team)
    return points


def clear_brief(workspace):
    """Remove your points and the host's notes; True if there were any. The running agents stay listed."""
    points, host, team = read_brief(workspace)
    write_brief(workspace, [], [], team)
    return bool(points or host)


HOST_NOTE_WAITING = '(The host has not written this note yet.)'


def add_host_note(workspace, stamp, label):
    """Add a dated entry to the host's notes as `label` starts, for the host to fill in; how the host finds it.

    The host (Claude Code or Codex) writes it, since only the host knows what its conversation has been working on:
    it replaces the entry's waiting line with its note. Entries are kept, so the notes read as a history. The
    waiting line names the agent, so each is unique even while an earlier one is still unwritten (names are never
    given out twice in a conversation). None when the brief can't be written.
    """
    points, host, team = read_brief(workspace)
    heading = stamp + ', when ' + label + ' started'
    waiting = HOST_NOTE_WAITING[:-2] + ' for ' + label + '.)'
    try:
        write_brief(workspace, points, host + ([''] if host else []) + ['### ' + heading, waiting], team)
    except OSError:
        return None
    return dict(file=ROOT + '/' + BRIEF, heading='### ' + heading, placeholder=waiting)


def write_team(workspace, team):
    """Rewrite the list of agents running now (lines from state.team_lines), keeping the rest of the brief."""
    points, host, old = read_brief(workspace)
    if team != old:
        try:
            write_brief(workspace, points, host, team)
        except OSError:
            pass  # The task still goes; its brief is only out of date.


def ensure_root(workspace):
    """`Agent_Working_Folder/` with its ignore file."""
    root = Path(workspace) / ROOT
    root.mkdir(exist_ok=True)
    if not (root / '.gitignore').exists():
        (root / '.gitignore').write_text(IGNORE, encoding='utf-8')
    return root


def instruction(name, brief=False, label=None):
    """The line added to a task when it is sent. The sender skips it for an agent's own slash command, which must
    go exactly as typed (dispatch's `provider_command`). With a project brief, it asks the agent to read it; the
    agent's own label tells it which of the agents the brief lists it is."""
    return ('\n\n---\nCLI-MODE: ' + ('you are ' + label + '. ' if label else '') +
            (('F' if label else 'f') + 'irst read `' + ROOT + '/' + BRIEF + '`, the brief every agent on this '
             'project follows: your team and the host\'s notes are in it. Y' if brief else ('Y' if label else 'y')) +
            'our working folder is `' + relative(name) + '/` in this project. If this task is '
            'coding, change the project\'s files as asked. Save any other file you create (notes, reports, assets, '
            'drafts, downloads) in your working folder, not elsewhere in the project, and name the files you saved '
            'in your answer.')


ATTACHMENTS = 'attachments'
ATTACHMENT_LIMIT = 250 * 1024 * 1024  # Bytes per attached file; a larger one is left out and named in the reply.
UPLOAD_ID = re.compile(r'^[0-9a-f]{8}-(?=.)')  # Claude Code's upload prefix: `878dde39-image.jpg`.


def attach(workspace, name, files):
    """Copy the files attached to a /d into `Agent_Working_Folder/<NAME>/attachments/`: (copied, skipped).

    `copied` holds their paths relative to the project, the way the task names them. The host keeps its own copy
    only for a while (Codex's clipboard images are temp files), so the agent gets one it can open whenever it
    runs. A name already taken gets `-2`, `-3`...; `skipped` holds files that are missing or too large.
    """
    import shutil
    folder = ensure(workspace, name)
    copied, skipped = [], []
    if folder is None:
        return copied, [str(item) for item in files]
    target = folder / ATTACHMENTS
    for item in files:
        source = Path(item)
        try:
            if not source.is_file() or source.stat().st_size > ATTACHMENT_LIMIT:
                skipped.append(source.name)
                continue
            target.mkdir(exist_ok=True)
            base = UPLOAD_ID.sub('', source.name)
            stem, suffix = os.path.splitext(base)
            destination, number = target / base, 1
            while destination.exists():
                number += 1
                destination = target / ('%s-%d%s' % (stem, number, suffix))
            shutil.copyfile(source, destination)
            copied.append(destination.relative_to(workspace).as_posix())
        except OSError:
            skipped.append(source.name)
    return copied, skipped


def attachments_note(paths):
    """The paragraph naming a task's attached files, added after the working-folder line."""
    if not paths:
        return ''
    return ('\nThe user attached ' + ('this file' if len(paths) == 1 else 'these files') + ' to the task (copies in '
            'your working folder): ' + ', '.join('`' + path + '`' for path in paths) + '. Open ' +
            ('it' if len(paths) == 1 else 'them') + ' as needed.')


def clear_attachments(workspace, name):
    """Remove an agent's attached-file copies when it closes; its own files and saved answers stay."""
    import shutil
    folder = path(workspace, name)
    if folder is not None:
        shutil.rmtree(folder / ATTACHMENTS, ignore_errors=True)


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
    """What a turn saved in the agent's folder, or None when nothing changed (or it couldn't be read).

    The agent's saved answers (ANSWERS/) are CLI-MODE's own copies, shown in the reference box instead, and its
    attached files (ATTACHMENTS/) are the user's.
    """
    if before is None or after is None:
        return None
    own = (ANSWERS + '/', ATTACHMENTS + '/')
    old, new = ({path: value for path, value in listed['files'].items() if not path.startswith(own)}
                for listed in (before, after))
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


ANSWERS = 'answers'
REFERENCES_KEPT = 8
# A file path as an answer mentions it: relative (src/app.py, notes.md) or absolute, ending in an extension.
MENTION = re.compile(r'(?:[A-Za-z]:[\\/])?[\w.\-]+(?:[\\/][\w.\-]+)*\.[A-Za-z0-9]{1,10}')


def save_answer(workspace, name, label, task, text):
    """Keep an agent's full answer as `Agent_Working_Folder/<NAME>/answers/NNN-<task words>.md`; its path, or None.

    Another agent can then be given the answer by path (the reference box under each answer lists it), and
    reads the exact text, formatting included. Numbered, so the names sort in order.
    """
    folder = ensure(workspace, name)
    if folder is None or not text.strip():
        return None
    answers = folder / ANSWERS
    try:
        answers.mkdir(exist_ok=True)
        taken = [int(match[1]) for match in (re.match(r'(\d+)-', entry.name) for entry in answers.glob('*.md'))
                 if match]
        words = '-'.join(re.findall(r'[a-z0-9]+', task.casefold())[:6])[:40].strip('-') or 'answer'
        path = answers / ('%03d-%s.md' % ((max(taken) + 1) if taken else 1, words))
        path.write_text('# ' + label + '\'s answer\n\nTask: ' + ' '.join(task.split()) + '\n\n---\n\n' +
                        text.strip() + '\n', encoding='utf-8')
    except (OSError, ValueError):
        return None
    return path.relative_to(workspace).as_posix()


def references(workspace, text, changes=None, saved=None, answer=None):
    """Files to hand to another agent: those the turn created or changed, then existing ones its answer mentions.

    Paths are relative to the project, which every agent works in. A mentioned path counts only if it is a file
    in the project, so an example or made-up path never gets in.
    """
    root = Path(workspace).resolve()
    found = [item['path'] for item in (changes or {}).get('paths') or [] if (root / item['path']).is_file()]
    found += [saved['folder'] + '/' + item['path'] for item in (saved or {}).get('paths') or []
              if item['status'] != 'removed']
    for token in MENTION.findall(text or ''):
        try:
            path = (root / token).resolve()
            if path.is_file() and path.is_relative_to(root):
                found.append(path.relative_to(root).as_posix())
        except (OSError, ValueError):
            continue
    unique = [path for index, path in enumerate(found) if path not in found[:index] and path != answer]
    return unique


def box(label, answer, files):
    """The reference box's lines: the saved answer, then the files (at most REFERENCES_KEPT)."""
    lines = [label + ' answer: ' + answer] if answer else []
    if files:
        shown = files[:REFERENCES_KEPT]
        more = len(files) - len(shown)
        lines.append('Files: ' + ', '.join(shown) + (', and ' + str(more) + ' more' if more > 0 else ''))
    return lines


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
