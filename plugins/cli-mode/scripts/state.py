"""Conversation metadata and scoped temporary input; no stored credentials."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import time
import help_view
import host
import names
from presentation import menu_block
from progress import DEFAULT_PROGRESS_MODE, PROGRESS_MODES, progress_mode


REGISTRY = names.REGISTRY
DEFAULT_ROUTING_MODE = 'direct'
MAX_QUEUED_REQUESTS = 32
DEFAULT_AGENT_LIMIT = 4
MAX_AGENT_LIMIT = 8
# An idle agent's process exits after this long; its next /d starts it again in the same conversation.
DEFAULT_TIMEOUT = 60
TIMEOUT_RANGE = (5, 1440)
_BACKENDS = None
_BACKEND_IDS = None
_BACKEND_WORDS = None


def data_root():
    # Same location for host commands and hooks; the host decides where (host.py).
    return host.data_root()


def _registry():
    """Cached registry lookups.

    The routing hook parses every message, and a single menu render asks for
    the backend list once per backend per check, so this file is read once per
    process rather than on every lookup.
    """
    global _BACKENDS, _BACKEND_IDS, _BACKEND_WORDS
    if _BACKEND_IDS is None:
        backends = json.loads(REGISTRY.read_text(encoding='utf-8'))['backends']
        _BACKENDS = backends
        _BACKEND_IDS = tuple(item['id'] for item in backends)
        words = {}
        for item in backends:
            # Its ID, its three-letter tag (cla, cod, gro...) and any other alias all name it.
            for word in [item['id'], item['tag']] + list(item.get('aliases') or []):
                key = word.casefold()
                if key in words and words[key] != item['id']:
                    raise ValueError('Ambiguous CLI-MODE backend word: ' + word)
                words[key] = item['id']
        _BACKEND_WORDS = words
    return _BACKEND_IDS, _BACKEND_WORDS


def backend_records():
    """The registry records themselves, cached alongside the ID lookups."""
    _registry()
    return deepcopy(_BACKENDS)


def backend_ids():
    """Canonical registered backend IDs."""
    return _registry()[0]


def resolve_backend(word):
    """Map a control word (canonical ID or registered alias) to its backend ID."""
    return _registry()[1].get(str(word).casefold())


@contextmanager
def lock(path, timeout=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        until = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise RuntimeError('CLI-MODE state is busy; retry this control operation.')
                time.sleep(.025)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, thread, workspace, root=None):
        if not thread or not isinstance(thread, str):
            raise ValueError('A real Claude Code session_id is required.' if host.claude() else
                             'A real Codex session_id / CODEX_THREAD_ID is required.')
        self.thread = thread
        self.workspace = str(Path(workspace).resolve(strict=True))
        self.root = Path(root) if root else data_root()
        # One conversation never silently activates a second workspace/backend.
        self.key = hashlib.sha256(thread.encode()).hexdigest()
        self.path = self.root / 'sessions' / (self.key + '.json')
        self.lock_path = self.path.with_suffix('.lock')

    def read(self):
        if not self.path.exists():
            return dict(schema=1, thread=self.thread, workspace=self.workspace,
                        active=False, pending=None, generation=0, backend=None,
                        settings=None, main=None, owned=[], inflight={}, routingMode=DEFAULT_ROUTING_MODE,
                        progressMode=DEFAULT_PROGRESS_MODE, helpMenu=None)
        # Windows can briefly deny a reader while another process atomically
        # replaces this file. Retry that transient sharing conflict; a lasting
        # denial still fails closed instead of looking like an inactive mode.
        until = time.monotonic() + .5
        while True:
            try:
                raw = self.path.read_text(encoding='utf-8')
                break
            except (PermissionError, FileNotFoundError):
                if time.monotonic() >= until:
                    raise
                time.sleep(.01)
        value = json.loads(raw)
        if value.get('schema') != 1 or value.get('thread') != self.thread:
            raise ValueError('Unsupported or mismatched CLI-MODE state; do not dispatch.')
        value.setdefault('routingMode', DEFAULT_ROUTING_MODE)
        if value['routingMode'] == 'passthrough':
            # Passthrough was removed: a prompt reaches the agent only through /d. A saved Passthrough
            # conversation opens in Direct mode; a request it captured still sends as captured.
            value['routingMode'] = DEFAULT_ROUTING_MODE
        value.pop('modeMenu', None)  # The routing-mode menu went with it.
        value.setdefault('progressMode', DEFAULT_PROGRESS_MODE)
        value.setdefault('helpMenu', None)
        if value['helpMenu'] in ('index', 'start', 'routing', 'settings', 'activity', 'setup', 'defaults'):
            value['helpMenu'] = 'commands'  # Preserve an open help view across upgrades.
        if value['helpMenu'] not in (None, 'commands'):
            raise ValueError('Unsupported CLI-MODE help page; inspect state before dispatch.')
        progress_mode(value)
        routing_mode(value)  # Fail closed on corrupt routing policy.
        if 'runner' in value:
            # One worker per agent now; a saved conversation-wide worker drained the current agent's queue.
            runner = value.pop('runner')
            if runner and value.get('main'):
                value.setdefault('runners', {})[value['main']] = runner
        used = value.get('usedNames') or []
        for item in value['owned']:
            if not item.get('alias'):
                # A session saved before names existed: its name is derived from the session, so it stays put.
                item['alias'] = names.generate(item.get('backend'), item['name'], used)
                used = used + [item['alias']]
        # Earlier request receipts could survive without an operation to inspect
        # or acknowledge. Give each unresolved legacy receipt a stable recovery
        # operation; never infer that its provider work was safe to replay.
        for request_id, record in value.get('requests', {}).items():
            if record['status'] in ('submitting', 'uncertain'):
                operation = record.setdefault('operation', request_id)
                if operation not in value['inflight']:
                    value['inflight'][operation] = dict(session=record['session'], kind='prompt',
                        phase='dispatching', requestId=request_id, uncertain=True,
                        running=record['status'] == 'submitting', submitterPid=record.get('submitterPid'))
        if os.path.normcase(value['workspace']) != os.path.normcase(self.workspace):
            if value['active'] or value.get('pending') or value['owned'] or value['inflight']:
                raise ValueError('This conversation owns another workspace: ' + value['workspace'] +
                                 '. Run off against that workspace before starting a new ' +
                                 ('session' if host.claude() else 'task') + '.')
            value['workspace'] = self.workspace
        return value

    def request_path(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
            raise ValueError('Invalid captured request ID.')
        return self.root / 'requests' / self.key / (request_id + '.txt')

    def cancel_path(self, operation):
        return self.request_path(operation).parent / 'operations' / (operation + '.cancel')

    def signal_cancel(self, state, session=None):
        """Persist cancellation where an already-spawned bridge can observe it."""
        for operation, entry in state['inflight'].items():
            if session is None or entry['session'] == session:
                path = self.cancel_path(operation)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

    def discard_captured(self, state):
        """Called under the state lock; never discard an admitted operation."""
        for request_id, record in state.get('requests', {}).items():
            if record['status'] == 'captured':
                record['status'] = 'superseded'

    def recover_captures(self, state):
        """Under the state lock, remove payloads not owned by a captured receipt.

        A submitting caller already read its payload under this same lock. Keep
        cleanup scoped to this conversation, including after a crashed capture.
        """
        folder = self.root / 'requests' / self.key
        retained = {request_id for request_id, record in state.get('requests', {}).items()
                    if record['status'] == 'captured'}
        if folder.exists():
            for path in folder.glob('*.txt'):
                if re.fullmatch(r'[0-9a-f]{32}', path.stem) and path.stem not in retained:
                    path.unlink(missing_ok=True)
        operations = folder / 'operations'
        if operations.exists():
            for path in operations.glob('*.txt'):
                if (re.fullmatch(r'[0-9a-f]{32}', path.stem)
                        and not state['inflight'].get(path.stem, {}).get('running')):
                    path.unlink(missing_ok=True)
            for path in operations.glob('*.cancel'):
                if re.fullmatch(r'[0-9a-f]{32}', path.stem) and path.stem not in state['inflight']:
                    path.unlink(missing_ok=True)

    def capture(self, state, request_id, text, session=None, named=False):
        """Persist exact hook input separately from routing metadata, under lock.

        The request goes to `session` (the current agent's unless the prompt named another); `named` is how many
        words after /d name the agents it went to, which are removed before it is sent.
        """
        if sum(record['status'] == 'captured' for record in state.get('requests', {}).values()) >= MAX_QUEUED_REQUESTS:
            raise RuntimeError('CLI-MODE queue is full (32 messages); wait for earlier work to finish.')
        session = session or state['main']
        target = agent_entry(state, session)
        path = self.request_path(request_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        record = dict(status='captured', capturedAt=time.time(), generation=state['generation'],
                      routingMode=routing_mode(state), session=session)
        if target:
            # Kept with the request, so its answer is still labelled after the agent is closed.
            record.update(agent=target.get('backend'), name=target.get('alias'))
        if named:
            record['named'] = int(named)
        state.setdefault('requests', {})[request_id] = record

    @contextmanager
    def edit(self):
        with lock(self.lock_path):
            value = self.read()
            self.recover_captures(value)
            before = deepcopy(value)
            committed = False
            try:
                yield value
                if value != before:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(dir=self.path.parent, suffix='.tmp')
                    try:
                        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
                            json.dump(value, out, indent=2)
                            out.write('\n')
                            out.flush()
                            os.fsync(out.fileno())
                        until = time.monotonic() + .5
                        while True:
                            try:
                                os.replace(name, self.path)
                                break
                            except PermissionError:
                                if time.monotonic() >= until:
                                    raise
                                time.sleep(.01)
                    finally:
                        if os.path.exists(name):
                            os.unlink(name)
                committed = True
            finally:
                self.recover_captures(value if committed else before)


INACTIVE_HINT = '/cli to activate.  Say /help to see options'

PREFIXES = ('/', '$')
# Claude Code also names a plugin's commands by plugin: /cli-mode:cli, /cli-mode:d.
NAMESPACE = '/cli-mode:'
ROUTING_MODES = ('direct',)  # A prompt reaches the agent only through /d (Passthrough was removed).
MODE_REMOVED = ('Prompts reach the agent only through /d <prompt>; every other message stays with {host}. '
                'Passthrough mode was removed.')


def inactive_hint():
    # Claude Code's own /help is built in; CLI-MODE's help is /cli help there.
    return '/cli to activate.  Say /cli help to see options' if host.claude() else INACTIVE_HINT


def help_hint():
    return 'Say /cli help to see options.' if host.claude() else 'Say /help to see options.'


def routing_mode(state):
    mode = state.get('routingMode', DEFAULT_ROUTING_MODE)
    if mode == 'passthrough':
        return DEFAULT_ROUTING_MODE  # Removed; Store.read() also rewrites it.
    if mode not in ROUTING_MODES:
        raise ValueError('Unsupported CLI-MODE routing mode; inspect state before dispatch.')
    return mode


def agent_entry(state, session=None):
    """The owned entry of `session`, or of the current (hot) agent."""
    session = session or state.get('main')
    return next((item for item in state.get('owned') or [] if item['name'] == session), None)


def live_agents(state, every=False):
    """Every ready agent, name -> session, the current one first. `every` adds agents not ready (one whose
    setting change failed, say), which only closing can target."""
    ready = [item for item in state.get('owned') or [] if item.get('alias') and (every or item.get('ready'))]
    ready.sort(key=lambda item: item['name'] != state.get('main'))
    return {item['alias']: item['name'] for item in ready} if every or state.get('active') else {}


def last_used(state, session):
    """When an agent was last started, made current or sent a prompt."""
    entry = agent_entry(state, session) or {}
    return max([entry.get('lastUsedAt') or 0] + [record.get('capturedAt') or 0 for record in
                                                 (state.get('requests') or {}).values()
                                                 if record.get('session') == session])


def parse_minutes(text):
    """`90`, `90m`, `2h` or `1.5h` as whole minutes within TIMEOUT_RANGE, else None."""
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)?', (text or '').strip().casefold())
    if not match:
        return None
    minutes = float(match.group(1)) * (60 if (match.group(2) or 'm').startswith('h') else 1)
    minutes = int(round(minutes))
    return minutes if TIMEOUT_RANGE[0] <= minutes <= TIMEOUT_RANGE[1] else None


def timeout_path(root):
    return Path(root) / 'agent-timeout.json'


def default_timeout(root):
    """Minutes an idle agent keeps running: the saved default for every conversation, else one hour."""
    try:
        minutes = json.loads(timeout_path(root).read_text(encoding='utf-8')).get('minutes')
    except (OSError, ValueError, AttributeError):
        return DEFAULT_TIMEOUT
    return minutes if isinstance(minutes, int) and TIMEOUT_RANGE[0] <= minutes <= TIMEOUT_RANGE[1] else DEFAULT_TIMEOUT


def save_default_timeout(root, minutes):
    path = timeout_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'minutes': minutes}) + '\n', encoding='utf-8')


def agent_limit(state):
    return state.get('agentLimit') or DEFAULT_AGENT_LIMIT


def agent_label(state, session=None, record=None):
    """How an agent is named in every line CLI-MODE writes: its kind and name, `Codex COD-7K`.

    A request's own record wins (its agent may have been closed since); then the owned entry.
    """
    import adapters
    record = record or {}
    entry = agent_entry(state, record.get('session') or session) or {}
    kind = record.get('agent') or entry.get('backend') or state.get('backend') or 'agy'
    name = record.get('name') or entry.get('alias')
    try:
        label = adapters.module(kind).LABEL
    except ValueError:
        label = 'Agent'
    return label + (' ' + name if name else '')


def passing_line(label):
    return 'Passing to ' + label + '...'


def tag_kind(word):
    """The agent kind a three-letter tag (`gro`) names, or None. Only tags: a kind's full name (`codex`) is too
    likely to start an ordinary prompt."""
    _registry()
    return next((item['id'] for item in _BACKENDS if item['tag'] == (word or '').casefold()), None)


def target_of(word, state, every=False):
    """(session, name) for a word naming a live agent, or a hint route explaining why it doesn't.

    A word names an agent by its name (`COD-7K`, `cod7k`, `-7K`, a custom name) or by its kind's tag (`cod`)
    when exactly one agent of that kind is running.
    """
    agents = live_agents(state, every)
    found = names.resolve(word, agents)
    if found and found[0] == 'match':
        name = next(alias for alias, session in agents.items() if session == found[1])
        return found[1], name
    if found:
        return None, {'route': 'hint', 'text': word + ' could mean ' + ' or '.join(found[1]) +
                      '; use the full name. Nothing was sent.'}
    kind = tag_kind(word)
    if kind:
        import adapters
        same = [(alias, session) for alias, session in agents.items()
                if (agent_entry(state, session) or {}).get('backend') == kind]
        if len(same) == 1:
            return same[0][1], same[0][0]
        if same:
            return None, {'route': 'hint', 'text': word + ' could mean ' + ' or '.join(sorted(alias for alias, _ in same)) +
                          '; use its name. Nothing was sent.'}
        return None, {'route': 'hint', 'text': 'No ' + adapters.module(kind).LABEL + ' agent is running. Nothing was sent.'}
    return None, None


def no_agent(word):
    return {'route': 'hint', 'text': 'No running agent is named ' + word.upper() + '. /cli list shows them.'}


def direct_payload(message):
    """Remove the complete /d or $d token and one separator, preserving payload."""
    trigger = r'^\s*(?:/cli-mode:d|[/\$]d)(?=\s|$)' if host.claude() else r'^\s*[/\$]d(?=\s|$)'
    match = re.match(trigger, message, re.IGNORECASE)
    if not match:
        return None
    rest = message[match.end():]
    if rest.startswith('\r\n'):
        return rest[2:]
    return rest[1:] if rest and rest[0].isspace() else rest


def is_command(word, name):
    """A complete prefixed token. /client is ordinary text, never /cli."""
    if host.claude() and word == NAMESPACE + name:
        return True
    return any(word == prefix + name for prefix in PREFIXES)


# Each pair is one command under two names, identical in every form.
VERB_ALIASES = {'stop': 'close', 'off': 'close', 'commands': 'help', 'settings': 'menu', 'spawn': 'bind',
                'agents': 'list', 'permissions': 'access'}


def close_route(state, word=None):
    """`/cli close [name|all]` (= stop = off): the last or every agent turns CLI-MODE off; one of several closes."""
    agents = live_agents(state, every=True)
    if not word:
        return {'route': 'close-menu'} if len(agents) > 1 else {'route': 'off'}
    if word.casefold() == 'all':
        return {'route': 'off'}
    session, name = target_of(word, state, every=True)
    if session is None:
        return name or (no_agent(word) if agents else {'route': 'hint', 'text': inactive_hint()})
    return {'route': 'off'} if len(agents) == 1 else {'route': 'close', 'session': session, 'name': name}


def close_menu_reply(message, state):
    """A reply to the close chooser: a number, a name, A (all) or X. Anything else is not one."""
    reply = message.strip()
    folded = reply.casefold()
    live = live_agents(state, every=True)
    sessions = [session for session in state.get('closeMenu') or [] if session in live.values()]
    if folded == 'x':
        return {'route': 'hint', 'text': 'Nothing was closed.'}
    if folded in ('a', 'all'):
        return {'route': 'off'}
    if folded.isdigit() and 1 <= int(folded) <= len(sessions):
        return close_route(state, agent_entry(state, sessions[int(folded) - 1])['alias'])
    if reply and len(reply.split()) == 1 and target_of(reply, state, every=True)[0]:
        return close_route(state, reply)
    return None


def named_tail(choice, state):
    """(session, name, rest) when `choice` starts with a live agent's name, else (None, None, choice)."""
    words = choice.split(None, 1)
    if words:
        session, name = target_of(words[0], state)
        if session:
            return session, name, words[1].strip() if len(words) > 1 else ''
    return None, None, choice


def bind_route(choice, state):
    """`/cli bind|spawn <agent> [name]`: a new agent, up to the limit; None when `choice` is not that."""
    given = choice.split()
    agent = resolve_backend(given[0]) if given else None
    if not agent or len(given) > 2:
        return None
    live = live_agents(state)
    if len(live) >= agent_limit(state):
        return {'route': 'hint', 'text': str(len(live)) + ' agents are running, the limit. Close one first '
                '(/cli close <name>), or raise the limit with /cli agents max <n>.'}
    if len(given) == 1:
        return {'route': 'bind', 'agent': agent}
    error = names.custom_error(given[1], live)
    return {'route': 'hint', 'text': error} if error else {'route': 'bind', 'agent': agent, 'name': given[1].upper()}


def approval_route(verb, choice, state):
    """`/cli approve [name] [always]` or `/cli deny [name]`: the answer to a stopped turn's permission question.

    The answer becomes the agent's next /d, a short note that goes on with the task: ACPX can't hold a request
    open, so the turn stopped (dispatch.remember_approval), and an approval is a rule sent with the next prompt
    (dispatch.approval_policy). Without a name it answers the only agent waiting for one.
    """
    import presentation
    if not state['active']:
        return {'route': 'hint', 'text': inactive_hint()}
    words = choice.split()
    always = verb == 'approve' and bool(words) and words[-1].casefold() == 'always'
    words = words[:-1] if always else words
    if len(words) > 1:
        return {'route': 'hint', 'text': 'Use /cli approve [name] [always] or /cli deny [name].'}
    waiting = {alias: session for alias, session in live_agents(state).items()
               if (agent_entry(state, session) or {}).get('approval')}
    if words:
        session, name = target_of(words[0], state)
        if not session:
            return name or no_agent(words[0])
        if session not in waiting.values():
            return {'route': 'hint', 'text': agent_label(state, session) + ' is not waiting for an approval.'}
    elif len(waiting) == 1:
        name, session = next(iter(waiting.items()))
    elif waiting:
        return {'route': 'hint', 'text': ' and '.join(agent_label(state, session) for session in waiting.values()) +
                ' are waiting for an answer: add a name, for example /cli ' + verb + ' ' + next(iter(waiting)) + '.'}
    else:
        return {'route': 'hint', 'text': 'No agent is waiting for an approval.'}
    asked = agent_entry(state, session)['approval']
    words, what = presentation.approval_words(asked), ' '.join((asked.get('detail') or asked.get('title') or '').split())
    if verb == 'approve':
        note = ('Approved: you may ' + words + (' (' + what + ')' if what else '') + (' from now on' if always else '') +
                '. Do the step you asked about now, then carry on with the task.')
    else:
        note = ('Not approved: do not ' + words + (' (' + what + ')' if what else '') + '. Carry on with the task '
                'without it, or say what you need instead.')
    return {'route': 'direct', 'session': session, 'name': name,
            'approval': dict(answer=verb, always=always, rule=presentation.approval_rule(asked), text='/d ' + note)}


def cli_route(verb, choice, state):
    """One `/cli <verb> [choice]` control, with `verb` already folded and de-aliased."""
    if not verb:
        return {'route': 'home'}
    if verb == 'help' and not choice:
        return {'route': 'help', 'text': help_view.render()}
    if verb == 'display' and host.claude():
        # How Claude Code shows CLI-MODE's own replies: instant notices or chat messages.
        return {'route': 'display', 'choice': choice.casefold()}
    if verb in ('color', 'colour') and host.claude():
        # Green titles and names in Claude Code's chat, or plain bold for the terminal.
        return {'route': 'color', 'choice': choice.casefold()}
    if verb == 'shortcuts' and not choice and host.claude():
        # Bare /cli and /d in autocomplete, for installs that did not run install-claude.ps1.
        return {'route': 'shortcuts'}
    selected = resolve_backend(verb)
    if selected and not choice:
        return {'route': 'frontend', 'agent': selected}
    if verb == 'bind':
        found = bind_route(choice, state)
        if found:
            return found
    if verb in ('approve', 'deny'):
        return approval_route(verb, choice, state)
    if verb == 'close':
        if len(choice.split()) <= 1:
            return close_route(state, choice)
        return {'route': 'hint', 'text': 'Use /cli close <name>, /cli close all, or /cli close.'}
    if verb == 'list':
        if not choice:
            return {'route': 'agents'}
        limit = choice.casefold().split()
        if len(limit) == 2 and limit[0] == 'max' and limit[1].isdigit() and 1 <= int(limit[1]) <= MAX_AGENT_LIMIT:
            return {'route': 'agents', 'max': int(limit[1])}
        return {'route': 'hint', 'text': 'Use /cli list, or /cli agents max <1-' + str(MAX_AGENT_LIMIT) + '>.'}
    if verb in ('use', 'cancel', 'queue', 'resume', 'menu', 'model', 'effort', 'access') and not state['active']:
        return {'route': 'hint', 'text': 'CLI-MODE: Agent not activated. /CLI to setup'
                if verb in ('menu', 'model') else inactive_hint()}
    if verb == 'timeout':
        words = choice.split()
        if not words:
            return {'route': 'timeout'}
        minutes = parse_minutes(words[-1])
        if minutes is None or len(words) > 2:
            return {'route': 'hint', 'text': 'Use /cli timeout <minutes>, or /cli timeout <name> <minutes>: from ' +
                    str(TIMEOUT_RANGE[0]) + ' minutes to 24 hours, for example 90, 90m or 2h.'}
        if len(words) == 1:
            return {'route': 'timeout', 'minutes': minutes}
        session, name = target_of(words[0], state, every=True)
        return ({'route': 'timeout', 'minutes': minutes, 'session': session, 'name': name} if session
                else name or no_agent(words[0]))
    if verb == 'attach':
        if len(choice.split()) > 1:
            return {'route': 'hint', 'text': 'Use /cli attach, or /cli attach <name or number>.'}
        return dict({'route': 'attach'}, **({'target': choice} if choice else {}))
    if verb == 'test':
        return {'route': 'test', 'command': choice} if choice else {'route': 'test'}
    if verb == 'brief':
        if choice.casefold() not in ('', 'clear'):
            return {'route': 'hint', 'text': 'Use /cli brief, /cli brief clear, or /cli brief-add <text>.'}
        return {'route': 'brief', 'action': 'clear' if choice else 'show'}
    if verb == 'brief-add':
        return ({'route': 'brief', 'action': 'add', 'text': choice} if choice else
                {'route': 'hint', 'text': 'Use /cli brief-add <text>: one point every agent reads before its task.'})
    if verb in ('diff', 'dir', 'undo'):
        if not choice:
            return {'route': verb}
        session, name = target_of(choice, state, every=True) if len(choice.split()) == 1 else (None, None)
        return {'route': verb, 'session': session, 'name': name} if session else name or no_agent(choice)
    if verb == 'use':
        session, name = target_of(choice, state) if len(choice.split()) == 1 else (None, None)
        if session:
            return {'route': 'use', 'session': session, 'name': name}
        return name or (no_agent(choice) if choice else {'route': 'hint', 'text': 'Use /cli use <name>.'})
    if verb == 'cancel':
        if not choice:
            return {'route': 'cancel'}
        session, name = target_of(choice, state)
        return {'route': 'cancel', 'session': session, 'name': name} if session else name or no_agent(choice)
    if verb in ('queue', 'resume') and not choice:
        return {'route': verb}
    if verb == 'progress':
        if not choice or choice.casefold() in PROGRESS_MODES:
            return {'route': 'progress', 'choice': choice.casefold()}
        return {'route': 'hint', 'text': 'Use /cli progress activity or /cli progress quiet.'}
    if verb == 'view':
        if not choice or choice.casefold() in ('on', 'off'):
            return {'route': 'view', 'choice': choice.casefold()}
        return {'route': 'hint', 'text': 'Use /cli view on or /cli view off.'}
    if verb == 'mode':
        return {'route': 'hint', 'text': MODE_REMOVED.format(host=host.name())}
    if verb in ('menu', 'model', 'effort', 'access'):
        session, name, text = named_tail(choice, state)
        target = {'session': session, 'name': name} if session else {}
        if verb == 'menu' and text:
            return no_agent(text.split()[0])
        if verb == 'menu' or (verb == 'model' and not text):
            return dict({'route': 'settings'}, **target)
        return dict({'route': 'tune', 'phase': verb, 'text': text}, **target)
    if verb not in names.RESERVED and not selected:  # An agent's name or tag is a known word too.
        # A word CLI-MODE doesn't know: say so, rather than suggest activating (there may be nothing to activate).
        return {'route': 'hint', 'text': 'CLI-MODE has no /cli ' + verb + '. ' + help_hint()}
    return {'route': 'hint', 'text': inactive_hint() if not state['active'] else help_hint()}


def direct_targets(payload, state):
    """The agents a /d payload starts by naming: ([(session, name)...], words used, problem hint or None).

    Targets are separated by commas: `gro-4k,elon` or `gro-4k, elon`. A word after a comma joins the list only
    if every part of it names an agent, so `/d elon, please fix it` goes to ELON with "please fix it".
    """
    words = payload.split()
    found, used, unknown = [], 0, []
    for index, word in enumerate(words):
        if index and not words[index - 1].endswith(','):
            break
        parts = [part for part in word.split(',') if part]
        resolved = [(part, target_of(part, state)) for part in parts]
        if index and (not parts or any(not session for _, (session, _) in resolved)):
            break  # Not a target: the prompt starts here.
        for part, (session, name) in resolved:
            if session:
                found.append((session, name))
            else:
                unknown.append((part, name))
        used = index + 1
    if unknown and found:  # Some targets named agents and some didn't: nothing goes to any of them.
        part, hint = unknown[0]
        return found, used, hint or {'route': 'hint', 'text': no_agent(part)['text'] + ' Nothing was sent.'}
    if unknown:
        part, hint = unknown[0]
        if hint:
            return [], 0, hint  # A short form or tag several agents share, or a tag with no agent.
        if names.looks_like_name(part):
            return [], 0, {'route': 'hint', 'text': no_agent(part)['text'] + ' Nothing was sent.'}
        return [], 0, None  # An ordinary first word: the whole payload is the task.
    unique = list(dict.fromkeys(found))
    return unique, used, None


def direct_route(payload, state):
    """A /d task: to the agents its first words name (commas between them), else to the current agent."""
    if not state['active']:
        return {'route': 'hint', 'text': inactive_hint()}
    if not payload.strip():
        return {'route': 'hint', 'text': 'Add a task after /d or $d. Nothing was sent.'}
    found, used, problem = direct_targets(payload, state)
    if problem:
        return problem
    if not found:
        return {'route': 'direct'}
    if len(payload.split()) <= used:
        return {'route': 'hint', 'text': 'Add a task after /d ' + ', '.join(name for _, name in found) +
                '. Nothing was sent.'}
    if len(found) == 1:
        return {'route': 'direct', 'session': found[0][0], 'name': found[0][1], 'named': used}
    return {'route': 'direct', 'targets': [dict(session=session, name=name) for session, name in found],
            'named': used}


def route(message, state):
    routing_mode(state)  # Fail closed on corrupt routing policy.
    parts = message.lstrip().split(None, 1)
    command_word = parts[0].casefold() if parts else ''
    rest = parts[1].strip() if len(parts) > 1 else ''
    if state.get('closeMenu') and not is_command(command_word, 'cli') and direct_payload(message) is None:
        reply = close_menu_reply(message, state)
        if reply:
            return reply
    if is_command(command_word, 'help') and not rest:
        return {'route': 'help', 'text': help_view.render()}
    if command_word == 'x' and not rest and (state.get('helpMenu') or state.get('turnRoute', {}).get('route') == 'help'):
        return {'route': 'help-dismiss'}
    if command_word == 'x' and not rest and ((state.get('pending') or {}).get('phase') == 'settings' or (state.get('pending') or {}).get('tuning')):
        return {'route': 'settings-dismiss'}
    if command_word == 'x' and not rest and (state.get('pending') or state.get('turnRoute', {}).get('route') == 'help'):
        return {'route': 'off'}
    if is_command(command_word, 'cli'):
        words = rest.split(None, 1)
        verb = words[0].casefold() if words else ''
        return cli_route(VERB_ALIASES.get(verb, verb), words[1].strip() if len(words) > 1 else '', state)
    if state.get('helpMenu'):
        # A complete Direct trigger remains an explicit task command. All other replies stay in help.
        if direct_payload(message) is None:
            return {'route': 'help-invalid'}
    if state.get('pending') and message.strip().casefold() in ('b', 'r', '>', '<') and (message.strip().casefold() != 'r' or state['pending'].get('phase') == 'model'):
        return {'route': 'navigate', 'action': message.strip().casefold()}
    if state.get('pending'):
        pending = state['pending']
        if pending.get('choices') and pending.get('phase') in ('agent', 'model', 'effort', 'access') and message.strip().isdigit():
            return {'route': 'choose', 'number': int(message.strip())}
        if pending.get('phase') == 'settings' and pending.get('stage') == 'menu':
            choice = message.strip().casefold()
            phase = {'1':'model', 'model':'model', '2':'effort', 'effort':'effort',
                     '3':'access', 'access':'access'}.get(choice)
            if phase:
                return {'route':'tune', 'phase':phase, 'text':''}
            if choice == 'done':  # X closes settings; "done" still does too.
                return {'route':'settings-dismiss'}
            if choice in ('4', 'progress'):
                return {'route': 'progress', 'choice': 'quiet' if progress_mode(state) == 'activity' else 'activity'}
            return {'route':'settings'}
        return {'route': 'setup'}
    # Only an explicit /d or $d reaches an agent; everything else is the host's.
    payload = direct_payload(message)
    if payload is not None:
        return direct_route(payload, state)
    return {'route': 'host'}
