"""Pinned ACPX runtime transport, installation discovery and public relay.

ACPX owns protocol handling and terminal results. Provider-specific catalogs,
settings, identity and readiness verification stay in each backend adapter.
"""
import io
import json
import os
from pathlib import Path
import shutil
import queue
import re
import subprocess
import threading
import time
import processes
from progress import ActivityRelay, DEFAULT_PROGRESS_MODE

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

ACPX_VERSION = '0.18.0'
MIN_NODE = (22, 13, 0)
BRIDGE = Path(__file__).with_name('acpx-runtime.mjs')
# package.json + package-lock.json that setup installs with `npm ci`.
RUNTIME_SPEC = Path(__file__).resolve().parents[1] / 'runtime' / 'acpx'
# Node's own warnings ("(node:1234) [DEP0190] DeprecationWarning: ..." and its "(Use `node --trace-...`" hint).
NODE_WARNING = re.compile(r'^\((node:\d+\)|Use `node --trace-)')


def without_node_warnings(text):
    """A child's stderr without Node's warning lines, which otherwise stand where the real reason should."""
    return '\n'.join(line for line in (text or '').strip().splitlines() if not NODE_WARNING.match(line.strip())).strip()


def owned_root():
    """The CLI-MODE-owned ACPX prefix; setup.ps1 computes the same default."""
    override = os.environ.get('CLI_MODE_ACPX_ROOT')
    if override:
        return Path(override)
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'CLI-MODE'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'cli-mode'
    return base / 'acpx' / ACPX_VERSION


def install_command():
    """Manual fallback when guided setup is unavailable."""
    return 'npm install --prefix "' + str(owned_root()) + '" acpx@' + ACPX_VERSION


def verify_package(package_dir):
    """True only for the exact tested package with both entry points."""
    try:
        manifest = json.loads((Path(package_dir) / 'package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(manifest, dict) and manifest.get('name') == 'acpx'
            and manifest.get('version') == ACPX_VERSION
            and all((Path(package_dir) / 'dist' / name).is_file() for name in ('cli.js', 'runtime.js')))


def node_version(node):
    try:
        result = subprocess.run([node, '--version'], stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=15, creationflags=_NO_WINDOW)
        return tuple(int(part) for part in result.stdout.strip().lstrip('v').split('.')[:3])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def candidates():
    """(source, package, node) in preference order: CLI-MODE's own copy, then global npm.

    Global installs are found by their package directory next to the npm
    launcher, never by the text of that launcher, so an npm upgrade that
    rewrites its shim template cannot change discovery.
    """
    node = shutil.which('node')
    yield 'owned', owned_root() / 'node_modules' / 'acpx', node
    quote = '"'
    for folder in os.environ.get('PATH', '').split(os.pathsep):
        folder = folder.strip().strip(quote)
        if not folder:
            continue
        folder = Path(folder)
        if os.name == 'nt':
            if any((folder / name).is_file() for name in ('acpx.cmd', 'acpx.ps1')):
                local = folder / 'node.exe'
                yield 'global', folder / 'node_modules' / 'acpx', str(local) if local.is_file() else node
        elif (folder / 'acpx').is_file():
            # npm links bin/acpx -> lib/node_modules/acpx/dist/cli.js.
            yield 'global', (folder / 'acpx').resolve().parent.parent, node


def locate():
    from installer import refresh_paths
    refresh_paths()
    seen = set()
    for source, package_dir, node in candidates():
        key = os.path.normcase(str(package_dir))
        if key in seen:
            continue
        seen.add(key)
        if node and verify_package(package_dir):
            return source, package_dir, node
    raise RuntimeError('ACPX ' + ACPX_VERSION + ' is not installed. Run /cli and choose I for guided setup, '
                       'or install it manually: ' + install_command())


def runtime_install(saved=None):
    """Resolve once per binding; every call verifies the same tested package."""
    if saved:
        node, package_dir, source = saved['node'], Path(saved['package']), saved.get('source')
    else:
        source, package_dir, node = locate()
        version = node_version(node)
        if version is None or version < MIN_NODE:
            raise RuntimeError('ACPX ' + ACPX_VERSION + ' needs Node.js ' + '.'.join(map(str, MIN_NODE)) +
                               ' or newer; ' + str(node) + ' does not qualify.')
    if not verify_package(package_dir) or not node or not Path(node).is_file():
        raise RuntimeError('This binding requires ACPX ' + ACPX_VERSION +
                           '; repair that installation before continuing.')
    install = dict(node=str(Path(node).resolve()), package=str(Path(package_dir).resolve()), version=ACPX_VERSION)
    if source:
        install['source'] = source
    return install


def executable():
    """The verified direct Node launcher for the resolved ACPX package."""
    install = runtime_install()
    return [install['node'], str(Path(install['package']) / 'dist' / 'cli.js')]


def public_event(event):
    """Legacy raw-ACP presentation helper for probes, not turn settlement.

    Tool metadata uses the shared bridge's stateful public-progress projector;
    this stateless helper cannot safely correlate partial tool notifications.
    Private reasoning and raw tool payloads are always excluded.
    """
    update = event.get('params', {}).get('update', {})
    kind = update.get('sessionUpdate')
    if kind in ('agent_thought_chunk', 'tool_call', 'tool_call_update'):
        return None
    if kind == 'plan':
        entries = update.get('entries')
        if not isinstance(entries, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('content'), str) or
                item.get('status') not in ('pending', 'in_progress', 'completed') for item in entries):
            return None
        return dict(type='plan', entries=[dict(content=item['content'], status=item['status']) for item in entries])
    if kind == 'agent_message_chunk':
        content = update.get('content', {})
        if content.get('type') == 'text':
            text = content.get('text')
            if not isinstance(text, str) or not text:
                return None
            message = dict(type='message', text=text)
            if isinstance(update.get('messageId'), str):
                message['messageId'] = update['messageId']
            return message
        if content.get('type') in ('image', 'audio', 'resource', 'resource_link'):
            return {'type': 'artifact', 'content': content}
    result = event.get('result', {})
    if isinstance(result, dict) and 'stopReason' in result:
        return {'type': 'done', 'stopReason': result['stopReason']}
    if isinstance(event.get('error'), dict):
        return {'type': 'error', 'message': event['error'].get('message', 'ACP error')}
    return None


class PublicRelay:
    """Coalesce public text without rewriting it; plans are complete snapshots."""
    MIN_CHUNK = 400

    def __init__(self, progress_mode=DEFAULT_PROGRESS_MODE):
        self.text = ''
        self.message_id = None
        self.started = None
        self.plan = None
        self.activity = ActivityRelay(progress_mode)

    def flush(self):
        if not self.text:
            return []
        value = dict(type='message', text=self.text)
        if self.message_id is not None:
            value['messageId'] = self.message_id
        self.text, self.started = '', None
        return [value]

    def due(self):
        ready = self.flush() if self.started is not None and time.monotonic() - self.started >= 1 else []
        updates = self.activity.due()
        return ready + (self.flush() if updates else []) + updates

    def feed(self, event):
        ready = self.due()
        if event['type'] in ('activity', 'usage'):
            updates = self.activity.feed(event)
            return ready + (self.flush() if updates else []) + updates
        if event['type'] in ('done', 'error'):
            ready += self.flush() + self.activity.flush()
        if event['type'] == 'message':
            identity = event.get('messageId')
            if identity != self.message_id:
                ready += self.flush()
            self.message_id = identity
            if self.started is None:
                self.started = time.monotonic()
            self.text += event['text']
            # Readable chunks, not one event per line: flush at a line end once
            # a paragraph's worth has built up, at 2000 characters, or (in
            # due()) after a second of streaming.
            if len(self.text) >= 2000 or (len(self.text) >= self.MIN_CHUNK and self.text.endswith('\n')):
                ready += self.flush()
            return ready
        if event['type'] == 'plan':
            if event['entries'] == self.plan:
                return ready
            self.plan = event['entries']
        return ready + self.flush() + [event]


class BridgeServer:
    """One long-lived Node bridge. It serves this process's runtime requests in turn,
    so the ACPX runtime and `config show` are loaded once rather than per turn.

    Its stderr is discarded (never parsed: it can hold private diagnostics), and
    the job object ties it to this process like any other submitter child.
    """
    def __init__(self, install, key):
        self.key, self.busy = key, False
        self.process = subprocess.Popen([install['node'], str(BRIDGE)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        encoding='utf-8', errors='replace', creationflags=_NO_WINDOW)
        processes.bind(self.process)
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def alive(self):
        return self.process.poll() is None

    def close(self):
        """Idle shutdown: EOF lets the bridge detach from owners and exit."""
        try:
            self.process.stdin.close()
        except OSError:
            pass


class BridgeTurn:
    """One request on a BridgeServer, shaped like the Popen the callers expect.

    stdout yields that request's lines and ends at its bridge_end marker;
    returncode is the marker's exitCode. kill() ends the whole server, which
    detaches from (never cancels) any turn the owner accepted.
    """
    acpx_runtime = True
    stdin = None

    def __init__(self, server):
        self.server, self.pid, self.returncode = server, server.process.pid, None
        self.stdout, self.stderr = self, io.StringIO('')

    def _accept(self, line):
        if line is None:
            try:
                code = self.server.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
            self.returncode = code or 1
            self.server.busy = False
            return ''
        if '"bridge_end"' in line:
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if isinstance(event, dict) and event.get('type') == 'bridge_end':
                self.returncode = int(event.get('exitCode', 1))
                self.server.busy = False
                return ''
        return line

    def _next(self, timeout=None):
        if self.returncode is not None:
            return ''
        return self._accept(self.server.lines.get(timeout=timeout))

    def readline(self):
        return self._next()

    def __iter__(self):
        while line := self._next():
            yield line

    def close(self):
        pass  # The request's stream ends at its marker; the server stays open.

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        until = None if timeout is None else time.monotonic() + timeout
        out = []
        while self.returncode is None:
            remaining = None if until is None else until - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired('acpx bridge', timeout)
            try:
                out.append(self._next(remaining))
            except queue.Empty:
                raise subprocess.TimeoutExpired('acpx bridge', timeout) from None
        return ''.join(out), ''

    def wait(self, timeout=None):
        self.communicate(timeout)
        return self.returncode

    def kill(self):
        self.server.busy = True  # Never hand a killed server to another request.
        try:
            self.server.process.kill()
        except OSError:
            pass
        if self.returncode is None:
            self.returncode = -9

    terminate = kill


_servers = []
_servers_lock = threading.Lock()


def bridge_idle(install):
    """True when a warm bridge for this install and environment is free now."""
    key = (install['node'], install['package'], hash(frozenset(os.environ.items())))
    with _servers_lock:
        return any(server.alive() and not server.busy and server.key == key for server in _servers)


def bridge_request(install, payload):
    """Send one request to an idle bridge for this install and environment."""
    key = (install['node'], install['package'], hash(frozenset(os.environ.items())))
    for attempt in range(2):
        with _servers_lock:
            live = [server for server in _servers if server.alive()]
            for server in live:
                # Keep at most one idle server, for the current environment.
                if not server.busy and server.key != key:
                    server.close()
            _servers[:] = [server for server in live if server.key == key or server.busy]
            server = next((item for item in _servers if not item.busy), None)
            if server is None:
                server = BridgeServer(install, key)
                _servers.append(server)
            server.busy = True
        try:
            server.process.stdin.write(json.dumps(payload) + '\n')
            server.process.stdin.flush()
            return BridgeTurn(server)
        except (OSError, ValueError):
            # A server that died while idle received nothing; use a fresh one.
            server.process.kill()
            if attempt:
                raise


class AcpxBackend:
    """Common owned-session transport for one ACPX agent profile."""
    profile = None
    owner_ttl = 1800
    # The bridge refuses a prompt whose saved provider conversation changed and
    # reports the identity after the turn, so ordinary turns need no separate
    # `sessions show` before or after.
    bridge_checks_identity = True
    # A newly ensured ACP session may be only a placeholder until its first
    # prompt. ACPX's CLI can recover that empty session; subsequent turns use
    # the strict shared owner after a real provider identity has been saved.
    bootstrap_with_cli_readiness = True

    def prepare(self, owned):
        if owned.get('transport') != 'native':
            owned['acpxRuntime'] = runtime_install(owned.get('acpxRuntime'))

    def validate_prompt(self, owned):
        """Provider adapters may reject unsupported saved access before dispatch."""

    def cli(self, owned):
        self.prepare(owned)
        install = owned['acpxRuntime']
        return [install['node'], str(Path(install['package']) / 'dist' / 'cli.js')]

    def command(self, owned, args, timeout=60):
        if '--file' in args:
            self.validate_prompt(owned)
        s = owned['settings']
        flags = ['--approve-all'] if s['access'] == 'allow' else [
            '--approve-reads', '--non-interactive-permissions', 'fail']
        return self.cli(owned) + ['--cwd', owned['workspace'], '--auth-policy', 'skip',
                '--ttl', str(self.owner_ttl), '--timeout', str(timeout), '--format', 'json'] + flags + [
                owned.get('acpxProfile', self.profile)] + args

    @staticmethod
    def spawn(command, stdin=subprocess.DEVNULL):
        process = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding='utf-8', errors='replace', creationflags=_NO_WINDOW)
        processes.bind(process)
        return process

    def start(self, owned, args, timeout=60):
        if '--file' in args:
            self.validate_prompt(owned)
            if owned.get('bootstrapPrompt'):
                return self.spawn(self.command(owned, args, timeout))
        control = args[2:] if args[:1] == ['-s'] else args
        shared_control = bool(owned.get('providerSession')) and control[:1] in (['set'], ['set-mode'])
        if '--file' in args or shared_control:
            payload = self.bridge_payload(owned, timeout)
            if shared_control:
                payload.update(action='control', control=control)
            else:
                payload.update(action='prompt', requestId=owned['requestId'], promptFile=args[args.index('--file') + 1])
            return bridge_request(owned['acpxRuntime'], payload)
        if control[:1] == ['cancel'] and self.bridge_ready(owned) and bridge_idle(owned['acpxRuntime']):
            # Session-wide cancel through a warm, idle bridge instead of a new ACPX
            # process. A busy bridge would mean starting another, which is slower.
            return bridge_request(owned['acpxRuntime'], dict(self.bridge_payload(owned, timeout), action='cancel'))
        return self.spawn(self.command(owned, args, timeout))

    def bridge_ready(self, owned):
        """A binding the bridge can serve: its runtime, workspace and access are known."""
        return bool(owned.get('acpxRuntime') and owned.get('workspace') and (owned.get('settings') or {}).get('access'))

    def bridge_payload(self, owned, timeout):
        self.prepare(owned)
        return dict(install=owned['acpxRuntime'], workspace=owned['workspace'], session=owned['name'],
                    profile=owned.get('acpxProfile', self.profile), access=owned['settings']['access'], timeout=timeout,
                    cursor=owned.get('watchCursor'), recordId=owned.get('acpxRecordId'),
                    cancelFile=owned.get('cancelFile'), ttl=self.owner_ttl,
                    progressMode=owned.get('progressMode', DEFAULT_PROGRESS_MODE),
                    expectedSession=owned.get('providerSession'),
                    configCache=owned.get('configCache'))

    def collect(self, process, timeout=75):
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # A descendant may still hold pipes; do not wait indefinitely.
            raise RuntimeError('ACPX control timed out. Inspect owned status before retrying.')
        if process.returncode:
            # Controls contain no user prompt or thought stream. Node's own warnings would hide the reason.
            raise RuntimeError((without_node_warnings(err) or out.strip() or 'ACPX failed')[-3000:])
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return [json.loads(line) for line in out.splitlines() if line.strip().startswith('{')]

    def control(self, owned, args):
        return self.collect(self.start(owned, args))

    def metadata(self, owned):
        return self.control(owned, ['sessions', 'show', owned['name']])

    def close(self, owned):
        if self.bridge_ready(owned):
            # One warm bridge request replaces three ACPX processes (cancel,
            # sessions close, status). Any failure falls back to the CLI path,
            # whose status check decides the outcome.
            try:
                result = self.collect(bridge_request(owned['acpxRuntime'],
                                                     dict(self.bridge_payload(owned, 60), action='close')))
                if isinstance(result, dict) and result.get('status') == 'no-session':
                    return
            except (OSError, RuntimeError, ValueError):
                pass
        # Close cancels the queue itself; cancel is best effort for an idle/dead owner.
        try:
            self.control(owned, ['cancel', '-s', owned['name']])
        except RuntimeError:
            pass
        try:
            self.control(owned, ['sessions', 'close', owned['name']])
        except RuntimeError:
            # Creation may have failed before ACPX registered the owned name.
            # Confirm absence through status, never by matching an error string.
            result = self.control(owned, ['status', '-s', owned['name']])
            if result.get('status') == 'no-session':
                return
            raise
        result = self.control(owned, ['status', '-s', owned['name']])
        if result.get('status') != 'no-session':
            raise RuntimeError('Owned agent shutdown not confirmed: ' + str(result.get('status')))
