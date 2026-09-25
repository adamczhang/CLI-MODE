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


REGISTRY = Path(__file__).resolve().parents[1] / 'codex/skills/cli-mode/references/backends.json'
DEFAULT_ROUTING_MODE = 'direct'
MAX_QUEUED_REQUESTS = 32
DEFAULT_AGENT_LIMIT = 4
MAX_AGENT_LIMIT = 8
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
            for word in [item['id']] + list(item.get('aliases') or []):
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

        The request goes to `session` (the current agent's unless the prompt named another); `named` means
        its first word after /d is that agent's name, which is removed before it is sent.
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
            record['named'] = True
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


def target_of(word, state, every=False):
    """(session, name) for a word naming a live agent, or a hint route explaining why it doesn't."""
    agents = live_agents(state, every)
    found = names.resolve(word, agents)
    if found and found[0] == 'match':
        name = next(alias for alias, session in agents.items() if session == found[1])
        return found[1], name
    if found:
        return None, {'route': 'hint', 'text': word + ' could mean ' + ' or '.join(found[1]) +
                      '; use the full name. Nothing was sent.'}
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
    return {'route': 'hint', 'text': inactive_hint() if not state['active'] else help_hint()}


def direct_route(payload, state):
    """A /d task: to the agent its first word names, else to the current agent."""
    if not state['active']:
        return {'route': 'hint', 'text': inactive_hint()}
    if not payload.strip():
        return {'route': 'hint', 'text': 'Add a task after /d or $d. Nothing was sent.'}
    words = payload.split(None, 1)
    session, name = target_of(words[0], state)
    if session:
        if len(words) < 2:
            return {'route': 'hint', 'text': 'Add a task after /d ' + name + '. Nothing was sent.'}
        return {'route': 'direct', 'session': session, 'name': name, 'named': True}
    if name:
        return name  # A short form several agents share: nothing is sent.
    if names.looks_like_name(words[0]):
        return {'route': 'hint', 'text': no_agent(words[0])['text'] + ' Nothing was sent.'}
    return {'route': 'direct'}


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
