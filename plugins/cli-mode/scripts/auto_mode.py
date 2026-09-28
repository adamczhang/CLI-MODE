"""AUTO mode (Claude Code only): the user talks to Claude, and Claude hands work to the user's AUTO agent.

DIRECT is the other mode: /d sends the user's words to an agent. The user picks the AUTO agent (and an optional
backup) once; the choice is saved for every conversation in `auto-mode.json`, like the agent timeout. Switching a
conversation to AUTO starts those agents, which then wait for work, so a handoff never waits for an agent to
start. Codex has no AUTO: it has no background task that wakes the model when an agent finishes.

Which agent does what belongs to CLI-MODE's code in AUTO (starting, replacing and closing the AUTO agents);
what each task is belongs to Claude.
"""
import json
from pathlib import Path
import posixpath
import re
import time
import uuid

import adapters
import agent_folder
import frontends
from operations import pending_work
from presentation import menu_block
from state import (MODE_PAGES, STRENGTHS, agent_entry, agent_label, agent_limit, live_agents, routing_mode,
                   target_of)

ROLES = ('agent', 'backup')
# Handoffs: Claude writes each task to a file here (git-ignored, CLI-MODE's own), then runs `handoff --task <id>`.
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}')
TASK_MAX = 800 * 1024  # Characters: about 200k tokens, most of Codex's 258k-token window.
WORKING = ('captured', 'submitting')  # A request still with its agent.
LEDGER_SHOWN = 5
# Strong: what Claude may change itself. Room for a small piece of work kept whole (a bug fix with its test, a small
# function): the 2026-09-27 usage test's smallest task (3 files, about 5 tool calls) cost Claude 92% as much handed
# off as done alone, and took 4x as long.
SMALL_EDIT = 40  # Lines one edit of Claude's may change.
TURN_FILES = 3  # Project files Claude may edit itself in one turn.
KEPT_SUBAGENTS = ('Explore', 'Plan', 'claude-code-guide', 'statusline-setup')  # Read-only helpers stay Claude's.
STRENGTH_RULES = {
    'normal': 'Delegation is Normal: you decide what to hand off.',
    'strong': ('Delegation is Strong: you may make small fixes yourself (up to about ' + str(SMALL_EDIT) + ' changed '
               'lines per edit, at most ' + str(TURN_FILES) + ' project files per turn); CLI-MODE refuses a larger '
               'edit, which then goes to the agent. Your own subagents that would write code are refused too.'),
    'max': ('Delegation is Max: every change to the project goes to the agent; you read, plan, check and write task '
            'files, and CLI-MODE refuses your own project edits.'),
}
TEMPLATE = ('Goal: what to achieve, in one or two lines\n'
            'Context: what you already know that saves exploring (the test command, the files involved, the '
            'layout and conventions to follow), then decisions and constraints\n'
            'Inputs: files to read first, such as the user\'s saved prompt (the part it needs, by its markers)\n'
            'Files: the only files or folders it may change (other agents may be changing the rest), or none when it '
            'writes only in its own working folder\n'
            'Do not: anything else to avoid; never commit or push\n'
            'Done when: the check that proves it (tests, a command, behaviour); new code comes with tests for each '
            'new function and its edge cases\n'
            'Report: what changed, what you ran, anything unresolved')
# Pass by reference (usage test, 2026-09-27: handed off, a 25k-token prompt cost Claude 7 minutes and 25-30k output
# tokens retyping its data for the agent): a prompt this long is saved where the agents can read it, and the task
# names it in its Inputs line.
PROMPT_SAVE_MIN = 4000  # Characters: a prompt this long is saved, and tasks point to it instead of restating it.
PROMPTS_KEPT = 20
# One writer per file: a writing task claims the files and folders its Files line names, and no two running tasks
# (nor Claude's own edits) may change the same file. A task without that line, and any other request, claims them all;
# `Files: none` claims no project file (work that writes only in the agent's working folder). Its result then names
# any edit of its own outside that claim (relay_view.host_text's OUTSIDE ITS CLAIM, a CHECK: look).
WHOLE = '*'
EVERYTHING = frozenset(('*', '.', 'all', 'any', 'everything', 'project', 'repo', 'repository'))
AUTO_TIMEOUT = 120  # Minutes an idle AUTO agent keeps running (its ACPX owner TTL); /cli off closes it sooner.
WARM_WINDOW = 600  # Seconds: task files this recent and not handed off yet count as waiting for an agent.
WARM_CLAIM_WAIT = 120  # Seconds `--agent new` waits for a warm agent still starting.
# Normal since 2026-09-27: in every usage test Claude did small and medium work faster itself, so it decides and
# hands off only what is worth it. Strong was the default before: a saved Strong counts only when it was chosen.
DEFAULT_STRENGTH = 'normal'
STRENGTH_LINES = {
    'normal': ('Claude works itself; long jobs', 'go to the agent.'),
    'strong': ('Claude fixes small things itself;', 'bigger work goes to the agent.'),
    'max': ('Every change goes to the agent;', 'Claude plans and checks.'),
}
# Purposes a pending activation can carry: whose agent it starts, and whether it turns AUTO on. An extra is another
# agent like the AUTO agent that Claude starts for work in parallel (`handoff --agent new`).
PURPOSES = {'auto-on': 'agent', 'auto-agent': 'agent', 'auto-backup': 'backup', 'auto-extra': 'extra'}


def config_path(root):
    return Path(root) / 'auto-mode.json'


def entry(value):
    """A saved agent choice, {agent, model, effort, access}, or None when it is missing or names no agent."""
    if not isinstance(value, dict) or not isinstance(value.get('agent'), str):
        return None
    try:
        adapters.module(value['agent'])
    except ValueError:
        return None
    saved = {key: value.get(key) for key in ('agent', 'model', 'effort', 'access')}
    if isinstance(value.get('fast'), bool):
        saved['fast'] = value['fast']  # The agent's own fast mode (Codex), when chosen.
    return saved


def load(root):
    """The user's AUTO choices: {agent, backup, strength}, each agent an entry() or None."""
    try:
        value = json.loads(config_path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {}
    value = value if isinstance(value, dict) else {}
    strength, chosen = value.get('strength'), value.get('strengthChosen') is True
    if strength not in STRENGTHS or (strength == 'strong' and not chosen):
        strength = DEFAULT_STRENGTH  # The old default, saved with the agent, is not a choice.
    config = {'agent': entry(value.get('agent')), 'backup': entry(value.get('backup')), 'strength': strength}
    if chosen:
        config['strengthChosen'] = True
    return config


def save(root, config):
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2), encoding='utf-8')
    temporary.replace(path)


def entry_of(agent, settings):
    """The entry for an agent that just started with `settings` (an adapter selection)."""
    saved = {'agent': agent, 'model': settings.get('model'), 'effort': settings.get('effortValue'),
             'access': settings.get('access')}
    if isinstance(settings.get('fast'), bool):
        saved['fast'] = settings['fast']
    return saved


def describe(root, choice, effort=True):
    """`Codex CLI, GPT-6 Sol, High` for a saved entry; `none` without one."""
    if not choice:
        return 'none'
    adapter = adapters.module(choice['agent'])
    try:
        selected = adapter.selection(root, choice['model'], choice['access'], choice.get('effort'))
    except (ValueError, KeyError, OSError, TypeError):
        selected = {}
    parts = [adapter.DISPLAY_NAME, selected.get('modelName') or choice.get('model') or 'its default model']
    if effort and selected.get('effort'):
        parts.append(selected['effort'])
    return ', '.join(parts)


def short(root, choice):
    """`Codex, GPT-6 Sol`: a saved agent in one agent-list row."""
    detail = describe(root, choice, effort=False).split(', ', 1)
    return adapters.module(choice['agent']).LABEL + (', ' + detail[1] if len(detail) > 1 else '')


def effort_options(root, choice):
    """The AUTO agent's effort levels as (label, value), when effort is a setting of its own (Codex, Claude, Grok);
    none when it is part of the model (Antigravity's) or the agent has none."""
    if not choice or adapters.descriptor(choice['agent']).get('effortRepresentation') != 'separate':
        return []
    adapter = adapters.module(choice['agent'])
    try:
        settings = adapter.selection(root, choice['model'], choice['access'], choice.get('effort'))
        return list(frontends.phase_options(root, choice['agent'], 'effort', settings, adapter.catalog(root)))
    except (ValueError, KeyError, OSError, TypeError):
        return []


def effort_name(root, choice):
    value = (choice or {}).get('effort')
    return next((label for label, option in effort_options(root, choice) if option == value), value or 'default')


def has_fast(choice):
    """True when the AUTO agent has a fast mode of its own (Codex's `fast-mode`)."""
    return bool(choice) and bool(getattr(adapters.module(choice['agent']), 'FAST_MODE_KEY', None))


def settings_rows(root, config):
    """AUTO settings, row by row: (lines, action), numbered in this order on the page and in mode_choose alike."""
    agent = config['agent']
    rows = [(['AUTO agent', '   ' + (describe(root, agent) if agent else 'not chosen yet')], 'agent'),
            (['Backup agent', '   ' + describe(root, config['backup'])], 'backup'),
            (['Delegation: ' + config['strength'].title(), *('   ' + line for line in STRENGTH_LINES[config['strength']])],
             'strength')]
    if effort_options(root, agent):
        rows.append((['Effort: ' + effort_name(root, agent)], 'effort'))
    if has_fast(agent):
        rows.append((['Fast mode: ' + ('On' if agent.get('fast') else 'Off')], 'fast'))
    if config['backup']:
        rows.append((['Remove backup'], 'clear-backup'))
    return rows


def page_text(page, root, state):
    """One page as framed menu text (menu_block adds X. Exit)."""
    config = load(root)
    if page == 'mode':
        lines = ['Mode', '', 'Now: ' + routing_mode(state).upper(), '',
                 'DIRECT  You drive the agents with', '        /d. Answers come back as', '        written.',
                 'AUTO    Talk to Claude only. It', '        hands work to your AUTO', '        agent, checks it, reports.',
                 '', 'AUTO agent: ' + (describe(root, config['agent'], effort=False) if config['agent'] else
                                       'not chosen yet'),
                 '', '1. DIRECT', '2. AUTO (default)', '3. AUTO settings', 'B. Back']
    elif page == 'auto-settings':
        lines = ['AUTO settings', '']
        for number, (row, _) in enumerate(settings_rows(root, config), 1):
            lines += [str(number) + '. ' + row[0], *row[1:]]
        lines.append('B. Back')
    elif page == 'auto-effort':
        agent = config['agent']
        lines = ['Effort', '', describe(root, agent, effort=False) if agent else 'No AUTO agent chosen yet',
                 'Now: ' + effort_name(root, agent), '']
        lines += [str(number) + '. ' + label for number, (label, _) in enumerate(effort_options(root, agent), 1)]
        lines.append('B. Back')
    else:
        lines = ['Delegation', '', 'Now: ' + config['strength'].title(), '']
        for number, strength in enumerate(STRENGTHS, 1):
            lines += [str(number) + '. ' + strength.title() + (' (default)' if strength == DEFAULT_STRENGTH else ''),
                      *('   ' + line for line in STRENGTH_LINES[strength])]
        lines.append('B. Back')
    return menu_block('\n'.join(lines))


def roster_line(root, state, config):
    """`Codex CLI COD-7K (GPT-6 Sol, High), backup Grok Build GRO-4K`: the AUTO agents running here."""
    roster = state.get('auto') or {}
    parts = []
    for role in ROLES:
        session = roster.get(role)
        if session and agent_entry(state, session) and config.get(role):
            detail = describe(root, config[role]).split(', ', 1)
            text = agent_label(state, session) + (' (' + detail[1] + ')' if len(detail) > 1 else '')
            parts.append(('backup ' if role == 'backup' else '') + text)
    return ', '.join(parts)


def auto_name(agent, taken):
    """An AUTO agent's name: its kind and the lowest number free among the running agents, `Codex-01`, `Codex-02`.
    Plain and clear in Claude's reports, and room for more agents of one kind."""
    base = adapters.module(agent).LABEL
    used = {str(name).casefold() for name in taken if name}
    for number in range(1, 100):
        name = base + '-' + str(number).zfill(2)
        if name.casefold() not in used:
            return name
    raise RuntimeError('No free AUTO name for ' + base + '.')


def adopted_line(root, state, purpose, session):
    """One line for the activation card: what the agent that just started is now."""
    label = agent_label(state, session)
    if purpose == 'auto-extra':
        return label + ' started for work in parallel.'
    if routing_mode(state) != 'auto':  # A backup chosen while no AUTO agent runs here.
        return (label + ' is now your backup agent. It works for Claude once AUTO is on with an AUTO agent '
                '(/cli mode auto).')
    if purpose == 'auto-backup':
        return label + ' is now your backup agent: it waits for work.'
    return ('AUTO is on: Claude hands work to ' + roster_line(root, state, load(root)) + '. /d asks Claude itself, with no handoff; '
            '/cli mode direct switches back.')


def adopt(state, purpose, session, agent, settings, root):
    """An activation with an AUTO purpose finished: save its agent as the user's choice and put it in this
    conversation's AUTO roster. Returns the session it replaces there, if any."""
    role = PURPOSES[purpose]
    if role == 'extra':  # Like the AUTO agent, so nothing is saved: it joins this conversation's extras.
        roster = dict(state.get('auto') or {})
        roster['extras'] = [item for item in roster.get('extras') or [] if agent_entry(state, item)] + [session]
        state['auto'] = roster
        return None
    config = load(root)
    config[role] = entry_of(agent, settings)
    save(root, config)
    roster = dict(state.get('auto') or {})
    previous = roster.get(role)
    roster[role] = session
    state['auto'] = roster
    # Choosing an AUTO agent means AUTO: it turns on, and a backup chosen while an AUTO agent runs here turns it on too.
    lead = agent_entry(state, roster.get('agent')) if roster.get('agent') else None
    if role == 'agent' or (lead and lead.get('ready')):
        state['routingMode'] = 'auto'
        state.pop('directChosen', None)
    return previous if previous != session else None


def tasks_dir(workspace):
    return Path(workspace) / agent_folder.ROOT / agent_folder.OWN / 'tasks'


def prompts_dir(workspace):
    return Path(workspace) / agent_folder.ROOT / agent_folder.OWN / 'prompts'


def save_prompt(workspace, text):
    """Keep a long AUTO prompt where the agents can read it (git-ignored, with the tasks), exactly as the user sent
    it, so a task can name it instead of Claude retyping its data. The newest PROMPTS_KEPT stay."""
    folder = prompts_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.md')
    path.write_bytes(text.encode('utf-8'))
    for old in sorted(folder.glob('*.md'))[:-PROMPTS_KEPT]:
        old.unlink(missing_ok=True)
    return path


def prompt_note(path, text):
    """What Claude is told on the turn a long prompt was saved."""
    return ('Prompt saved: ' + Path(path).as_posix() + ' (' + str(len(text)) + ' characters: the user\'s message this '
            'turn, exactly as sent). If a task needs its data, name this file in the task\'s Inputs line with the '
            'markers of the part it needs (a heading or the line before the data); do not copy the data into the task '
            'or into project files yourself.')


def task_file(workspace, task):
    if not isinstance(task, str) or not TASK_ID.fullmatch(task):
        raise ValueError('A task id is 1-40 letters, digits, _ or -, starting with a letter or digit (such as t1).')
    return tasks_dir(workspace) / (task + '.md')


def auto_requests(state):
    """This conversation's AUTO requests (handoffs, and approvals' continuations of them), oldest first."""
    return sorted(((key, record) for key, record in (state.get('requests') or {}).items()
                   if record.get('routingMode') == 'auto'), key=lambda item: item[1].get('capturedAt') or 0)


def file_key(word, workspace=None):
    """A file or folder as a claim: project-relative, `/`-separated and case-folded (Windows ignores case); WHOLE for
    the whole project; None for a path outside it. A wildcard claims the folder it is in (`src/*.py` is `src`)."""
    word = str(word or '').strip().strip('`"\'').replace('\\', '/')
    wild = re.search(r'[*?\[]', word)
    if wild:
        word = word[:wild.start()].rpartition('/')[0]
    if not word or word.casefold() in EVERYTHING:
        return WHOLE
    path = Path(word)
    if path.is_absolute() or re.match(r'[A-Za-z]:', word):
        try:
            word = path.resolve().relative_to(Path(workspace or '.').resolve()).as_posix()
        except (OSError, ValueError):
            return None
    word = posixpath.normpath(word.lstrip('/'))
    if word == '.':
        return WHOLE
    return None if word.startswith('../') or word == '..' else word.casefold()


NOT_PATHS = frozenset(('e.g', 'i.e', 'etc', 'vs'))


def path_like(word, workspace=None):
    """True for a word of a Files line that names a file or folder: it has a slash or a wildcard, ends in an
    extension (`README.md`, `.gitignore`), or is there in the project (`Makefile`). `only`, `files` and `v1.2` are
    words, not files."""
    if word.casefold() in NOT_PATHS:
        return False
    if re.search(r'[/\\*]', word) or re.search(r'\.[A-Za-z][\w-]*$', word):
        return True
    return bool(workspace and exists(Path(workspace) / word))


def task_files(text, workspace=None):
    """The claims of a writing task's Files line: [WHOLE] without one, or when it names no path (prose); [] (no project
    file) when it says `none` (or `nothing`, `no files`), or names only the agents' working folder, never the
    project's (its own notes, scripts and outputs go there).

    Every path in the line counts, in brackets too ("the report tests (tests/test_report.py)"); the rest is prose,
    whose sentence ends are not files (live, 2026-09-27: `...its test file only.` claimed `only`, and `No other
    files.` claimed `files`). A word for the whole project (`everything`) claims it only when no path is named."""
    line = next((row.split(':', 1)[1] for row in text.splitlines() if re.match(r'\s*files\s*:', row, re.I)), None)
    if line is None:
        return [WHOLE]
    words = [word.strip('`"\'').rstrip('.:!?') for word in re.split(r'[\s,;()\[\]{}]+', line)]
    words = [word for word in words if word]
    nothing = bool(re.match(r'(none|nothing|no (project )?files?)\b', ' '.join(words).casefold()))
    keys, everything = [], False
    for word in words:
        if word.casefold() in EVERYTHING:
            everything = everything or not nothing  # `no project files` names no project.
            continue
        if not path_like(word, workspace):
            continue  # Prose: `and`, `the`, `new`, `only`.
        key = file_key(word, workspace)
        if key == WHOLE:
            return [WHOLE]
        if key and working_folder(key):
            nothing = True  # The working folder: no project file.
        elif key and key not in keys:
            keys.append(key)
    return keys or ([] if nothing and not everything else [WHOLE])


def working_folder(key):
    """True for a claim key inside the agents' working folder (Agent_Working_Folder/…): never a project file."""
    root = agent_folder.ROOT.casefold()
    return key == root or key.startswith(root + '/')


def outside_claim(paths, claim, workspace=None):
    """The files among `paths` (an agent's own edits) that its writing task's `claim` does not cover: a project file
    it was not given. The working folder is always the agent's own; files outside the project are not counted."""
    found = []
    for path in paths or []:
        key = file_key(path, workspace)
        if key and key != WHOLE and not working_folder(key) and not any(overlaps(key, mine) for mine in claim):
            found.append(path)
    return found


def exists(path):
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def overlaps(one, other):
    return (WHOLE in (one, other) or one == other or one.startswith(other.rstrip('/') + '/')
            or other.startswith(one.rstrip('/') + '/'))


def claims(state, exclude=None):
    """(session, request, files) of the work in progress that may change files, on agents other than `exclude`: each
    writing handoff claims its Files line, and any other request (a /d from DIRECT) the whole project."""
    found = []
    for key, record in (state.get('requests') or {}).items():
        handoff = record.get('handoff') or {}
        if record.get('status') in WORKING and record.get('session') != exclude and not handoff.get('readOnly'):
            files = handoff.get('files')  # [] (Files: none) claims no project file; missing claims them all.
            found.append((record.get('session'), key, [WHOLE] if files is None else list(files)))
    return found


def running_with(state, request_id):
    """The other requests that ran while `request_id` did: they had started, and had not ended before it started
    (work still queued never ran)."""
    records = state.get('requests') or {}
    mine = records.get(request_id) or {}
    start = mine.get('submittedAt') or mine.get('capturedAt') or 0
    end = mine.get('endedAt') or time.time()
    return [record for key, record in records.items() if key != request_id and record.get('submittedAt')
            and record['submittedAt'] <= end and (record.get('endedAt') is None or record['endedAt'] >= start)]


def alongside(state, request_id):
    """What other work could change while `request_id` ran: the Files claims (short of the whole project) and the
    edited files of every other request that was running at the same time (it had started, and had not ended before
    this one started). Such changes in its receipt are expected, not a CHECK: look (live: two writers on
    separate claims flagged each other's files, and Claude spent a call on git to confirm it)."""
    keys = set()
    for record in running_with(state, request_id):
        keys.update(claim for claim in (record.get('handoff') or {}).get('files') or [] if claim != WHOLE)
        keys.update(found for found in (file_key(path) for path in record.get('touched') or [])
                    if found and found != WHOLE)
    return sorted(keys)


def conflict(state, files, exclude=None):
    """The first running task that may change one of `files`: (session, request, its claim), else None. One writer
    per file keeps each turn's change receipt, and each undo, its own."""
    for session, request, claimed in claims(state, exclude):
        for theirs in claimed:
            if any(overlaps(mine, theirs) for mine in files):
                return session, request, theirs
    return None


def conflict_text(state, found, files):
    session, request, theirs = found
    name = agent_label(state, session)
    task = ((state.get('requests') or {}).get(request, {}).get('handoff') or {}).get('task')
    what = 'files anywhere in the project' if theirs == WHOLE else theirs
    return (name + ' may be changing ' + what + (' (task ' + task + ')' if task else '') + ' right now, and one agent '
            'writes a file at a time.' + (' This task has no Files line, so it claims the whole project.'
                                          if files == [WHOLE] else '') +
            ' Hand it to ' + name + ' to run after that' + (', list other files in its Files line' if WHOLE not in
                                                             (theirs, *files) else '') +
            ', or wait for the result; --read-only work can run alongside.')


def unread(state):
    """AUTO requests that finished but whose result Claude has not read (relay --for-host), oldest first."""
    return [key for key, record in auto_requests(state) if record.get('status') not in WORKING
            and not record.get('hostRead')]


def work_summary(state):
    """What the agents have done in this conversation, from CLI-MODE's own records, for the user's /cli list: the
    work AUTO moved off Claude. None before the first finished handoff."""
    done = [record for _, record in auto_requests(state) if record.get('status') == 'completed']
    if not done:
        return None
    files = sum(((record.get('changes') or {}).get('files') or 0) for record in done)
    added = sum(((record.get('changes') or {}).get('added') or 0) for record in done)
    removed = sum(((record.get('changes') or {}).get('removed') or 0) for record in done)
    minutes = sum(max(0.0, (record.get('endedAt') or 0) - (record.get('submittedAt') or 0)) for record in done
                  if record.get('endedAt') and record.get('submittedAt')) / 60
    agents = len({record.get('session') for record in done})
    return ('Agents did ' + str(len(done)) + (' task' if len(done) == 1 else ' tasks') + ' for Claude on ' +
            str(agents) + (' agent' if agents == 1 else ' agents') + ': ' + str(files) +
            (' file' if files == 1 else ' files') + ' changed (+' + str(added) + ' -' + str(removed) + '), ' +
            ('%.1f' % minutes) + ' min of their work.')


def ledger_lines(state, relay_command=None, limit=LEDGER_SHOWN, active=False):
    """The AUTO ledger: unread results first, then the newest. With `relay_command(request)` (Claude's copy), an
    unread result names its exact relay; without it (the user's /cli list), it says Claude has not read it yet.
    `active` keeps only what is still working or unread (Claude's per-turn copy, lever L5: what it has read is
    already in the conversation)."""
    rows = auto_requests(state)
    if active:
        rows = [item for item in rows if item[1].get('status') in WORKING or not item[1].get('hostRead')]
        if not rows:
            return []
    if not rows:
        return ['No handoffs yet.']
    labels = state.get('followLabels') or {}
    waiting = set(unread(state))
    rows = [item for item in rows if item[0] in waiting] + [item for item in reversed(rows) if item[0] not in waiting]
    words = {'completed': 'finished', 'canceled': 'canceled', 'superseded': 'canceled', 'rejected': 'not sent',
             'uncertain': 'unconfirmed'}
    lines = []
    for key, record in rows[:limit]:
        name = labels.get(key) or agent_label(state, record.get('session'))
        handoff = record.get('handoff') or {}
        what = ' (read-only)' if handoff.get('readOnly') else ''
        if record.get('status') in WORKING:
            lines.append(name + what + ': working.')
        elif key in waiting:
            lines.append(name + what + ': ' + words.get(record.get('status'), record.get('status') or 'settled') +
                         (', NOT READ YET: `' + relay_command(key) + '`' if relay_command else
                          ', Claude has not read it yet.'))
        else:
            lines.append(name + what + ': ' + words.get(record.get('status'), record.get('status') or 'settled') +
                         ', read.')
    more = len(rows) - limit
    return lines + (['(and ' + str(more) + ' older)'] if more > 0 else [])


def agents_line(state):
    """Claude's AUTO agents and what each is doing: `Codex-01 (AUTO agent): t1, changing src/app.py; Codex-02: idle`."""
    roster = state.get('auto') or {}
    sessions = [(roster.get('agent'), 'AUTO agent'), (roster.get('backup'), 'backup')]
    sessions += [(item, None) for item in roster.get('extras') or []]
    parts = []
    for session, role in sessions:
        if not session or not agent_entry(state, session):
            continue
        work = []
        for key, record in sorted(((key, record) for key, record in (state.get('requests') or {}).items()
                                   if record.get('session') == session and record.get('status') in WORKING),
                                  key=lambda item: item[1].get('capturedAt') or 0):
            handoff = record.get('handoff') or {}
            files = handoff.get('files')
            files = [WHOLE] if files is None else files
            work.append((handoff.get('task') or 'a /d task') + ' (' + (
                'read-only' if handoff.get('readOnly') else 'may change any file' if files == [WHOLE] else
                'its working folder only' if not files else
                'changing ' + ' '.join(files[:4]) + (' …' if len(files) > 4 else '')) + ')')
        parts.append(agent_label(state, session) + (' (' + role + ')' if role else '') + ': ' +
                     (' then '.join(work) if work else 'idle'))
    return '; '.join(parts) or 'none'


NOT_RUNNING = ('CLI-MODE AUTO is on, but its AUTO agent is not running, so nothing can be handed off. Say so in one '
               'line: /cli mode auto starts it again, and /cli mode direct switches back.')


def context(root, state, workspace, handoff_command, relay_command, style=lambda text: text):
    """Everything Claude is told about AUTO: the rule, then the status. hooks/claude.py sends the rule once and
    after that only a changed status (lever L1)."""
    text = rule(root, state, workspace, handoff_command, style)
    return NOT_RUNNING if text is None else text + '\n' + status(state, relay_command)


def status(state, relay_command=None):
    """What changes between AUTO turns: the agents and what each does, and what is still working or unread."""
    rows = ledger_lines(state, relay_command, active=True)
    return 'Agents now: ' + agents_line(state) + '.' + ('\nAUTO ledger:\n' + '\n'.join('- ' + row for row in rows)
                                                         if rows else '')


def rule(root, state, workspace, handoff_command, style=lambda text: text):
    """The AUTO rule (lever A), from P0's tested wording (2026-09-27: 19 of 20 prompts routed as intended), made
    light (levers L2, L3, L6-L8): one call hands off, the result comes with the wake-up, a CHECK line says what to
    look at. Claude works itself by default and hands off only what is worth it (the usage tests of 2026-09-27:
    native was faster on every small and medium job, and each handoff cost about half a minute of Claude's turns). None while the AUTO agent is not running. `handoff_command` is the handoff command with `<id>` for the
    task id; `style` marks CLI-MODE's attribution lines as DIRECT's are (green bold, or plain bold with color off)."""
    config = load(root)
    roster = state.get('auto') or {}
    lead = roster.get('agent')
    if not lead or not agent_entry(state, lead):
        return None
    name = agent_label(state, lead)
    detail = describe(root, config['agent']).split(', ', 1)
    backup = roster.get('backup')
    backup_text = ('; backup ' + agent_label(state, backup) + ' (`--agent backup`), only for when the AUTO agent fails '
                   'or is out of usage') if backup and agent_entry(state, backup) else ''
    kind = adapters.module(agent_entry(state, lead)['backend']).LABEL
    return (
        'CLI-MODE AUTO is on: the user\'s AUTO agent, ' + name + (' (' + detail[1] + ')' if len(detail) > 1 else '') +
        ', works in this same project folder' + backup_text + ', for the work worth handing to it. You lead. ' +
        STRENGTH_RULES[config['strength']] + '\n'
        'Work as you would without CLI-MODE, and decide from the request alone, before any tool call, whether to hand '
        'it off. Hand off only: a long job (several minutes of work or more, such as a feature built to a spec, a '
        'port or refactor across files, many tests to write or fix); a request whose long message CLI-MODE saved '
        '(Prompt saved: ...); independent parts of several minutes each that can run at once; what the user asks the '
        'agent to do; and reviews, second opinions and wide research (read-only). Everything else is yours, and so is '
        'anything that depends on this conversation: when in doubt, do it yourself. A short request (a few '
        'paragraphs, no pasted data) is yours even when it has several parts, unless each part is several minutes of '
        'work: on small requests a handoff\'s fixed cost outweighs the work it moves. A handoff adds about half a '
        'minute of your own turns and the agent is slower than you, so a needless handoff costs more than doing a '
        'job yourself. Do not read the code to decide, nor to write a task or its Files line: give the goal and '
        'constraints, name the files the request names, and let the agent explore. What you already know from this '
        'conversation that would save it exploring goes in Context in a line or two (the test command, the files '
        'involved, the layout and conventions): a cheaper agent spends most of its time finding its way. Do not '
        'guess either: name what '
        'you have not checked as something for the agent to find out, not as a fact or a suspect. When the user\'s '
        'message was long, CLI-MODE saves it and says where (Prompt saved: ...): name that file in the task\'s Inputs '
        'line, with the heading and markers of the part the agent needs, and leave the part there: Context adds only '
        'what the saved prompt does not say (decisions, constraints, the other agents\' parts), and you never copy '
        'its spec or data into the task or the project yourself.\n'
        'One writer per file: a writing task\'s Files line names the only files or folders it may change (without one, '
        'it claims the whole project; `none` claims no project file, for work that writes only in its working folder), '
        'and CLI-MODE refuses a handoff, or an edit of yours, that would change a file another running task may '
        'change; a result names any edit outside its claim. Work on other files can run in parallel: hand it to an idle agent '
        '(`--agent <name>`), or start another ' + kind + ' like ' + name + ' for it with `--agent new` (15-40 s; at '
        'most ' + str(agent_limit(state)) + ' agents run). A task for a busy agent waits its turn: with more parts than '
        'the limit allows, hand the rest to running agents (`--agent <name>`) and end your report with one line '
        'saying the agent limit was reached. A request with three '
        'or more independent parts that change different files, each several minutes of work, goes out at once, one '
        'task per part on its own agent (`--agent new` beyond the idle ones), each with its own Files line: write every '
        'task file in one message, then run every handoff in the next, so the whole request goes out in two turns; parts '
        'that share files stay in one task, and a part of a minute or two stays in another part\'s task or is yours: '
        'a new agent starts cold, and costs more than such a part.\n'
        'To hand off, all in one message: (1) a line that opens with this attribution, exactly as written but with the '
        'name of the agent you hand it to, then says in plain words what you pass on:\n' +
        style('Passing to ' + name + ':') + '\n(2) the task, written with the Write tool to ' +
        tasks_dir(workspace).as_posix() + '/<id>.md, where <id> is a short new name such as t1, using this template '
        '(for a small, self-contained task, Goal and Done when are enough):\n' + TEMPLATE + '\n(3) `' +
        handoff_command + '`, run after the Write (add `--read-only` for reviews and research, `--agent <name>` or '
        '`--agent new` for another agent). CLI-MODE hands the task over and turns that same call into the background '
        'follow of the agent\'s work: there is nothing else to run, and a refusal comes back as the call\'s error. '
        'Then end your turn with one short line that opens with this attribution, the same way:\n' +
        style(name + ' is working.') + '\nThe agent\'s finish wakes you with its result, already read for you, and '
        'a CHECK line: CHECK: ok means tell the user in a few lines what was done, from the result alone, with no '
        'tool call; CHECK: look names the only things to check first. Its CHANGES line is CLI-MODE\'s own git '
        'status and diff of the project across the agent\'s turn, new files included, and its TESTS line CLI-MODE\'s '
        'own run of the project\'s tests after it: do not run git, the tests or read the changed files again to '
        'confirm them. Never poll or wait for it.\n'
        'The user switches back to driving the agents with /cli mode direct.')


class AutoMixin:
    """The Mode page, AUTO's settings, and starting the AUTO agents (Claude Code only)."""

    def mode_control(self, action, to=None, role='agent', agent=None, number=None):
        if action == 'page':
            return self.mode_page('mode')
        if action == 'set':
            return self.set_mode(to)
        if action == 'agent':
            return self.choose_auto_agent(role, agent)
        if action == 'clear-backup':
            return self.clear_backup()
        if action == 'strength':
            return self.set_strength(to)
        if action == 'effort':
            return self.set_effort(to)
        if action == 'fast':
            return self.set_fast(to)
        if action == 'choose':
            return self.mode_choose(number)
        if action == 'back':
            return self.mode_back()
        return self.mode_close()

    def mode_page(self, page='mode', message=None):
        with self.store.edit() as state:
            if (state.get('pending') or {}).get('stage') == 'verifying':
                raise RuntimeError('An agent is starting; wait for it before changing modes.')
            state['pending'] = dict(id=uuid.uuid4().hex, stage='menu', phase=page)
        result = dict(state, activationMenu=page_text(page, self.store.root, state))
        if message:
            result['message'] = message
        return result

    def close_page(self, state):
        if (state.get('pending') or {}).get('phase') in MODE_PAGES:
            state['pending'] = None

    def set_mode(self, to):
        if to not in ('auto', 'direct'):
            raise ValueError('Choose auto or direct.')
        if to == 'direct':
            with self.store.edit() as state:
                self.close_page(state)
                was = routing_mode(state)
                state.update(routingMode='direct', directChosen=True)  # Kept after /cli off too (Store.read).
                state.pop('autoRule', None)  # Back in AUTO, Claude is given the whole rule again.
            running = ' Your agents keep running; /d sends them work.' if state['active'] else \
                ' /cli starts an agent; /d sends it work.'
            return dict(state, message=('DIRECT is on' if was == 'auto' else 'DIRECT is already on') + ': you drive '
                        'the agents with /d, and Claude answers everything else.' + running)
        config = load(self.store.root)
        if not config['agent']:
            return self.open_picker('auto-on', message='Choose your AUTO agent: the agent Claude hands work to. It '
                                    'starts now and waits for work.')
        return self.start_auto()

    def start_auto(self):
        """Start whichever AUTO agents are not already running here, then turn AUTO on."""
        config = load(self.store.root)
        started = []
        for role in ROLES:
            choice = config[role]
            state = self.store.read()
            session = (state.get('auto') or {}).get(role)
            current = agent_entry(state, session) if session else None
            if not choice or (current and current.get('ready') and current.get('backend') == choice['agent']):
                continue  # Not chosen, or already running and waiting.
            self.start_role(role, choice)
            started.append(role)
        with self.store.edit() as state:
            self.close_page(state)
            was = routing_mode(state)
            state['routingMode'] = 'auto'
            state.pop('directChosen', None)
            lead = (state.get('auto') or {}).get('agent')
            target = agent_entry(state, lead) if lead else None
            if target:  # The AUTO agent is the current one, even when the backup started last.
                state.update(main=target['name'], settings=target['settings'], backend=target['backend'])
        line = roster_line(self.store.root, state, config)
        opening = 'AUTO is already on' if was == 'auto' and not started else 'AUTO is on'
        return dict(state, message=opening + ': Claude hands work to ' + line + '. /d asks Claude itself, with no handoff; /cli mode direct '
                    'switches back.')

    def start_role(self, role, choice):
        """Start one AUTO agent with its saved settings, the way bind starts one (in the hook, 13-42 s)."""
        purpose = {'backup': 'auto-backup', 'extra': 'auto-extra'}.get(role, 'auto-agent')
        self.use(choice['agent'])
        self.frontend(choice['agent'], purpose=purpose)
        return self.activate(choice['model'], choice['access'], effort=choice.get('effort'), agent=choice['agent'],
                             require_hooks=True, fast=choice.get('fast'))

    def open_picker(self, purpose, agent=None, message=None):
        """The activation flow (agent list, then its page), marked so its activation becomes an AUTO agent."""
        result = self.frontend(agent or 'home', purpose=purpose)
        if message:
            result['message'] = message
        return result

    def choose_auto_agent(self, role, agent=None):
        """`/cli mode agent|backup [<agent> [<model>]]`: the picker, that agent's page, or a direct choice."""
        purpose = 'auto-backup' if role == 'backup' else 'auto-agent'
        typed = ((self.store.read().get('turnRoute') or {}).get('choice') or '').strip()
        what = 'backup agent' if role == 'backup' else 'AUTO agent'
        if not agent or not typed:
            return self.open_picker(purpose, agent, message='Choose your ' + what + '. It starts now' +
                                    (' and waits for work.' if self.auto_is_on() else '.'))
        adapter = self.use(agent)
        settings = adapter.selection(self.store.root, **adapter.DEFAULTS)
        options = frontends.phase_options(self.store.root, agent, 'model', settings, adapter.catalog(self.store.root))
        matches = frontends.match_choice(options, typed, 'model')
        if len(matches) != 1:
            return {'message': ('Several ' if matches else 'No ') + adapter.DISPLAY_NAME + ' models match "' + typed +
                    '". /cli mode ' + role + ' ' + agent + ' opens its page to choose.'}
        choice = {'agent': agent, 'model': matches[0], 'access': settings['access'],
                  'effort': settings.get('effortValue') if matches[0] == settings['model'] else None}
        config = load(self.store.root)
        config[role] = choice
        save(self.store.root, config)
        if role == 'backup' and not config['agent']:
            return {'message': 'Your backup agent is now ' + describe(self.store.root, choice) + '. Choose an AUTO '
                    'agent too (/cli mode agent); AUTO then starts both.'}
        state = self.store.read()
        current = agent_entry(state, (state.get('auto') or {}).get(role))
        running = (current.get('backend'), (current.get('settings') or {}).get('model')) if current else None
        if current and current.get('ready') and running != (choice['agent'], choice['model']):
            self.start_role(role, choice)  # Another agent or model: it replaces the running one (adopt, retire).
        return self.start_auto()  # Choosing an AUTO agent means AUTO: start what is not running, and turn it on.

    def auto_is_on(self):
        return routing_mode(self.store.read()) == 'auto'

    def clear_backup(self):
        config = load(self.store.root)
        config['backup'] = None
        save(self.store.root, config)
        with self.store.edit() as state:
            roster = dict(state.get('auto') or {})
            old = roster.pop('backup', None)
            state['auto'] = roster
        closed = self.retire(old) if old and routing_mode(state) == 'auto' else None
        return self.mode_page('auto-settings', message='Backup removed.' + (' ' + closed if closed else ''))

    def set_strength(self, to):
        if to not in STRENGTHS:
            raise ValueError('Choose normal, strong or max.')
        config = load(self.store.root)
        config.update(strength=to, strengthChosen=True)
        save(self.store.root, config)
        return self.mode_page('auto-settings', message='Delegation: ' + to.title() + '.')

    def mode_choose(self, number):
        page = (self.store.read().get('pending') or {}).get('phase')
        config = load(self.store.root)
        actions = {
            'mode': {1: lambda: self.set_mode('direct'), 2: lambda: self.set_mode('auto'),
                     3: lambda: self.mode_page('auto-settings')},
            'auto-settings': {number: self.settings_action(key, config) for number, (_, key) in
                              enumerate(settings_rows(self.store.root, config), 1)},
            'auto-effort': {number: (lambda value=value: self.set_effort(value)) for number, (_, value) in
                            enumerate(effort_options(self.store.root, config['agent']), 1)},
            'auto-strength': {index: (lambda value=value: self.set_strength(value))
                              for index, value in enumerate(STRENGTHS, 1)},
        }.get(page)
        if not actions or number not in actions:
            raise ValueError('Choose a number from the page.')
        return actions[number]()

    def settings_action(self, key, config):
        """What an AUTO settings row does when its number is chosen."""
        return {'agent': lambda: self.choose_auto_agent('agent'), 'backup': lambda: self.choose_auto_agent('backup'),
                'strength': lambda: self.mode_page('auto-strength'), 'effort': lambda: self.mode_page('auto-effort'),
                'fast': lambda: self.set_fast('off' if (config['agent'] or {}).get('fast') else 'on'),
                'clear-backup': self.clear_backup}[key]

    def set_effort(self, to):
        """The AUTO agent's effort (`/cli mode effort <level>`, or the Effort page): saved, and taken in place by the
        running AUTO agents of its kind."""
        config = load(self.store.root)
        choice = config['agent']
        options = effort_options(self.store.root, choice)
        if not options:
            raise ValueError('Your AUTO agent has no effort of its own to set' + (
                ': its effort is part of its model, so /cli mode agent chooses both.' if choice else ' yet.'))
        wanted = str(to or '').strip().casefold()
        value = next((option for label, option in options if wanted in (label.casefold(), option.casefold())), None)
        if value is None:
            raise ValueError('Choose one of ' + ', '.join(label for label, _ in options) + '.')
        config['agent'] = dict(choice, effort=value)
        save(self.store.root, config)
        notes = self.apply_to_running(effort=value)
        return self.mode_page('auto-settings', message='Effort: ' + effort_name(self.store.root, config['agent']) +
                              '.' + (' ' + ' '.join(notes) if notes else ''))

    def set_fast(self, to):
        """Codex's own fast mode for the AUTO agent (`/cli mode fast on|off`, or the settings page)."""
        if to not in ('on', 'off'):
            raise ValueError('Choose on or off.')
        config = load(self.store.root)
        if not has_fast(config['agent']):
            raise ValueError('Fast mode is Codex\'s own setting; your AUTO agent is ' +
                             (describe(self.store.root, config['agent'], effort=False) if config['agent'] else
                              'not chosen yet') + '.')
        config['agent'] = dict(config['agent'], fast=to == 'on')
        save(self.store.root, config)
        notes = self.apply_to_running(fast=to == 'on')
        return self.mode_page('auto-settings', message='Fast mode: ' + to.title() + '.' +
                              (' ' + ' '.join(notes) if notes else ''))

    def apply_to_running(self, effort=None, fast=None):
        """Give the running AUTO agent, and the extras like it, a new effort or fast mode in place."""
        state = self.store.read()
        roster = state.get('auto') or {}
        lead = agent_entry(state, roster.get('agent')) if roster.get('agent') else None
        if not lead:
            return []
        sessions = [lead['name']] + [item for item in roster.get('extras') or []
                                     if (agent_entry(state, item) or {}).get('backend') == lead['backend']]
        return [note for note in (self.reconfigure(session, effort, fast) for session in sessions) if note]

    def reconfigure(self, session, effort=None, fast=None):
        """A running agent takes a new effort or fast mode in place: the same session and conversation, through the
        setting controls a running agent takes, with no restart. One still working keeps its settings for now."""
        state = self.store.read()
        target = agent_entry(state, session)
        if not target or not target.get('ready'):
            return None
        name = agent_label(state, session)
        if pending_work(state, session=session):
            return name + ' is working, so it keeps its settings until its next start.'
        settings = target['settings']
        with self.store.edit() as latest:
            latest['pending'] = dict(id=uuid.uuid4().hex, stage='menu', phase='access', entrypoint=target['backend'],
                                     backend=target['backend'], tuning=True, session=session, draft={})
        try:
            self.activate(settings['model'], settings['access'],
                          effort=effort if effort is not None else settings.get('effortValue'),
                          agent=target['backend'], fast=fast if fast is not None else settings.get('fast'))
        except (RuntimeError, ValueError) as exc:
            with self.store.edit() as latest:
                if (latest.get('pending') or {}).get('session') == session:
                    latest['pending'] = None
            return name + ' could not take it: ' + str(exc)
        return name + ' now works with it.'

    def mode_back(self):
        page = (self.store.read().get('pending') or {}).get('phase')
        if page in ('auto-strength', 'auto-effort'):
            return self.mode_page('auto-settings')
        if page == 'auto-settings':
            return self.mode_page('mode')
        state = self.store.read()
        if routing_mode(state) == 'auto' and state['active']:
            return self.mode_close()  # With AUTO's agents running, /cli is the Mode page: no agent list behind it.
        return self.frontend('home')

    def mode_close(self):
        with self.store.edit() as state:
            self.close_page(state)
        return dict(state, message='Mode page closed. /cli mode opens it again.')

    def handoff(self, task, agent=None, read_only=False, request=None, check=False):
        """Hand Claude's task file to an AUTO agent: captured as a /d is, marked as Claude's own (routingMode auto),
        so its result wakes Claude instead of going to the user word for word.

        The approval hook normally runs this itself and turns Claude's call into the follow (lever L2). A new agent
        (`--agent new`) takes too long to start in a hook: `check` then runs only the checks that can refuse it, and
        the background task runs the rest under the `request` id the hook chose (hooks/claude.py:handoff_approval)."""
        if request is not None and not re.fullmatch(r'[0-9a-f]{32}', request):
            raise ValueError('A handoff --request is 32 lowercase hex characters.')
        state = self.store.read()
        if routing_mode(state) != 'auto':
            raise RuntimeError('AUTO is off, so nothing was handed off. The user turns it on with /cli mode auto.')
        if not state['active']:
            raise RuntimeError('AUTO is off (no agent is running), so nothing was handed off. The user starts it with '
                               '/cli.')
        path = task_file(self.store.workspace, task)
        if not path.is_file():
            raise RuntimeError('No task file at ' + path.as_posix() + '. Write the task there with the Write tool '
                               'first, then run the handoff again.')
        text = path.read_text(encoding='utf-8', errors='replace').strip()
        if not text:
            raise RuntimeError('The task file ' + path.as_posix() + ' is empty. Nothing was handed off.')
        if len(text) > TASK_MAX:
            raise RuntimeError('The task file is over ' + str(TASK_MAX // 1024) + ' KB. Keep the task to what the '
                               'agent needs; it can read the project itself.')
        files = None if read_only else task_files(text, self.store.workspace)
        if agent and agent.casefold() == 'new':
            # Another agent like the AUTO agent, for work in parallel: checked first, since it takes 15-40 s to start.
            lead = agent_entry(state, self.handoff_target(state))
            found = files and conflict(state, files)
            if found:
                raise RuntimeError(conflict_text(state, found, files) + ' No agent was started.')
            warm = any(item.get('warm') and not item.get('claimed') for item in state.get('owned') or [])
            if len(state.get('owned') or []) >= agent_limit(state) and not warm:  # A warm one is already counted.
                raise RuntimeError(str(len(state['owned'])) + ' agents are running, the limit, so no agent was started. '
                                   'Hand this to one of them with --agent <name>; it waits its turn there. (The user '
                                   'raises the limit with /cli agents max <n>.)')
            if check:
                first = ' '.join(text.splitlines()[0].split())
                goal = first[5:].strip() if first.casefold().startswith('goal:') else first
                return dict(checked=True, label=adapters.module(lead['backend']).LABEL + ' (new) · ' + (
                    goal[:24].rstrip() + '…' if len(goal) > 24 else goal))
            session = self.start_extra()
            state = self.store.read()
        else:
            session = self.handoff_target(state, agent)
        target = agent_entry(state, session)
        name = agent_label(state, session)
        if read_only and target.get('actsWithoutAsking'):
            raise RuntimeError(name + ' runs commands and edits files without asking first, so it cannot take '
                               'read-only work. Hand it to another agent, or drop --read-only.')
        first = ' '.join(text.splitlines()[0].split())  # The template's Goal line names the work best.
        goal = first[5:].strip() if first.casefold().startswith('goal:') else first
        label = name + ' · ' + (goal[:30].rstrip() + '…' if len(goal) > 30 else goal)
        request = request or uuid.uuid4().hex
        with self.store.edit() as latest:
            if routing_mode(latest) != 'auto':
                raise RuntimeError('AUTO was turned off; nothing was handed off.')
            found = files and conflict(latest, files, exclude=session)  # One writer per file; its own queue waits.
            if found:
                raise RuntimeError(conflict_text(latest, found, files) + ' Nothing was handed off.')
            self.store.capture(latest, request, text, session=session)
            latest['requests'][request]['handoff'] = dict(task=task, readOnly=bool(read_only),
                                                          **({'files': files} if files is not None else {}))
            labels = latest.setdefault('followLabels', {})  # The follow's pane row: `<Agent NAME> · <goal>`.
            labels.pop(request, None)
            labels[request] = label
            for stale in list(labels)[:-20]:
                labels.pop(stale)
        started = self.ensure_pump(session)
        return dict(requestId=request, agent=name, task=task, readOnly=bool(read_only), label=label,
                    worker=started.get('worker'))

    def note_handoff_error(self, request, message):
        """A handoff the background task could not make (a new agent that did not start): its wake-up says why."""
        with self.store.edit() as state:
            errors = state.setdefault('handoffErrors', {})
            errors.pop(request, None)
            errors[request] = message
            for stale in list(errors)[:-10]:
                errors.pop(stale)

    def handoff_target(self, state, agent=None):
        """The session a handoff goes to: the AUTO agent, `backup`, or a running agent the user named."""
        roster = state.get('auto') or {}
        if not agent or agent.casefold() in ('agent', 'auto'):
            session = roster.get('agent')
        elif agent.casefold() == 'backup':
            session = roster.get('backup')
            if not session:
                raise RuntimeError('No backup agent is set; hand this to the AUTO agent (no --agent).')
        else:
            session, _ = target_of(agent, state)
            if not session:
                raise RuntimeError('No running agent is called ' + agent + '. Running: ' +
                                   (', '.join(live_agents(state)) or 'none') + '.')
        target = agent_entry(state, session) if session else None
        if not target or not target.get('ready'):
            raise RuntimeError('The AUTO agent is not running, so nothing was handed off. Tell the user: /cli mode auto '
                               'starts it again.')
        return session

    def start_extra(self):
        """Start another agent like the running AUTO agent (its kind, model, effort and access), named after it:
        `Codex-02`. It joins this conversation's extras, closes with the others, and idles out like them.

        Extras start at the same time: each owns its own entry while it starts (`starting`), never the menu's one
        pending activation (live, 2026-09-28: starts queued one after another, about 20 s each, so the fifth part of
        five waited over a minute to begin)."""
        warm = self.claim_warm()
        if warm:
            return warm
        session = self.start_alongside(self.lead_choice())
        if not agent_entry(self.store.read(), session):
            raise RuntimeError('The new agent did not start, so nothing was handed off.')
        return session

    def lead_choice(self):
        """Settings for another agent like the running AUTO agent (its kind, model, effort, access, fast mode)."""
        state = self.store.read()
        lead = agent_entry(state, self.handoff_target(state))
        settings = lead.get('settings') or {}
        return {'agent': lead['backend'], 'model': settings.get('model'), 'access': settings.get('access'),
                'effort': settings.get('effortValue'), 'fast': settings.get('fast')}

    def claim_warm(self, wait=WARM_CLAIM_WAIT, poll=1.0):
        """A warm extra (started ahead by `warm`, while Claude wrote the task files) for `--agent new`: a ready one at
        once, else one still starting, once it is ready. None when there is none, or it failed to start."""
        with self.store.edit() as state:
            found = next((item for item in state['owned'] if item.get('warm') and not item.get('claimed')), None)
            if found is None:
                return None
            found['claimed'] = True
            name = found['name']
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            item = agent_entry(self.store.read(), name)
            if item is None:
                return None  # Its start failed: a new one starts instead.
            if item.get('ready'):
                with self.store.edit() as state:
                    for entry in state['owned']:
                        if entry['name'] == name:
                            entry.pop('warm', None)
                            entry.pop('claimed', None)
                return name
            time.sleep(poll)
        return None

    def warm_needed(self, state, adding=None):
        """Task files written and not handed off yet outnumber the agents free to take them (idle, or warming and
        unclaimed), and another agent may start: then one starts now, before its handoff (just in time)."""
        if len(state.get('owned') or []) >= agent_limit(state):
            return False
        handed = {(record.get('handoff') or {}).get('task') for _, record in auto_requests(state)}
        now, folder = time.time(), tasks_dir(self.store.workspace)
        try:
            written = {path.stem for path in folder.glob('*.md') if now - path.stat().st_mtime < WARM_WINDOW}
        except OSError:
            written = set()
        pending = (written | ({adding} if adding else set())) - handed
        busy = {record.get('session') for _, record in auto_requests(state) if record.get('status') in WORKING}
        free = sum(1 for item in state.get('owned') or [] if not item.get('claimed') and (
            (item.get('ready') and item['name'] not in busy) or (item.get('warm') and item.get('starting'))))
        return len(pending) > free

    def warm_ahead(self, task):
        """The approval hook, as Claude writes task file `task`: when it needs an agent none is free to take, reserve
        one and start it in a background process, so it is ready by its handoff (live, 2026-09-28: each extra's start
        took about 40 s after its handoff; Claude writes a batch's task files well before handing them off)."""
        if not self.warm_needed(self.store.read(), adding=task):
            return None
        owned, _ = self.reserve_alongside(self.lead_choice(), warm=True)
        import os
        import subprocess
        import sys
        from queue_worker import CONTROLLER
        command = [sys.executable, str(CONTROLLER), '--thread', self.store.thread, '--workspace',
                   self.store.workspace, '--data-root', str(self.store.root), 'warm', '--token', owned['starting']]
        options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
                   'close_fds': True}
        try:
            if os.name == 'nt':
                flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                try:  # Outside the hook's job, which ends its processes with it.
                    subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
                except OSError:
                    subprocess.Popen(command, creationflags=flags, **options)
            else:
                subprocess.Popen(command, start_new_session=True, **options)
        except OSError:
            self.cleanup(owned)  # Not started: its handoff starts one itself.
            return None
        return owned['alias']

    def warm(self, token):
        """`warm --token`: start the reserved warm extra (the slow part, 15-40 s), detached from Claude's turn."""
        state = self.store.read()
        owned = next((item for item in state['owned'] if item.get('starting') == token), None)
        if owned is None:
            return dict(started=False)
        self.use(owned['backend'])
        self.finish_alongside(owned, token, state['generation'])
        return dict(started=True, agent=owned['alias'])

    def start_alongside(self, choice):
        """Start an extra AUTO agent while others start or work: its entry is reserved (name, limit) at once, the slow
        start runs outside the store's lock, and it joins the extras when ready."""
        owned, generation = self.reserve_alongside(choice)
        return self.finish_alongside(owned, owned['starting'], generation)

    def reserve_alongside(self, choice, warm=False):
        import uuid
        from state import ACTS_WITHOUT_ASKING
        target = self.use(choice['agent']).ID
        settings = self.adapter.selection(self.store.root, choice['model'], choice['access'], choice.get('effort'))
        if choice.get('fast') is not None and getattr(self.adapter, 'FAST_MODE_KEY', None):
            settings['fast'] = bool(choice['fast'])
        token = uuid.uuid4().hex
        with self.store.edit() as state:
            if len(state['owned']) >= agent_limit(state):
                raise RuntimeError(str(len(state['owned'])) + ' agents are running, the limit, so no agent was '
                                   'started. Hand this to one of them with --agent <name>; it waits its turn there. '
                                   '(The user raises the limit with /cli agents max <n>.)')
            owned = dict(name='cli-mode-' + self.store.key[:12] + '-' + uuid.uuid4().hex[:12],
                         workspace=self.store.workspace, backend=target, role='main', settings=settings, ready=False,
                         starting=token, alias=auto_name(target, [item.get('alias') for item in state['owned']]),
                         timeout=AUTO_TIMEOUT)
            if warm:
                owned['warm'] = True
            if target in ACTS_WITHOUT_ASKING:
                owned['actsWithoutAsking'] = True
            if getattr(self.backend, 'profile', None):
                owned['acpxProfile'] = self.backend.profile
            if hasattr(self.backend, 'prepare'):
                self.backend.prepare(owned)
            state['owned'].append(owned)
            generation = state['generation']
        return owned, generation

    def finish_alongside(self, owned, token, generation):
        target, settings = owned['backend'], owned['settings']
        try:
            provider = self.provision(owned, generation, token)
            with self.store.edit() as state:
                if not self.valid(state, generation, token):
                    raise RuntimeError('The start was canceled (/cli off), so nothing was handed off.')
                for item in state['owned']:
                    if item.get('starting') == token:
                        item.pop('starting')
                        item.update(ready=True, providerSession=provider, lastUsedAt=time.time())
                state['active'] = True
                adopt(state, 'auto-extra', owned['name'], target, settings, self.store.root)
        except BaseException:
            self.cleanup(owned)
            raise
        return owned['name']

    def relay_for_host(self, request_id, wait=25.0, poll=.25, answer_max=None):
        """A handoff's result for Claude to read (not to post): plain lines, then the agent's answer. It marks the
        result read, which keeps it out of every user relay and clears the wake-up's Stop guard. The wake-up builds
        it itself (lever L3, `answer_max` sharing its room between results); `relay --for-host` is the fallback."""
        import relay_view
        from queue_worker import request_label
        state = self.store.read()
        record = (state.get('requests') or {}).get(request_id)
        if not record or record.get('routingMode') != 'auto':
            raise RuntimeError('No AUTO handoff ' + request_id + ' in this conversation.')
        started = time.monotonic()
        while True:
            view = self.observe(request_id, 0, limit=1)
            status = view['receipt']['status']
            if status not in WORKING or time.monotonic() - started >= wait:
                break
            time.sleep(poll)
        label = request_label(state, request_id)
        if status in WORKING:
            return dict(requestId=request_id, done=False, status=status,
                        text=label + ' is still working on this handoff. Its follow wakes you when it finishes: end '
                                     'the turn now, without polling.')
        batch, position = [], 0
        while True:  # A settled request's log is final.
            view = self.observe(request_id, position, limit=500)
            if not view['events']:
                break
            batch, position = batch + view['events'], view['cursor']
        public = [event for event in batch if event.get('type') in self.RELAY_PUBLIC]
        latest = self.store.read()
        finished = (latest.get('requests') or {}).get(request_id) or record  # Its touched files, now it has settled.
        stopped = next((item.get('approval') for item in latest.get('owned') or []
                        if (item.get('approval') or {}).get('requestId') == request_id), None)
        handoff = record.get('handoff') or {}
        task = handoff.get('task')
        changed = [(item['path'], file_key(item['path'])) for item in
                   ((view['receipt'] or {}).get('changes') or {}).get('paths') or []]
        own = None if finished.get('unlocated') else finished.get('touched')
        if own is not None and not running_with(latest, request_id):
            # Alone, a change its edit tools did not name came from a command it ran: its own too (live, 2026-09-27:
            # a Claude agent writing through shell heredocs got CHECK: look on every result, and Claude ran git).
            named = {path.casefold() for path in own}
            own = list(own) + [path for path, _ in changed if path.casefold() not in named]
        near = alongside(latest, request_id) if own is not None else []
        mine = {path.casefold() for path in own or []}
        expected = [path for path, key in changed if path.casefold() not in mine and key and key != WHOLE
                    and any(overlaps(key, claim) for claim in near)]
        claim = None if handoff.get('readOnly') else handoff.get('files')
        outside = []
        if claim is not None and WHOLE not in claim:  # Its Files line named what it may change: did it keep to it?
            if own is not None:
                theirs = own
            else:  # Its edits name no files: the receipt's changes that no work running alongside explains.
                other = alongside(latest, request_id)
                theirs = [path for path, key in changed if key and not any(overlaps(key, item) for item in other)]
            outside = outside_claim(theirs, claim, self.store.workspace)
        text = relay_view.host_text(
            request_id, label, status, public, view['receipt'], read_only=handoff.get('readOnly'),
            task=task_file(self.store.workspace, task).as_posix() if task and TASK_ID.fullmatch(task) else None,
            stopped=stopped, access=(agent_entry(latest, record.get('session')) or {}).get('settings'),
            touched=own, answer_max=answer_max or relay_view.HOST_ANSWER_MAX, alongside=expected, outside=outside)
        with self.store.edit() as latest:
            saved = (latest.get('requests') or {}).get(request_id)
            if saved:
                saved.update(relayed=True, hostRead=True)
            latest.setdefault('relayProgress', {})[request_id] = dict(cursor=position, done=True)
            if latest.get('autoWake') == request_id:
                latest.pop('autoWake')
        return dict(requestId=request_id, done=True, status=status, text=text)

    def retire(self, session):
        """Close an AUTO agent that was replaced, unless it is still working (then say so)."""
        state = self.store.read()
        target = agent_entry(state, session)
        if not target:
            return None
        name = target.get('alias') or session
        if pending_work(state, session=session):
            return name + ' is still working and keeps running; it closes with /cli mode direct and /cli close.'
        self.close(name)
        return name + ' was closed.'
