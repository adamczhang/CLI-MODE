"""Detached queue worker, receipts and turn cancellation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from operations import _detached_workers, emit, follow_path, operation_running, pending_work, status_age
import adapters
import host
import menu_view
import relay_view

# The worker runs the controller CLI entry point, not this module.
CONTROLLER = Path(__file__).resolve().with_name('controller.py')


class QueueMixin:
    def resume_monitoring(self):
        """Reattach to receipts after a host turn ends, without replaying prompts."""
        try:
            worker = self.ensure_pump()
        except OSError as exc:
            worker = {'worker': 'start-failed', 'error': str(exc)}
        queue = self.queue_status()
        requests = queue['requests']
        pending = [item for item in requests if item['status'] in ('submitting', 'captured', 'uncertain')]
        # Follow the in-flight turn first, then queued turns in capture order.
        pending.sort(key=lambda item: {'submitting': 0, 'uncertain': 1, 'captured': 2}[item['status']])
        latest = requests[-1] if requests else None
        return dict(worker=worker, workerState=queue['workerState'],
                    requestIds=[item['requestId'] for item in pending],
                    statuses={item['requestId']: item['status'] for item in pending},
                    latestRequestId=latest['requestId'] if latest else None,
                    latestStatus=latest['status'] if latest else None,
                    blocked=worker.get('worker') in ('blocked', 'start-failed'))

    def ensure_pump(self):
        """Start one conversation-owned submitter independent of the host turn."""
        if os.environ.get('CLI_MODE_TEST_DISABLE_AUTORUN') == '1':
            return {'worker': 'disabled-for-tests'}
        with self.store.edit() as state:
            captured = [key for key, value in state.get('requests', {}).items()
                        if value['status'] == 'captured']
            if not state['active']:
                return {'worker': 'idle', 'queued': len(captured)}
            runner = state.get('runner') or {}
            if runner and operation_running(runner):
                return {'worker': 'running', 'pid': runner['pid'], 'queued': len(captured)}
            if runner:
                # A vanished owner may have reached the provider. Never replay its
                # admitted prompt merely because the Python process disappeared.
                for record in state.get('requests', {}).values():
                    if record['status'] == 'submitting' and record.get('submitterPid') == runner.get('pid'):
                        record['status'] = 'uncertain'
                        operation = state['inflight'].get(record.get('operation'))
                        if operation:
                            operation['running'] = False
                state.pop('runner', None)
            for record in state.get('requests', {}).values():
                if record['status'] == 'submitting':
                    operation = state['inflight'].get(record.get('operation'))
                    if operation and not operation_running(operation):
                        record['status'] = 'uncertain'
                        operation['running'] = False
            if any(value['status'] in ('submitting', 'uncertain')
                   for value in state.get('requests', {}).values()) or state['inflight']:
                return {'worker': 'blocked', 'queued': len(captured),
                        'message': 'Inspect and reconcile the earlier operation before resuming the queue.'}
            if not captured:
                return {'worker': 'idle', 'queued': 0}
            token = uuid.uuid4().hex
            command = [sys.executable, str(CONTROLLER), '--thread', self.store.thread,
                       '--workspace', self.store.workspace, '--data-root', str(self.store.root),
                       'pump', '--token', token]
            options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
                       'stderr': subprocess.DEVNULL, 'close_fds': True}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            else:
                options['start_new_session'] = True
            _detached_workers[:] = [child for child in _detached_workers if child.poll() is None]
            process = subprocess.Popen(command, **options)
            _detached_workers.append(process)
            state['runner'] = dict(pid=process.pid, token=token, started=time.time())
            state.pop('runnerError', None)
            return {'worker': 'started', 'pid': process.pid, 'queued': len(captured)}

    # After draining, stay ready this long for the next message: it then skips a
    # new worker process and reuses this process's warm ACPX bridge.
    PUMP_LINGER = float(os.environ.get('CLI_MODE_PUMP_LINGER', 300))

    def pump(self, token):
        """Drain captured requests in order; stop at any uncertain outcome."""
        idle_since = None
        seen = None
        while True:
            if idle_since is not None:
                # Lingering: a stat per poll; parse state only after it changes
                # (every write replaces the file, so its stamp moves).
                try:
                    info = self.store.path.stat()
                    stamp = (info.st_mtime_ns, info.st_size)
                except OSError:
                    stamp = None
                if stamp == seen and time.monotonic() - idle_since < self.PUMP_LINGER:
                    time.sleep(.25)
                    continue
                seen = stamp
                state = self.store.read()
                runner = state.get('runner') or {}
                lingering = (runner.get('token') == token and state['active'] and
                             not any(record['status'] == 'captured' for record in state.get('requests', {}).values()))
                if lingering and time.monotonic() - idle_since < self.PUMP_LINGER:
                    time.sleep(.25)
                    continue
            with self.store.edit() as state:
                runner = state.get('runner') or {}
                if runner.get('token') != token or runner.get('pid') != os.getpid():
                    return {'worker': 'replaced'}
                if not state['active']:
                    state.pop('runner', None)
                    return {'worker': 'stopped'}
                if state['inflight'] or any(record['status'] in ('submitting', 'uncertain')
                    for record in state.get('requests', {}).values()):
                    # An unrelated live submitter can finish; an uncertain one
                    # needs deliberate reconciliation before further dispatch.
                    if any(not operation_running(op) for op in state['inflight'].values()):
                        state.pop('runner', None)
                        return {'worker': 'blocked'}
                    wait = True
                    request_id = None
                else:
                    wait = False
                    request_id = next((key for key, record in state.get('requests', {}).items()
                                       if record['status'] == 'captured'), None)
                    if request_id is None:
                        if idle_since is None and self.PUMP_LINGER > 0:
                            idle_since = time.monotonic()
                            continue
                        state.pop('runner', None)
                        return {'worker': 'drained'}
                    idle_since = None
            if wait:
                time.sleep(.2)
                continue
            try:
                self.send_request(request_id, output=lambda event: None)
                with self.store.edit() as state:
                    state.pop('runnerError', None)
            except Exception as exc:
                with self.store.edit() as state:
                    error = dict(requestId=request_id, kind=type(exc).__name__, time=time.time())
                    if isinstance(exc, OSError):
                        error.update(errno=exc.errno, path=str(exc.filename) if exc.filename else None)
                    state['runnerError'] = error
                    if state['requests'][request_id]['status'] == 'uncertain':
                        state.pop('runner', None)
                        return {'worker': 'blocked'}

    def observe(self, request_id, cursor=0, limit=100, ends=False):
        """Read a receipt and bounded public events without holding a provider turn.

        With `ends`, also return each event's end offset, so a caller can stop
        partway through the events and resume from there.
        """
        self.store.request_path(request_id)  # Validate before looking up any path.
        if not isinstance(cursor, int) or cursor < 0 or limit < 1 or limit > 500:
            raise ValueError('Invalid event cursor or limit.')
        state = self.store.read()
        record = state.get('requests', {}).get(request_id)
        if record is None:
            raise RuntimeError('Captured request was not found in this conversation.')
        receipt = deepcopy(record)
        path = Path(receipt['events']) if receipt.get('events') else None
        events, offsets = [], []
        last_public = None
        if path and path.is_file():
            # The saved path is generated by this controller, but validate its
            # scope before reading in case state was manually altered.
            expected = self.store.request_path(request_id).parent / 'operations'
            if path.resolve().parent != expected.resolve() or path.suffix != '.jsonl':
                raise ValueError('Invalid public event path in saved receipt.')
            with path.open('rb') as source:
                source.seek(cursor)
                next_cursor = cursor
                budget = 1024 * 1024
                for _ in range(limit):
                    line = source.readline(min(budget + 1, 256 * 1024 + 1))
                    if not line or not line.endswith(b'\n'):
                        break
                    if len(line) > 256 * 1024:
                        raise ValueError('Public event exceeds the supported size.')
                    events.append(json.loads(line))
                    next_cursor = source.tell()
                    offsets.append(next_cursor)
                    budget -= len(line)
                    if budget <= 0:
                        break
                cursor = next_cursor
            try:
                last_public = path.stat().st_mtime
            except OSError:
                pass  # A concurrent cleanup does not change the saved receipt.
        status = receipt['status']
        active = status in ('captured', 'submitting', 'uncertain')
        started = receipt.get('capturedAt') if status == 'captured' else receipt.get('submittedAt')
        runner = state.get('runner')
        now = time.time()
        result = {'requestId': request_id, 'receipt': receipt, 'events': events, 'cursor': cursor,
                  'worker': runner,
                  'workerState': ('running' if operation_running(runner) else 'stale') if runner else 'idle',
                  'statusAgeSeconds': status_age(started, now) if active else None,
                  'withoutPublicUpdateSeconds': status_age(last_public or started, now)
                      if status in ('submitting', 'uncertain') else None}
        if ends:
            result['ends'] = offsets
        return result

    # Relay batching, as in OpenClaw's block streaming: hold small fragments
    # until a readable chunk, a quiet pause, or a bounded wait.
    RELAY_MIN_CHARS = 800
    RELAY_IDLE = 1.5
    RELAY_MAX_HOLD = 6.0
    RELAY_VIEWS_KEPT = 300
    # Text hosts (Claude Code) read each result through a shell tool, which turns
    # output past about 30,000 characters into a file. Stay under it.
    RELAY_TEXT_MAX = 24000
    RELAY_PROGRESS_KEPT = 20
    RELAY_PUBLIC = ('message', 'plan', 'artifact', 'error', 'activity', 'usage', 'context_warning')

    def relay(self, request_id, cursor=0, wait=8.0, view_dir=None, poll=.25):
        """Block until a request has something worth showing, then render one view.

        The host calls this in a loop (passing back `cursor`) until `done`, and
        displays each returned view as is. It never re-types or rewrites the
        agent's words, and replaces observe + format-message + format-progress.
        """
        started = time.monotonic()
        batch, position = [], cursor
        first_new = last_new = None
        while True:
            view = self.observe(request_id, position, limit=500)
            now = time.monotonic()
            if view['events']:
                batch += view['events']
                position = view['cursor']
                first_new = first_new or now
                last_new = now
            status = view['receipt']['status']
            done = status not in ('captured', 'submitting')
            if done and view['events']:
                continue  # The log is final once settled; drain it before returning.
            chars = sum(len(event.get('text', '')) for event in batch if event.get('type') == 'message')
            if (done or (cursor == 0 and status != 'captured' and not view['receipt'].get('passingAnnounced'))
                    or (batch and (chars >= self.RELAY_MIN_CHARS or now - last_new >= self.RELAY_IDLE
                                   or now - first_new >= self.RELAY_MAX_HOLD))
                    or now - started >= wait):
                break
            time.sleep(poll)
        # Relay only public events, as the log records them; never the ACPX history.
        public = [event for event in batch if event.get('type') in ('message', 'plan', 'artifact', 'error',
                                                                    'activity', 'usage', 'context_warning')]
        for event in public:
            if event['type'] == 'context_warning':
                event.update(type='error', message=event.get('message', ''))
        receipt = view['receipt']
        footer = None
        if status == 'captured':
            footer = 'Queued behind an earlier turn.'
        elif status == 'uncertain':
            footer = 'Completion could not be confirmed. Check /cli queue before sending the task again.'
        elif status in ('canceled', 'superseded'):
            footer = 'Turn canceled.'
        elif status == 'rejected':
            footer = self._not_sent(request_id)
        adapter = adapters.module(self.agent_of(self.store.read()))
        passing = None
        if cursor == 0 and status != 'captured':
            # Empty polls keep cursor zero; claim the introduction independently,
            # under the state lock, including across separate controller processes.
            with self.store.edit() as state:
                record = state.get('requests', {}).get(request_id)
                if record is not None and not record.get('passingAnnounced'):
                    record['passingAnnounced'] = True
                    passing = adapter.PASSING
        show_work = (self.store.read().get('progressMode') or 'activity') != 'quiet'
        history = self._public_history(receipt, position)
        artifacts = [event['content'] for event in public if event['type'] == 'artifact']
        # Mid-turn: a Markdown update the host posts as is. Views only render in
        # a final response, so the turn's one view comes when it is done.
        update = relay_view.markdown(adapter.LABEL, public, history, passing=passing,
                                     footer=None if done else footer, show_work=show_work,
                                     workspace=self.store.workspace)
        result = dict(requestId=request_id, cursor=position, done=done, status=status,
                      idleSeconds=view.get('withoutPublicUpdateSeconds'), markdown=update,
                      messageView=None, reference=None, text=update, artifacts=artifacts)
        if done:
            folder = Path(view_dir) if view_dir else self.store.root / 'views' / self.store.key
            path, text, _ = relay_view.render(
                adapter.LABEL, [event for event in history if event.get('type') != 'context_warning'],
                folder / (request_id + '-final-' + uuid.uuid4().hex[:6] + '.html'),
                footer=footer, show_work=show_work, workspace=self.store.workspace)
            self._prune_views(folder)
            result.update(text=text, reference=menu_view.reference(path), messageView=dict(
                path=path, format='inline-html', label=adapter.LABEL, kind='relay'))
        return result

    def relay_text(self, request_id, cursor=0, wait=25.0, poll=.25):
        """The relay for a host without inline views (Claude Code), which prints it as plain text.

        Claude Code's desktop app folds the text a turn posts between tool calls
        out of view; only text before the first call and after the last one stays
        open. So nothing is posted mid-turn: each call waits until the request
        settles or `wait` runs out, and the final `text` carries the whole turn
        under "X says..." (the "Passing to" line is Claude's own first words,
        before any call). Past RELAY_TEXT_MAX the earliest words come first as
        `markdown`, and the cursor continues from where they stopped. Nothing is
        written or saved here.
        """
        started = time.monotonic()
        while True:
            view = self.observe(request_id, cursor, limit=1)
            status = view['receipt']['status']
            if status not in ('captured', 'submitting') or time.monotonic() - started >= wait:
                break
            time.sleep(poll)
        adapter = adapters.module(self.agent_of(self.store.read()))
        result = dict(requestId=request_id, cursor=cursor, done=False, status=status, agent=adapter.LABEL,
                      idleSeconds=view.get('withoutPublicUpdateSeconds'), markdown='', text='', artifacts=[])
        if status in ('captured', 'submitting'):
            return result
        batch, ends, position = [], [], cursor
        while True:  # A settled request's log is final: read what is left of it.
            view = self.observe(request_id, position, limit=500, ends=True)
            if not view['events']:
                break
            batch, ends, position = batch + view['events'], ends + view['ends'], view['cursor']
        kept = size = 0
        for index, event in enumerate(batch):
            size += len(event.get('text') or '') + len(event.get('message') or '')
            if size > self.RELAY_TEXT_MAX and kept:
                break
            kept = index + 1
        trimmed = kept < len(batch)
        if trimmed:  # The rest follows on the next call, from this event's end.
            batch, position = batch[:kept], ends[kept - 1]
        # Relay only public events, as the log records them; never the ACPX history.
        public = [event for event in batch if event.get('type') in self.RELAY_PUBLIC]
        for event in public:
            if event['type'] == 'context_warning':
                event.update(type='error', message=event.get('message', ''))
        color = host.chat_color(self.store.root)  # Dark green attribution in Claude Code's chat.
        artifacts = [event['content'] for event in public if event['type'] == 'artifact']
        if trimmed:
            update = relay_view.markdown(adapter.LABEL, public, [], show_work=False, color=color)
            return dict(result, cursor=position, markdown=update, text=update, artifacts=artifacts)
        footer = None
        if status == 'uncertain':
            footer = 'Completion could not be confirmed. Check /cli queue before sending the task again.'
        elif status in ('canceled', 'superseded'):
            footer = 'Turn canceled.'
        elif status == 'rejected':
            footer = self._not_sent(request_id)
        show_work = (self.store.read().get('progressMode') or 'activity') != 'quiet'
        history = [event for event in self._public_history(view['receipt'], position)
                   if event.get('type') != 'context_warning']
        return dict(result, cursor=position, done=True, artifacts=artifacts, text=relay_view.final_markdown(
            adapter.LABEL, public, history, footer=footer, show_work=show_work, color=color))

    def relay_chain(self, request_ids, cursor=0, wait=25.0, poll=.25):
        """Relay a turn's requests with one command, for a text host (Claude Code).

        A turn can carry earlier requests whose relay was cut off, then its own.
        They share one command because the host reliably posts only the final
        `text` of its last command (P7b: given one relay each, Claude posted the
        second relay's answer and dropped the first one's). `cursor` is a
        position in the requests' public logs laid end to end; a settled
        request's log never changes, so positions stay put. The final `text`
        carries each request's words in order, under their own headings. Past
        RELAY_TEXT_MAX the earliest go out first, as one long answer's would.
        Progress is saved for the Stop guard and compaction.
        """
        started = time.monotonic()
        state = self.store.read()
        records, progress = state.get('requests') or {}, state.get('relayProgress') or {}
        if any(request_id not in records for request_id in request_ids):
            raise RuntimeError('Captured request was not found in this conversation.')
        if all((progress.get(request_id) or {}).get('done') for request_id in request_ids):
            # Background turns overlap: a later relay can post these before this command runs. Never twice.
            return dict(requestId=request_ids[-1], cursor=cursor, done=True, posted=True,
                        status=records[request_ids[-1]]['status'], agent=adapters.module(self.agent_of(state)).LABEL,
                        idleSeconds=None, markdown='', text='', artifacts=[])
        base, held = 0, []  # Where the current request's log starts in the stream; settled requests' words.
        for index, request_id in enumerate(request_ids):
            record = records[request_id]
            local, last = cursor - base, index == len(request_ids) - 1
            if not last and record['status'] not in ('captured', 'submitting'):
                end = self._log_end(record)
                saved = progress.get(request_id) or {}
                if saved.get('done'):
                    cursor = max(cursor, base + end)  # The stream moves past it, even from its start.
                    base += end
                    continue  # Already shown, possibly by a relay that ran after this command was given.
                if local < end:
                    start, cursor = local, base + end  # The stream stood in it: the rest waits for the end.
                else:
                    start = saved.get('cursor', 0)
                held.append((request_id, start))
                base += end
                continue
            result = self.relay_text(request_id, local, max(wait - (time.monotonic() - started), 0), poll=poll)
            if not last and result['status'] not in ('captured', 'submitting'):
                # It settled while this call waited: its words join the others at the end.
                held.append((request_id, local))
                base = cursor = base + self._log_end(self.store.read()['requests'][request_id])
                continue
            break

        def chain(position):  # Saved with the last request, when there is more than one.
            return dict(requests=list(request_ids), cursor=position) if len(request_ids) > 1 else None

        if not last or result['status'] in ('captured', 'submitting'):
            self._record_relay([(held_id, start, False) for held_id, start in held] +
                               [(request_id, result['cursor'], False)], chain(base + result['cursor']))
            return dict(result, cursor=base + result['cursor'])
        # Every request has settled: their words go out in order, in as few results as fit.
        finals = [(each, start, self.relay_text(each, start, 0, poll=poll))
                  for each, start in held + [(request_id, local)]]
        pieces, shown, size = [], [], 0
        for each, start, final in finals:
            piece = final['text'] if final['done'] else final['markdown']
            if pieces and size + len(piece) > self.RELAY_TEXT_MAX:
                break
            pieces.append(piece)
            shown.append((each, final['cursor'], final['done']))
            size += len(piece) + 2
            if not final['done']:
                break  # Its rest follows on the next call.
        done = len(shown) == len(finals) and shown[-1][2]
        # The last request's place in the stream; earlier ones resume from their own saved cursors.
        current = shown[-1][1] if len(shown) == len(finals) else local
        self._record_relay(shown + [(each, start, False) for each, start, _ in finals[len(shown):]],
                           chain(base + current))
        text = '\n\n'.join(pieces)
        return dict(finals[-1][2], cursor=base + current, done=done, markdown='' if done else text, text=text,
                    artifacts=[item for _, _, final in finals[:len(shown)] for item in final['artifacts']])

    # Following a turn from a background task (Claude Code): one short line per step, no agent text.
    FOLLOW_POLL = 1.0
    FOLLOW_LINE = 160
    FOLLOW_ENDS = dict(completed='{} finished.', canceled='{}\'s turn was canceled.',
                       superseded='{}\'s turn was canceled.', rejected='Not sent to {}.',
                       uncertain='{}\'s turn could not be confirmed.')

    def follow(self, request_id, write, poll=None):
        """Wait until a request settles, writing one line per step of the agent's work.

        Claude Code runs this as a background task: the lines fill the task's row in the
        background-tasks pane, and the task ending wakes Claude, which then runs `relay`
        once for the agent's words. So nothing here posts or saves relay progress; the
        process ID file only lets the hook avoid starting a second follow.
        """
        label = adapters.module(self.agent_of(self.store.read())).LABEL
        path = follow_path(self.store, request_id)
        mine = str(os.getpid())
        path.write_text(mine, encoding='ascii')
        tools, failed, plan, cursor, queued, writing = set(), set(), None, 0, False, False

        def say(text):
            text = ' '.join(relay_view.clean(text).split())
            write(text if len(text) <= self.FOLLOW_LINE else text[:self.FOLLOW_LINE - 1] + '…')
        try:
            while True:
                view = self.observe(request_id, cursor, limit=500)
                cursor, status = view['cursor'], view['receipt']['status']
                for event in view['events']:
                    kind = event.get('type')
                    if kind == 'activity' and event.get('toolCallId'):
                        writing = False
                        row = relay_view.tool_row(dict(event, kind=event.get('kind') or 'other'),
                                                  self.store.workspace)
                        if event['toolCallId'] not in tools:
                            tools.add(event['toolCallId'])
                            say(label + ': ' + row)
                        if event.get('status') == 'failed' and event['toolCallId'] not in failed:
                            failed.add(event['toolCallId'])
                            say(label + ': failed: ' + row)
                    elif kind == 'plan' and isinstance(event.get('entries'), list):
                        entries = event['entries']
                        done = sum(isinstance(entry, dict) and entry.get('status') == 'completed' for entry in entries)
                        if (done, len(entries)) != plan:
                            plan = (done, len(entries))
                            say(label + ': plan ' + str(done) + ' of ' + str(len(entries)) + ' steps done')
                    elif kind == 'message' and not writing:
                        writing = True
                        say(label + ' is writing.')
                    elif kind == 'error':
                        say(label + ': error: ' + str(event.get('message') or ''))
                if status == 'captured' and not queued:
                    queued = True
                    say(label + ' is finishing an earlier turn; this one is queued.')
                if status not in ('captured', 'submitting') and not view['events']:
                    break  # Settled, and its log is read to the end.
                if not view['events']:
                    time.sleep(self.FOLLOW_POLL if poll is None else poll)
        finally:
            try:
                if path.read_text(encoding='ascii') == mine:
                    path.unlink()
            except OSError:
                pass  # Another follow took the file over, or it is already gone.
        say(self.FOLLOW_ENDS.get(status, '{} stopped (' + status + ').').format(label))
        return dict(requestId=request_id, status=status, done=True)

    def _not_sent(self, request_id):
        """The footer of a request refused before it reached the agent, with the reason."""
        record = (self.store.read().get('requests') or {}).get(request_id) or {}
        return ' '.join(filter(None, ('Not sent.', record.get('rejectedReason'))))

    @staticmethod
    def _log_end(record):
        """Where a settled request's public log ends; it never grows again."""
        try:
            return Path(record['events']).stat().st_size if record.get('events') else 0
        except OSError:
            return 0

    def _record_relay(self, entries, chain=None):
        """Where the host's relay loop stands, so an early stop can be resumed.

        `entries` are (request, cursor, done) in stream order. A chain
        (relay_chain's requests and stream cursor) is kept with its last
        request until that request is done, for the Stop guard and compaction.
        """
        with self.store.edit() as state:
            progress = state.setdefault('relayProgress', {})
            for request_id, cursor, done in entries:
                progress.pop(request_id, None)  # Re-added last: pruning keeps the newest.
                progress[request_id] = dict(cursor=cursor, done=done)
            if chain:
                last = chain['requests'][-1]
                entry = progress.pop(last, None) or dict(cursor=0, done=False)
                entry.pop('chain', None)
                if not entry['done']:
                    entry['chain'] = chain
                progress[last] = entry
            for stale in list(progress)[:-self.RELAY_PROGRESS_KEPT]:
                if progress.pop(stale).get('done') and stale in (state.get('requests') or {}):
                    state['requests'][stale]['relayed'] = True  # Its entry is gone, but the user saw it.

    def _public_history(self, receipt, until):
        """Every public event up to `until`, for the turn-wide work snapshot."""
        path = Path(receipt['events']) if receipt.get('events') else None
        if not path or not path.is_file():
            return []
        events = []
        with path.open('rb') as source:
            for line in source.read(until).splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        return events

    def _prune_views(self, folder):
        try:
            views = sorted(folder.glob('*.html'), key=lambda item: item.stat().st_mtime)
            for stale in views[:-self.RELAY_VIEWS_KEPT]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass  # Presentation housekeeping never affects a turn.

    def queue_status(self):
        state = self.store.read()
        now = time.time()
        runner = state.get('runner')
        receipts = [(key, record) for key, record in state.get('requests', {}).items()
                    if record['generation'] == state['generation']]
        visible = [(key, record) for index, (key, record) in enumerate(receipts)
                   if record['status'] in ('captured', 'submitting', 'uncertain')
                   or index >= len(receipts) - 100]
        return {'worker': runner, 'workerState': ('running' if operation_running(runner) else 'stale') if runner else 'idle',
                'workerError': state.get('runnerError'), 'totalRequests': len(receipts),
                'requests': [{'requestId': key, 'status': record['status'],
                              'operation': record.get('operation'), 'events': record.get('events'),
                              'statusAgeSeconds': status_age(
                                  record.get('capturedAt') if record['status'] == 'captured'
                                  else record.get('submittedAt'), now)
                                  if record['status'] in ('captured', 'submitting', 'uncertain') else None}
                             for key, record in visible],
                'inflight': state['inflight']}

    def send_request(self, request_id, output=emit, timeout=86400):
        path = self.store.request_path(request_id)
        with self.store.edit() as state:
            record = state.get('requests', {}).get(request_id)
            if record is None:
                raise RuntimeError('Captured request was not found in this conversation.')
            if record['status'] != 'captured':
                return dict(requestId=request_id, existing=True, **deepcopy(record))
            if (not state['active'] or state.get('pending')
                    or record['generation'] != state['generation']
                    or record['session'] != state['main']):
                raise RuntimeError('Captured request no longer matches the active binding; nothing was sent.')
            oldest = next((key for key, value in state['requests'].items()
                           if value['status'] == 'captured'), None)
            if oldest != request_id:
                raise RuntimeError('An earlier captured request is queued; wait for it to settle.')
            if pending_work(state, request_id, include_queue=False):
                raise RuntimeError('Session has pending/uncertain work. Inspect or cancel it before submitting.')
            with path.open(encoding='utf-8', newline='') as source:
                text = source.read()
            # Claim before any provider call. A crash leaves an inspectable claim, never an automatic retry.
            op = uuid.uuid4().hex
            record.update(status='submitting', submittedAt=time.time(), submitterPid=os.getpid(), operation=op,
                          settings=deepcopy(state['settings']))
            state['inflight'][op] = dict(session=record['session'], kind='prompt', phase='admitted',
                                         requestId=request_id, submitterPid=os.getpid(), running=True)
        try:
            result = self._send(text, output=output, timeout=timeout, request_id=request_id)
            with self.store.edit() as state:
                state['requests'][request_id].update(status='completed', result=result)
                state['inflight'].pop(op, None)
            return dict(requestId=request_id, **result)
        except BaseException as exc:
            with self.store.edit() as state:
                entry = state['inflight'].get(op, {})
                record = state['requests'][request_id]
                if (entry.get('closed') or record['generation'] != state['generation'] or record.get('cancelRequested')
                        or record.get('providerOutcome', {}).get('status') == 'cancelled'):
                    status = 'canceled' if entry.get('settled') or entry.get('phase') == 'admitted' or entry.get('closed') else 'uncertain'
                elif entry.get('phase') == 'admitted' or entry.get('notDispatched'):
                    status = 'rejected'
                else:
                    status = 'failed' if entry.get('settled') else 'uncertain'
                record['status'] = status
                if status == 'rejected' and str(exc):
                    record['rejectedReason'] = str(exc)  # The relay shows it: the agent never saw the request.
                if status == 'uncertain':
                    entry['running'] = False
                else:
                    state['inflight'].pop(op, None)
            raise
        finally:
            path.unlink(missing_ok=True)

    def send(self, text, output=emit, timeout=86400):
        """File/programmatic input joins the same lifecycle as hook input."""
        request_id = uuid.uuid4().hex
        with self.store.edit() as state:
            if state.get('turnRoute', {}).get('route') == 'direct-result':
                raise RuntimeError('This turn was already dispatched; inspect its saved events instead of replaying it.')
            if state.get('turnRoute', {}).get('requestId'):
                raise RuntimeError('This turn has captured input. Observe its request ID instead of submitting it again.')
            if pending_work(state):
                raise RuntimeError('Session has pending/uncertain work. Inspect or cancel it before submitting.')
            if not state['active'] or state.get('pending') or state.get('helpMenu'):
                raise RuntimeError('Mode is off or a menu is pending; no task was sent.')
            self.store.capture(state, request_id, text)
            state['requests'][request_id]['source'] = 'file'
        return self.send_request(request_id, output, timeout)

    def cancel(self):
        with self.store.edit() as state:
            owned = next((x for x in state['owned'] if x['name'] == state['main']), None)
            if not owned:
                raise RuntimeError('No owned session found.')
            operations = [entry for entry in state['inflight'].values()
                          if entry['session'] == owned['name']]
            if not operations:
                return {'canceled': False, 'message': 'No active turn to cancel; queued messages are unchanged.'}
            self.store.signal_cancel(state, owned['name'])
            for record in state.get('requests', {}).values():
                if record['session'] == owned['name'] and record['status'] in ('submitting', 'uncertain'):
                    record['cancelRequested'] = True
            # Live ACPX prompt bridges poll their own operation cancel file.
            # An extra session-wide cancel can arrive after that turn settles
            # and accidentally cancel the next queued follow-up.
            local_signal = (owned.get('transport') != 'native' and
                            all(entry.get('kind') == 'prompt' and operation_running(entry)
                                for entry in operations))
        if local_signal:
            return {'canceled': True, 'method': 'operation-signal'}
        return self.backend.control(owned, ['cancel', '-s', owned['name']])

    def acknowledge(self, operation):
        # Host must inspect provider status/files first; a live submitter is never releasable.
        with self.store.edit() as state:
            if operation not in state['inflight']:
                raise RuntimeError('Unknown operation.')
            if operation_running(state['inflight'][operation]):
                raise RuntimeError('Submitter is still running; cancel and let it finish before acknowledging.')
            entry = state['inflight'].pop(operation)
            if entry.get('requestId'):
                state['requests'][entry['requestId']]['status'] = 'acknowledged'
        return {'acknowledged': operation}
