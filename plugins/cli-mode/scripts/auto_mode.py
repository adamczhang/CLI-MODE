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
import re
import time
import uuid

import adapters
import agent_folder
import frontends
from operations import pending_work
from presentation import menu_block
from state import MODE_PAGES, STRENGTHS, agent_entry, agent_label, live_agents, routing_mode, target_of

ROLES = ('agent', 'backup')
# Handoffs: Claude writes each task to a file here (git-ignored, CLI-MODE's own), then runs `handoff --task <id>`.
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}')
TASK_MAX = 100 * 1024  # Characters.
WORKING = ('captured', 'submitting')  # A request still with its agent.
LEDGER_SHOWN = 5
SMALL_EDIT = 20  # Strong: lines one edit of Claude's may change.
TURN_FILES = 2  # Strong: project files Claude may edit itself in one turn.
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
            'Context: files, decisions and constraints the task needs\n'
            'Do not: files or areas to leave alone; never commit or push\n'
            'Done when: the check that proves it (tests, a command, behaviour)\n'
            'Report: what changed, what you ran, anything unresolved')
DEFAULT_STRENGTH = 'strong'
STRENGTH_LINES = {
    'normal': ('Claude decides what to hand off.',),
    'strong': ('Claude fixes small things itself;', 'bigger work goes to the agent.'),
    'max': ('Every change goes to the agent;', 'Claude plans and checks.'),
}
# Purposes a pending activation can carry: whose agent it starts, and whether it turns AUTO on.
PURPOSES = {'auto-on': 'agent', 'auto-agent': 'agent', 'auto-backup': 'backup'}


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
    return {key: value.get(key) for key in ('agent', 'model', 'effort', 'access')}


def load(root):
    """The user's AUTO choices: {agent, backup, strength}, each agent an entry() or None."""
    try:
        value = json.loads(config_path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {}
    value = value if isinstance(value, dict) else {}
    strength = value.get('strength')
    return {'agent': entry(value.get('agent')), 'backup': entry(value.get('backup')),
            'strength': strength if strength in STRENGTHS else DEFAULT_STRENGTH}


def save(root, config):
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2), encoding='utf-8')
    temporary.replace(path)


def entry_of(agent, settings):
    """The entry for an agent that just started with `settings` (an adapter selection)."""
    return {'agent': agent, 'model': settings.get('model'), 'effort': settings.get('effortValue'),
            'access': settings.get('access')}


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


def page_text(page, root, state):
    """One page as framed menu text (menu_block adds X. Exit)."""
    config = load(root)
    if page == 'mode':
        lines = ['Mode', '', 'Now: ' + routing_mode(state).upper(), '',
                 'DIRECT  You drive the agents with', '        /d. Answers come back as', '        written.',
                 'AUTO    Talk to Claude only. It', '        hands work to your AUTO', '        agent, checks it, reports.',
                 '', 'AUTO agent: ' + (describe(root, config['agent'], effort=False) if config['agent'] else
                                       'not chosen yet'),
                 '', '1. DIRECT', '2. AUTO', '3. AUTO settings', 'B. Back']
    elif page == 'auto-settings':
        lines = ['AUTO settings', '', '1. AUTO agent', '   ' + (describe(root, config['agent']) if config['agent']
                                                              else 'not chosen yet'),
                 '2. Backup agent', '   ' + describe(root, config['backup']),
                 '3. Delegation: ' + config['strength'].title(),
                 *('   ' + line for line in STRENGTH_LINES[config['strength']])]
        lines += (['4. Remove backup'] if config['backup'] else []) + ['B. Back']
    else:
        lines = ['Delegation', '', 'Now: ' + config['strength'].title(), '']
        for number, strength in enumerate(STRENGTHS, 1):
            lines += [str(number) + '. ' + strength.title(), *('   ' + line for line in STRENGTH_LINES[strength])]
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


def adopted_line(root, state, purpose, session):
    """One line for the activation card: what the agent that just started is now."""
    label = agent_label(state, session)
    if purpose == 'auto-on':
        return ('AUTO is on: Claude hands work to ' + roster_line(root, state, load(root)) + '. /d is off; '
                '/cli mode direct switches back.')
    what = 'backup agent' if purpose == 'auto-backup' else 'AUTO agent'
    return (label + ' is now your ' + what + (': it waits for work.' if routing_mode(state) == 'auto' else
                                              '. /cli mode auto hands it work.'))


def adopt(state, purpose, session, agent, settings, root):
    """An activation with an AUTO purpose finished: save its agent as the user's choice and put it in this
    conversation's AUTO roster. Returns the session it replaces there, if any."""
    role = PURPOSES[purpose]
    config = load(root)
    config[role] = entry_of(agent, settings)
    save(root, config)
    roster = dict(state.get('auto') or {})
    previous = roster.get(role)
    roster[role] = session
    state['auto'] = roster
    if purpose == 'auto-on':
        state['routingMode'] = 'auto'
    return previous if previous != session else None


def tasks_dir(workspace):
    return Path(workspace) / agent_folder.ROOT / agent_folder.OWN / 'tasks'


def task_file(workspace, task):
    if not isinstance(task, str) or not TASK_ID.fullmatch(task):
        raise ValueError('A task id is 1-40 letters, digits, _ or -, starting with a letter or digit (such as t1).')
    return tasks_dir(workspace) / (task + '.md')


def auto_requests(state):
    """This conversation's AUTO requests (handoffs, and approvals' continuations of them), oldest first."""
    return sorted(((key, record) for key, record in (state.get('requests') or {}).items()
                   if record.get('routingMode') == 'auto'), key=lambda item: item[1].get('capturedAt') or 0)


def writer(state, exclude=None):
    """(session, request) of work in progress that may change files, on an agent other than `exclude`: anything
    unfinished but a read-only handoff. One writer at a time keeps each turn's change receipt its own."""
    for key, record in (state.get('requests') or {}).items():
        if (record.get('status') in WORKING and record.get('session') != exclude
                and not (record.get('handoff') or {}).get('readOnly')):
            return record.get('session'), key
    return None


def unread(state):
    """AUTO requests that finished but whose result Claude has not read (relay --for-host), oldest first."""
    return [key for key, record in auto_requests(state) if record.get('status') not in WORKING
            and not record.get('hostRead')]


def ledger_lines(state, relay_command=None, limit=LEDGER_SHOWN):
    """The AUTO ledger: unread results first, then the newest. With `relay_command(request)` (Claude's copy), an
    unread result names its exact relay; without it (the user's /cli list), it says Claude has not read it yet."""
    rows = auto_requests(state)
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


def context(root, state, workspace, handoff_command, relay_command):
    """What Claude is told on every AUTO turn (lever A), from P0's tested wording (2026-09-27: 19 of 20 prompts
    routed as intended). `handoff_command` is the exact handoff command with `<id>` for the task id."""
    config = load(root)
    roster = state.get('auto') or {}
    lead = roster.get('agent')
    if not lead or not agent_entry(state, lead):
        return ('CLI-MODE AUTO is on, but its AUTO agent is not running, so nothing can be handed off. Say so in one '
                'line: /cli mode auto starts it again, and /cli mode direct switches back.')
    name = agent_label(state, lead)
    detail = describe(root, config['agent']).split(', ', 1)
    backup = roster.get('backup')
    backup_text = ('; backup ' + agent_label(state, backup) + ' (`--agent backup`), only for when the AUTO agent fails '
                   'or is out of usage, and for read-only work while it writes') if backup and agent_entry(
                       state, backup) else ''
    short = (agent_entry(state, lead).get('alias') or name)
    return (
        'CLI-MODE AUTO is on. The user turned it on: that is their explicit request that you hand coding work to '
        'their CLI agent. You lead; their AUTO agent, ' + name + (' (' + detail[1] + ')' if len(detail) > 1 else '') +
        ', does the work in this same project folder' + backup_text + '. ' + STRENGTH_RULES[config['strength']] + '\n'
        'Hand off: self-contained work such as implementing a feature to a spec, writing tests, fixing failing tests '
        'until they pass, ports and refactors, code reviews and second opinions (read-only), and wide research. Keep '
        'for yourself: small edits (one file, a few lines), quick questions you can answer from a short read, and '
        'anything that depends on this conversation. Do not read the code just to write a task: give the goal and '
        'constraints and let the agent explore. Do not guess either: name what you have not checked as something for '
        'the agent to find out, not as a fact or a suspect. One writer: while a writing handoff runs, do not edit the '
        'project yourself.\n'
        'To hand off: (1) post one line, "Passing to ' + short + ': <what>"; (2) write the task with the Write tool to '
        + tasks_dir(workspace).as_posix() + '/<id>.md, where <id> is a short new name such as t1, using this '
        'template:\n' + TEMPLATE + '\n(3) run `' + handoff_command + '` (add `--read-only` for reviews and research); '
        'it prints the follow command, which you run next, as is (CLI-MODE makes it a background task); (4) end your '
        'turn with a short status for the user. The agent\'s finish wakes you: then read its result with the relay '
        'command CLI-MODE gives you, check it (the change receipt, the test result, the agent\'s report), and tell the '
        'user in your own words what was done and anything unresolved. Never poll or wait for it.\n'
        '/d is off in AUTO; the user switches back with /cli mode direct.\n'
        'AUTO ledger:\n' + '\n'.join('- ' + line for line in ledger_lines(state, relay_command)))


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
                state['routingMode'] = 'direct'
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
            lead = (state.get('auto') or {}).get('agent')
            target = agent_entry(state, lead) if lead else None
            if target:  # The AUTO agent is the current one, even when the backup started last.
                state.update(main=target['name'], settings=target['settings'], backend=target['backend'])
        line = roster_line(self.store.root, state, config)
        opening = 'AUTO is already on' if was == 'auto' and not started else 'AUTO is on'
        return dict(state, message=opening + ': Claude hands work to ' + line + '. /d is off; /cli mode direct '
                    'switches back.')

    def start_role(self, role, choice):
        """Start one AUTO agent with its saved settings, the way bind starts one (in the hook, 13-42 s)."""
        purpose = 'auto-backup' if role == 'backup' else 'auto-agent'
        self.use(choice['agent'])
        self.frontend(choice['agent'], purpose=purpose)
        return self.activate(choice['model'], choice['access'], effort=choice.get('effort'), agent=choice['agent'],
                             require_hooks=True)

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
        if self.auto_is_on():
            self.start_role(role, choice)  # adopt() saves it and replaces the old one.
            return self.start_auto()
        config = load(self.store.root)
        config[role] = choice
        save(self.store.root, config)
        return {'message': 'Your ' + what + ' is now ' + describe(self.store.root, choice) + '. It starts when AUTO '
                'is on: /cli mode auto.'}

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
        config['strength'] = to
        save(self.store.root, config)
        return self.mode_page('auto-settings', message='Delegation: ' + to.title() + '.')

    def mode_choose(self, number):
        page = (self.store.read().get('pending') or {}).get('phase')
        config = load(self.store.root)
        actions = {
            'mode': {1: lambda: self.set_mode('direct'), 2: lambda: self.set_mode('auto'),
                     3: lambda: self.mode_page('auto-settings')},
            'auto-settings': {1: lambda: self.choose_auto_agent('agent'), 2: lambda: self.choose_auto_agent('backup'),
                              3: lambda: self.mode_page('auto-strength'),
                              **({4: self.clear_backup} if config['backup'] else {})},
            'auto-strength': {index: (lambda value=value: self.set_strength(value))
                              for index, value in enumerate(STRENGTHS, 1)},
        }.get(page)
        if not actions or number not in actions:
            raise ValueError('Choose a number from the page.')
        return actions[number]()

    def mode_back(self):
        page = (self.store.read().get('pending') or {}).get('phase')
        if page == 'auto-strength':
            return self.mode_page('auto-settings')
        if page == 'auto-settings':
            return self.mode_page('mode')
        if routing_mode(self.store.read()) == 'auto':
            return self.mode_close()  # In AUTO, /cli is the Mode page: there is no agent list behind it.
        return self.frontend('home')

    def mode_close(self):
        with self.store.edit() as state:
            self.close_page(state)
        return dict(state, message='Mode page closed. /cli mode opens it again.')

    def handoff(self, task, agent=None, read_only=False):
        """Hand Claude's task file to an AUTO agent: captured as a /d is, marked as Claude's own (routingMode auto),
        so its result wakes Claude (relay --for-host) instead of going to the user word for word."""
        state = self.store.read()
        if routing_mode(state) != 'auto':
            raise RuntimeError('AUTO is off, so nothing was handed off. The user turns it on with /cli mode auto.')
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
        session = self.handoff_target(state, agent)
        target = agent_entry(state, session)
        name = agent_label(state, session)
        if read_only and target.get('actsWithoutAsking'):
            raise RuntimeError(name + ' runs commands and edits files without asking first, so it cannot take '
                               'read-only work. Hand it to another agent, or drop --read-only.')
        if not read_only:
            busy = writer(state, exclude=session)
            if busy:
                raise RuntimeError(agent_label(state, busy[0]) + ' is still working on a change in this project (one '
                                   'writer at a time). Wait for its result, or hand this over with --read-only.')
        first = ' '.join(text.splitlines()[0].split())  # The template's Goal line names the work best.
        goal = first[5:].strip() if first.casefold().startswith('goal:') else first
        label = name + ' · ' + (goal[:30].rstrip() + '…' if len(goal) > 30 else goal)
        request = uuid.uuid4().hex
        with self.store.edit() as latest:
            if routing_mode(latest) != 'auto':
                raise RuntimeError('AUTO was turned off; nothing was handed off.')
            self.store.capture(latest, request, text, session=session)
            latest['requests'][request]['handoff'] = dict(task=task, readOnly=bool(read_only))
            labels = latest.setdefault('followLabels', {})  # The follow's pane row: `<Agent NAME> · <goal>`.
            labels.pop(request, None)
            labels[request] = label
            for stale in list(labels)[:-20]:
                labels.pop(stale)
        started = self.ensure_pump(session)
        return dict(requestId=request, agent=name, task=task, readOnly=bool(read_only), label=label,
                    worker=started.get('worker'))

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

    def relay_for_host(self, request_id, wait=25.0, poll=.25):
        """A handoff's result for Claude to read (not to post): plain lines, then the agent's answer. It marks the
        result read, which keeps it out of every user relay and clears the wake-up's Stop guard."""
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
        stopped = next((item.get('approval') for item in latest.get('owned') or []
                        if (item.get('approval') or {}).get('requestId') == request_id), None)
        handoff = record.get('handoff') or {}
        task = handoff.get('task')
        text = relay_view.host_text(
            request_id, label, status, public, view['receipt'], read_only=handoff.get('readOnly'),
            task=task_file(self.store.workspace, task).as_posix() if task and TASK_ID.fullmatch(task) else None,
            stopped=stopped, access=(agent_entry(latest, record.get('session')) or {}).get('settings'))
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
