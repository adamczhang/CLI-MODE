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
from presentation import menu_block
from progress import DEFAULT_PROGRESS_MODE, PROGRESS_MODES, progress_mode


REGISTRY = Path(__file__).resolve().parents[1] / 'codex/skills/cli-mode/references/backends.json'
DEFAULT_ROUTING_MODE = 'direct'
MAX_QUEUED_REQUESTS = 32
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
        value.setdefault('progressMode', DEFAULT_PROGRESS_MODE)
        value.setdefault('helpMenu', None)
        if value['helpMenu'] in ('index', 'start', 'routing', 'settings', 'activity', 'setup', 'defaults'):
            value['helpMenu'] = 'commands'  # Preserve an open help view across upgrades.
        if value['helpMenu'] not in (None, 'commands'):
            raise ValueError('Unsupported CLI-MODE help page; inspect state before dispatch.')
        progress_mode(value)
        routing_mode(value)  # Fail closed on corrupt routing policy.
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

    def capture(self, state, request_id, text):
        """Persist exact hook input separately from routing metadata, under lock."""
        if sum(record['status'] == 'captured' for record in state.get('requests', {}).values()) >= MAX_QUEUED_REQUESTS:
            raise RuntimeError('CLI-MODE queue is full (32 messages); wait for earlier work to finish.')
        path = self.request_path(request_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        state.setdefault('requests', {})[request_id] = dict(
            status='captured', capturedAt=time.time(), generation=state['generation'],
            routingMode=routing_mode(state), session=state['main'])

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
ROUTING_MODES = ('passthrough', 'direct')


def inactive_hint():
    # Claude Code's own /help is built in; CLI-MODE's help is /cli help there.
    return '/cli to activate.  Say /cli help to see options' if host.claude() else INACTIVE_HINT


def help_hint():
    return 'Say /cli help to see options.' if host.claude() else 'Say /help to see options.'


def routing_mode(state):
    mode = state.get('routingMode', DEFAULT_ROUTING_MODE)
    if mode not in ROUTING_MODES:
        raise ValueError('Unsupported CLI-MODE routing mode; inspect state before dispatch.')
    return mode


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


def route(message, state):
    mode = routing_mode(state)
    parts = message.lstrip().split(None, 1)
    command_word = parts[0].casefold() if parts else ''
    rest = parts[1].strip() if len(parts) > 1 else ''
    if is_command(command_word, 'help') and not rest:
        return {'route': 'help', 'text': help_view.render()}
    if command_word == 'x' and not rest and (state.get('helpMenu') or state.get('turnRoute', {}).get('route') == 'help'):
        return {'route': 'help-dismiss'}
    if command_word == 'x' and not rest and state.get('modeMenu'):
        return {'route': 'mode-dismiss'}
    if command_word == 'x' and not rest and ((state.get('pending') or {}).get('phase') == 'settings' or (state.get('pending') or {}).get('tuning')):
        return {'route': 'settings-dismiss'}
    if command_word == 'x' and not rest and (state.get('pending') or state.get('turnRoute', {}).get('route') == 'help'):
        return {'route': 'off'}
    if is_command(command_word, 'cli'):
        words = rest.split(None, 1)
        verb = words[0].casefold() if words else ''
        choice = words[1].strip() if len(words) > 1 else ''
        if not verb:
            return {'route': 'home'}
        if verb == 'help' and not choice and host.claude():
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
        if verb == 'bind' and resolve_backend(choice):
            return {'route': 'bind', 'agent': resolve_backend(choice)}
        if verb in ('stop', 'off') and not choice:
            return {'route': 'off'}
        if verb == 'cancel' and not choice:
            return {'route': 'cancel'} if state['active'] else {'route': 'hint', 'text': inactive_hint()}
        if verb in ('queue', 'resume') and not choice:
            return {'route': verb} if state['active'] else {'route': 'hint', 'text': inactive_hint()}
        if verb == 'progress':
            if not choice or choice.casefold() in PROGRESS_MODES:
                return {'route': 'progress', 'choice': choice.casefold()}
            return {'route': 'hint', 'text': 'Use /cli progress activity or /cli progress quiet.'}
        if verb == 'view':
            if not choice or choice.casefold() in ('on', 'off'):
                return {'route': 'view', 'choice': choice.casefold()}
            return {'route': 'hint', 'text': 'Use /cli view on or /cli view off.'}
        if verb in ('mode', 'menu', 'model') and not state['active']:
            return {'route': 'hint', 'text': 'CLI-MODE: Agent not activated. /CLI to setup'}
        if verb in ('mode', 'menu', 'model') and not choice and state['active']:
            return {'route': 'settings'}
        if verb == 'mode':
            if not choice or choice.casefold() in ROUTING_MODES:
                return {'route': 'mode', 'choice': choice.casefold()}
            return {'route': 'hint', 'text': 'Use /cli mode passthrough or /cli mode direct.'}
        if verb in ('model', 'effort', 'access', 'permissions') and state['active']:
            return {'route': 'tune', 'phase': 'access' if verb == 'permissions' else verb, 'text': choice}
        return {'route': 'hint', 'text': inactive_hint() if not state['active'] else help_hint()}
    if state.get('helpMenu'):
        # A complete Direct trigger remains an explicit task command. All other
        # unprefixed replies stay in help instead of reaching Passthrough.
        if not (mode == 'direct' and direct_payload(message) is not None):
            return {'route': 'help-invalid'}
    if state.get('modeMenu'):
        choice = {'1': 'passthrough', '2': 'direct'}.get(message.strip().casefold(), message.strip().casefold())
        return {'route': 'mode', 'choice': choice if choice in ROUTING_MODES else ''}
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
            if choice in ('4', 'mode', 'routing', 'passthrough'):
                return {'route':'mode', 'choice':''}
            if choice == 'done':  # X closes settings; "done" still does too.
                return {'route':'settings-dismiss'}
            if choice in ('5', 'progress'):
                return {'route': 'progress', 'choice': 'quiet' if progress_mode(state) == 'activity' else 'activity'}
            return {'route':'settings'}
        if (pending.get('phase') == 'activation' and pending.get('stage') == 'menu'
                and not pending.get('onboarding') and message.strip().casefold() in ('3', 'change routing mode')):
            return {'route': 'mode', 'choice': ''}
        return {'route': 'setup'}
    if mode == 'direct':
        payload = direct_payload(message)
        if payload is not None:
            if not state['active']:
                return {'route': 'hint', 'text': inactive_hint()}
            if not payload.strip():
                return {'route': 'hint', 'text': 'Add a task after /d or $d. Nothing was sent.'}
            return {'route': 'direct'}
        return {'route': 'host'}
    return {'route': 'delegate' if state['active'] else 'host'}
