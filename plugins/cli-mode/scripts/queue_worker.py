"""Detached queue worker, receipts and turn cancellation."""
from copy import deepcopy
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import uuid

from operations import _detached_workers, emit, follow_path, menu_holds, operation_running, pending_work, status_age
import agent_folder
import changes
import host
import menu_view
import relay_view
from state import agent_entry, agent_label, passing_line, team_lines


def request_label(state, request_id):
    """The agent a request went to, as every relay line names it: `Codex COD-7K`."""
    record = (state.get('requests') or {}).get(request_id) or {}
    return agent_label(state, record.get('session'), record)

# The worker runs the controller CLI entry point, not this module.
CONTROLLER = Path(__file__).resolve().with_name('controller.py')


class QueueMixin:
    def resume_monitoring(self):
        """Reattach to receipts after a host turn ends, without replaying prompts."""
        try:
            worker = self.ensure_pumps()
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

    def ensure_pumps(self):
        """Every agent's worker, for /cli resume: the first that needs attention, else the current agent's."""
        state = self.store.read()
        sessions = [item['name'] for item in state['owned'] if item.get('ready')]
        sessions.sort(key=lambda session: session != state.get('main'))
        results = [self.ensure_pump(session) for session in sessions] or [self.ensure_pump()]
        for kind in ('blocked', 'start-failed'):
            found = next((result for result in results if result.get('worker') == kind), None)
            if found:
                return found
        return results[0]

    def ensure_pump(self, session=None):
        """Start an agent's submitter (the current agent's by default), independent of the host turn.

        Each agent has its own worker and queue, so agents work side by side.
        """
        if os.environ.get('CLI_MODE_TEST_DISABLE_AUTORUN') == '1':
            return {'worker': 'disabled-for-tests'}
        with self.store.edit() as state:
            session = session or state.get('main')
            mine = {key: value for key, value in state.get('requests', {}).items() if value.get('session') == session}
            captured = [key for key, value in mine.items() if value['status'] == 'captured']
            target = agent_entry(state, session)
            if not state['active'] or not target or not target.get('ready'):
                return {'worker': 'idle', 'queued': len(captured)}
            runner = (state.get('runners') or {}).get(session) or {}
            if runner and operation_running(runner):
                return {'worker': 'running', 'pid': runner['pid'], 'queued': len(captured)}
            if runner:
                # A vanished owner may have reached the provider. Never replay its
                # admitted prompt merely because the Python process disappeared.
                for record in mine.values():
                    if record['status'] == 'submitting' and record.get('submitterPid') == runner.get('pid'):
                        record['status'] = 'uncertain'
                        operation = state['inflight'].get(record.get('operation'))
                        if operation:
                            operation['running'] = False
                state['runners'].pop(session, None)
            for record in mine.values():
                if record['status'] == 'submitting':
                    operation = state['inflight'].get(record.get('operation'))
                    if operation and not operation_running(operation):
                        record['status'] = 'uncertain'
                        operation['running'] = False
            if any(value['status'] in ('submitting', 'uncertain') for value in mine.values()) or any(
                    operation.get('session') == session for operation in state['inflight'].values()):
                return {'worker': 'blocked', 'queued': len(captured),
                        'message': 'Inspect and reconcile the earlier operation before resuming the queue.'}
            if not captured:
                return {'worker': 'idle', 'queued': 0}
            token = uuid.uuid4().hex
            command = [sys.executable, str(CONTROLLER), '--thread', self.store.thread,
                       '--workspace', self.store.workspace, '--data-root', str(self.store.root),
                       'pump', '--token', token, '--session', session]
            options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
                       'stderr': subprocess.DEVNULL, 'close_fds': True}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            else:
                options['start_new_session'] = True
            _detached_workers[:] = [child for child in _detached_workers if child.poll() is None]
            process = subprocess.Popen(command, **options)
            _detached_workers.append(process)
            state.setdefault('runners', {})[session] = dict(pid=process.pid, token=token, started=time.time())
            state.pop('runnerError', None)
            return {'worker': 'started', 'pid': process.pid, 'queued': len(captured)}

    # After draining, stay ready this long for the next message: it then skips a
    # new worker process and reuses this process's warm ACPX bridge.
    PUMP_LINGER = float(os.environ.get('CLI_MODE_PUMP_LINGER', 300))

    def pump(self, token, session=None):
        """Drain one agent's captured requests in order; stop at any uncertain outcome."""
        idle_since = None
        seen = None
        session = session or self.store.read().get('main')
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
                runner = (state.get('runners') or {}).get(session) or {}
                lingering = (runner.get('token') == token and state['active'] and
                             not any(record['status'] == 'captured' and record.get('session') == session
                                     for record in state.get('requests', {}).values()))
                if lingering and time.monotonic() - idle_since < self.PUMP_LINGER:
                    time.sleep(.25)
                    continue
            with self.store.edit() as state:
                runners = state.get('runners') or {}
                runner = runners.get(session) or {}
                if runner.get('token') != token or runner.get('pid') != os.getpid():
                    return {'worker': 'replaced'}
                target = agent_entry(state, session)
                if not state['active'] or target is None:
                    runners.pop(session, None)
                    return {'worker': 'stopped'}
                operations = [op for op in state['inflight'].values() if op.get('session') == session]
                if operations or not target.get('ready') or any(
                        record['status'] in ('submitting', 'uncertain') and record.get('session') == session
                        for record in state.get('requests', {}).values()):
                    # An unrelated live submitter can finish, and a setting change ends; an uncertain
                    # one needs deliberate reconciliation before further dispatch.
                    if any(not operation_running(op) for op in operations):
                        runners.pop(session, None)
                        return {'worker': 'blocked'}
                    wait = True
                    request_id = None
                else:
                    wait = False
                    request_id = next((key for key, record in state.get('requests', {}).items()
                                       if record['status'] == 'captured' and record.get('session') == session), None)
                    if request_id is None:
                        if idle_since is None and self.PUMP_LINGER > 0:
                            idle_since = time.monotonic()
                            continue
                        runners.pop(session, None)
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
                        (state.get('runners') or {}).pop(session, None)
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
        runner = (state.get('runners') or {}).get(record.get('session'))
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
        label = request_label(self.store.read(), request_id)
        passing = None
        if cursor == 0 and status != 'captured':
            # Empty polls keep cursor zero; claim the introduction independently,
            # under the state lock, including across separate controller processes.
            with self.store.edit() as state:
                record = state.get('requests', {}).get(request_id)
                if record is not None and not record.get('passingAnnounced'):
                    record['passingAnnounced'] = True
                    passing = passing_line(label)
        show_work = (self.store.read().get('progressMode') or 'activity') != 'quiet'
        history = self._public_history(receipt, position)
        artifacts = [event['content'] for event in public if event['type'] == 'artifact']
        # Mid-turn: a Markdown update the host posts as is. Views only render in
        # a final response, so the turn's one view comes when it is done.
        update = relay_view.markdown(label, public, history, passing=passing,
                                     footer=None if done else footer, show_work=show_work,
                                     workspace=self.store.workspace)
        result = dict(requestId=request_id, cursor=position, done=done, status=status,
                      idleSeconds=view.get('withoutPublicUpdateSeconds'), markdown=update,
                      messageView=None, reference=None, text=update, artifacts=artifacts)
        if done:
            folder = Path(view_dir) if view_dir else self.store.root / 'views' / self.store.key
            path, text, _ = relay_view.render(
                label, [event for event in history if event.get('type') != 'context_warning'],
                folder / (request_id + '-final-' + uuid.uuid4().hex[:6] + '.html'),
                footer=footer, show_work=show_work, workspace=self.store.workspace, receipt=receipt.get('changes'),
                saved=receipt.get('saved'), refs=receipt.get('refs'),
                tests=receipt.get('tests'), overlaps=receipt.get('overlaps'))
            self._prune_views(folder)
            result.update(text=text, reference=menu_view.reference(path), messageView=dict(
                path=path, format='inline-html', label=label, kind='relay'))
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
        label = request_label(self.store.read(), request_id)
        result = dict(requestId=request_id, cursor=cursor, done=False, status=status, agent=label,
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
            update = relay_view.markdown(label, public, [], show_work=False, color=color)
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
            label, public, history, footer=footer, show_work=show_work, color=color,
            receipt=view['receipt'].get('changes'), saved=view['receipt'].get('saved'),
            refs=view['receipt'].get('refs'), tests=view['receipt'].get('tests'),
            overlaps=view['receipt'].get('overlaps')))

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
                        status=records[request_ids[-1]]['status'], agent=request_label(state, request_ids[-1]),
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
        label = request_label(self.store.read(), request_id)
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
        end = self.FOLLOW_ENDS.get(status, '{} stopped (' + status + ').').format(label)
        record = (self.store.read().get('requests') or {}).get(request_id) or {}
        receipt = record.get('changes')
        if receipt:
            count = receipt.get('files') or 0
            end += (' It changed ' + str(count) + (' file' if count == 1 else ' files') + ' (+' +
                    str(receipt.get('added', 0)) + ' -' + str(receipt.get('removed', 0)) + ').' if count else
                    ' It changed no files.')
        if record.get('saved'):
            end += ' ' + agent_folder.summary('It', record['saved'])
        if record.get('tests'):
            end += ' Tests ' + ('passed.' if record['tests']['passed'] else 'FAILED.')
        if record.get('overlaps'):
            end += ' ⚠ Another agent edited the same files.'
        say(end)
        return dict(requestId=request_id, status=status, done=True)

    def undo(self, session=None):
        """`/cli undo [name]`: put back the files an agent's last turn changed, if nothing changed them since."""
        state = self.store.read()
        session = session or state.get('main')
        label = agent_label(state, session) if session else 'The agent'
        found = [(record.get('capturedAt') or 0, key, record) for key, record in (state.get('requests') or {}).items()
                 if record.get('session') == session and (record.get('changes') or {}).get('files')]
        if not found:
            text = label + ' has no turn to undo: undo works on a turn that changed files in a git repository.'
            return dict(message=text, text=text)
        _, request_id, record = max(found, key=lambda item: item[0])
        if record.get('undone'):
            text = label + '\'s last turn that changed files is already undone.'
            return dict(message=text, text=text)
        if record.get('status') in ('captured', 'submitting'):
            alias = (agent_entry(state, session) or {}).get('alias') or ''
            text = label + ' is still working; /cli cancel ' + alias.lower() + ' first, then undo.'
            return dict(message=text, text=text)
        workspace = record.get('workspace') or self.store.workspace
        # Only what the agent's own tools edited: the receipt covers the whole folder, so another agent's
        # edits made meanwhile are in it too. A turn saved before tool edits were recorded undoes its receipt.
        own = record.get('touched')
        restored, removed, conflicts, left = changes.undo(workspace, record['changes'],
                                                          only=None if own is None else set(own))
        note = ('' if not left else ' Left as they are, not edited by ' + label + '\'s own tools: ' +
                ', '.join(left[:6]) + (' and more' if len(left) > 6 else '') + '.')
        if conflicts:
            text = ('Nothing was undone: ' + ', '.join(conflicts[:6]) + (' and more' if len(conflicts) > 6 else '') +
                    (' has' if len(conflicts) == 1 else ' have') + ' changed since ' + label + '\'s turn.')
            return dict(message=text, text=text, conflicts=conflicts)
        if not restored and not removed:
            text = ('Nothing was undone: ' + label + '\'s own tools edited none of the files its last turn changed.'
                    + note)
            return dict(message=text, text=text, left=left)
        with self.store.edit() as latest:
            latest['requests'][request_id]['undone'] = True
        parts = (['restored ' + ', '.join(restored)] if restored else []) + (['removed ' + ', '.join(removed)]
                                                                             if removed else [])
        text = 'Undid ' + label + '\'s last turn: ' + '; '.join(parts) + '.' + note
        return dict(message=text, text=text, restored=restored, removed=removed, left=left)

    def tests(self, text=None):
        """`/cli test [command|off]`: the command CLI-MODE runs after each agent turn that changes files."""
        import test_gate
        workspace = self.store.workspace
        text = (text or '').strip()
        if '\n' in text or '\r' in text:
            reply = 'A test command is one line, such as /cli test npm test. Nothing was changed.'
        elif text.casefold() == test_gate.OFF:
            test_gate.set_command(self.store.root, workspace, test_gate.OFF)
            reply = 'Tests are off for this project. /cli test auto finds them again.'
        elif text.casefold() == 'auto':
            test_gate.set_command(self.store.root, workspace, None)
            found, source = test_gate.detect(workspace)
            reply = ('Tests for this project: ' + found + ' (found from ' + source + '), after each agent turn that '
                     'changes files.' if found else 'No test setup found in this project, so no tests run.')
        elif text:
            test_gate.set_command(self.store.root, workspace, text)
            reply = ('Tests for this project: ' + text + '. CLI-MODE runs them after each agent turn that '
                     'changes files, and the answer says whether they passed.')
        else:
            value = test_gate.saved(self.store.root, workspace)
            found, source = test_gate.detect(workspace)
            if value == test_gate.OFF:
                reply = 'Tests are off for this project. /cli test auto finds them again.'
            elif value:
                reply = ('Tests for this project: ' + value + ' (after each agent turn that changes files). '
                         '/cli test off stops them.')
            elif found:
                reply = ('Tests for this project: ' + found + ' (found from ' + source + '), after each agent turn '
                         'that changes files. /cli test off stops them; /cli test <command> sets another.')
            else:
                reply = ('No test setup found in this project, so no tests run. /cli test <command> sets one, e.g. '
                         '/cli test npm test.')
        return dict(message=reply, text=reply)

    def brief(self, action=None, text=None):
        """`/cli brief`, `/cli brief clear`, `/cli brief-add <text>`: the brief every agent reads first."""
        workspace = self.store.workspace
        where = agent_folder.ROOT + '/' + agent_folder.BRIEF
        if action == 'add':
            if not text or not text.strip():
                reply = 'Use /cli brief-add <text>: one point every agent reads before its task.'
            else:
                points = agent_folder.add_brief(workspace, text)
                reply = ('Added to the project brief (' + str(len(points)) + (' point' if len(points) == 1 else
                         ' points') + ', ' + where + '). Every agent reads it before its next task.')
        elif action == 'clear':
            reply = ('Your points and the host\'s notes are cleared from the project brief; the running agents stay '
                     'listed.' if agent_folder.clear_brief(workspace) else 'There is nothing in the project brief to '
                     'clear.')
        else:
            points, notes, team = agent_folder.read_brief(workspace)
            if not points and not notes and not team:
                reply = 'No project brief yet. /cli brief-add <text> adds a point every agent reads before its task.'
            else:
                headings = [line[4:] for line in notes if line.startswith('### ')]
                lines = ['Project brief (' + where + '), read by every agent first:']
                lines += ([str(index) + '. ' + point for index, point in enumerate(points, 1)] if points else
                          ['No points yet: /cli brief-add <text> adds one.'])
                if headings:
                    lines.append('Host notes: ' + str(len(headings)) + ' (latest: ' + headings[-1] + ').')
                if team:
                    lines.append('Agents listed: ' + ', '.join(line[2:].split(':', 1)[0] for line in team) + '.')
                reply = '\n'.join(lines)
        return dict(message=reply, text=reply)

    NO_USAGE = 'Usage reporting not supported through its CLI'

    def usage_report(self, session=None):
        """`/cli usage [name]`: each running agent's plan usage, from its own CLI, or that it can't report it.

        Every agent is asked at once (Claude's local /usage alone takes seconds). Nothing is sent to an agent's
        model: each helper reads what its CLI or account already knows.
        """
        import confirmation
        from concurrent.futures import ThreadPoolExecutor
        from state import live_agents
        state = self.store.read()
        sessions = [session] if session else list(live_agents(state).values())
        entries = [entry for entry in (agent_entry(state, item) for item in sessions) if entry]
        if not entries:
            text = 'No agent is running. /cli starts one; /cli usage then shows what each has used.'
            return dict(message=text, text=text)

        def ask(entry):
            return confirmation.lookup(entry.get('backend'), entry.get('settings') or {},
                                       entry.get('workspace') or self.store.workspace, entry.get('providerSession'))
        with ThreadPoolExecutor(max_workers=len(entries)) as pool:
            summaries = list(pool.map(ask, entries))
        lines = []
        for entry, summary in zip(entries, summaries):
            label = agent_label(state, entry['name'])
            rows = confirmation.window_rows(summary)
            if rows:
                lines += [label + ':'] + ['  ' + row for row in rows]
            elif isinstance(summary.get('reason'), str) and summary['reason']:
                lines += [label + ':', '  can\'t report its usage: ' + summary['reason']]  # Its lookup failed.
            else:
                lines += [label + ':', '  ' + self.NO_USAGE]  # No way to ask its CLI (Grok, Cursor).
        text = '\n'.join(lines)
        return dict(message=text, text=text)

    def agent_dir(self, session=None):
        """`/cli dir [name]`: where an agent saves its files, as a full path and as the path in the project."""
        state = self.store.read()
        session = session or state.get('main')
        entry = agent_entry(state, session) if session else None
        if not entry or not entry.get('alias'):
            text = 'No agent is running. /cli starts one; each agent saves its files in its own folder.'
            return dict(message=text, text=text)
        label = agent_label(state, session)
        folder = agent_folder.path(entry.get('workspace') or self.store.workspace, entry['alias'])
        lines = [label + ' saves its files in:', str(folder),
                 'In this project: ' + agent_folder.relative(entry['alias']) + '/']
        listing = agent_folder.listing(folder)
        everything = (listing or {}).get('files', {})
        answers = sum(path.startswith(agent_folder.ANSWERS + '/') for path in everything)
        attached = sorted(path.split('/', 1)[1] for path in everything
                          if path.startswith(agent_folder.ATTACHMENTS + '/'))
        own = (agent_folder.ANSWERS + '/', agent_folder.ATTACHMENTS + '/')
        files = sorted(((path, value) for path, value in everything.items()
                        if not path.startswith(own)), key=lambda item: item[1][1], reverse=True)
        if not files:
            lines.append('Nothing saved yet.' if folder.is_dir() else
                         'Nothing saved yet; the folder is made with its next task.')
        else:
            more = listing.get('truncated')
            lines.append(('More than ' if more else '') + str(len(files)) + (' file' if len(files) == 1 else ' files') +
                         ', newest first: ' + ', '.join(path for path, _ in files[:5]) +
                         (', ...' if len(files) > 5 else ''))
        if answers:
            lines.append(str(answers) + (' answer' if answers == 1 else ' answers') + ' saved in ' +
                         agent_folder.ANSWERS + '/.')
        if attached:
            lines.append(str(len(attached)) + (' attached file' if len(attached) == 1 else ' attached files') + ' in ' +
                         agent_folder.ATTACHMENTS + '/ (removed when the agent closes): ' +
                         ', '.join(attached[:5]) + (', ...' if len(attached) > 5 else ''))
        text = '\n'.join(lines)
        return dict(message=text, text=text, path=str(folder), relative=agent_folder.relative(entry['alias']))

    def diff(self, session=None):
        """`/cli diff [name]`: what an agent's last turn changed in its folder, as a diff block."""
        state = self.store.read()
        session = session or state.get('main')
        found = [(record.get('capturedAt') or 0, key, record) for key, record in (state.get('requests') or {}).items()
                 if record.get('changes') and (session is None or record.get('session') == session)]
        label = agent_label(state, session) if session else 'The agent'
        if not found:
            text = (label + ' has no change receipt yet. Receipts are taken for turns in a git repository, from '
                    'the next turn on.')
            return dict(message=text, text=text)
        _, request_id, record = max(found, key=lambda item: item[0])
        label = request_label(state, request_id)
        receipt = record['changes']
        head = relay_view.receipt_markdown(label, receipt).split('\n\n')[0]
        if not receipt.get('files'):
            return dict(message=head, text=head, requestId=request_id)
        body = changes.diff_text(record.get('workspace') or self.store.workspace, receipt)
        if body is None:
            text = head + '\n\nIts diff could not be read (the snapshots may have been cleaned up by git).'
            return dict(message=text, text=text, requestId=request_id)
        # A fence longer than any backtick run in the diff, so file contents can't close it.
        import re
        fence = '`' * max([3] + [len(run) + 1 for run in re.findall('`+', body)])
        return dict(message=head, text=head + '\n\n' + fence + 'diff\n' + body.rstrip('\n') + '\n' + fence,
                    requestId=request_id)

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
        runner = (state.get('runners') or {}).get(state.get('main'))
        receipts = [(key, record) for key, record in state.get('requests', {}).items()
                    if record['generation'] == state['generation']]
        visible = [(key, record) for index, (key, record) in enumerate(receipts)
                   if record['status'] in ('captured', 'submitting', 'uncertain')
                   or index >= len(receipts) - 100]
        return {'worker': runner, 'workerState': ('running' if operation_running(runner) else 'stale') if runner else 'idle',
                'workerError': state.get('runnerError'), 'totalRequests': len(receipts),
                'workers': state.get('runners') or {},
                'requests': [{'requestId': key, 'status': record['status'], 'agent': request_label(state, key),
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
            target = agent_entry(state, record['session'])
            if (not state['active'] or menu_holds(state, record['session'])
                    or record['generation'] != state['generation'] or not target or not target.get('ready')):
                raise RuntimeError('Captured request no longer matches the active binding; nothing was sent.')
            oldest = next((key for key, value in state['requests'].items()
                           if value['status'] == 'captured' and value.get('session') == record['session']), None)
            if oldest != request_id:
                raise RuntimeError('An earlier captured request is queued; wait for it to settle.')
            if pending_work(state, request_id, include_queue=False, session=record['session']):
                raise RuntimeError('Session has pending/uncertain work. Inspect or cancel it before submitting.')
            with path.open(encoding='utf-8', newline='') as source:
                text = source.read()
            # Claim before any provider call. A crash leaves an inspectable claim, never an automatic retry.
            op = uuid.uuid4().hex
            record.update(status='submitting', submittedAt=time.time(), submitterPid=os.getpid(), operation=op,
                          settings=deepcopy(target['settings']))
            # A permission question belongs to the turn that stopped; the agent's next turn moves past it
            # (a queued follow-up, not only an answer), so /cli approve can't later answer a stale one.
            target.pop('approval', None)
            state['inflight'][op] = dict(session=record['session'], kind='prompt', phase='admitted',
                                         requestId=request_id, submitterPid=os.getpid(), running=True)
            workspace = target.get('workspace') or self.store.workspace
            name = target.get('alias')
        # The agent's working folder, listed before the turn (empty until it exists). _send makes it, git-ignored,
        # when the task names it; the request file keeps the user's text, only the agent sees the added paragraph.
        folder = agent_folder.path(workspace, name)
        kept = agent_folder.listing(folder)
        agent_folder.keep_ignored(workspace)
        # The folder before the turn, for its change receipt (none outside a git repository).
        before = changes.snapshot(workspace)

        def receipt():
            """Taken before the request settles, so a relay started by its end always finds it."""
            return changes.compare(workspace, before, changes.snapshot(workspace)) if before else None

        def saved():
            """What the turn saved in the agent's working folder, git or not."""
            return agent_folder.compare(name, kept, agent_folder.listing(folder)) if folder is not None else None
        try:
            result = self._send(text, output=output, timeout=timeout, request_id=request_id,
                                working_folder=folder is not None)
            done, stored = receipt(), saved()
            tests = self._run_tests(workspace, name, done)  # Before completion, so the relay always has it.
            with self.store.edit() as state:
                state['requests'][request_id].update(status='completed', result=result)
                if done:
                    state['requests'][request_id]['changes'] = done
                if stored:
                    state['requests'][request_id]['saved'] = stored
                if tests:
                    state['requests'][request_id]['tests'] = tests
                self._note_touched(state, request_id, workspace)
                state['inflight'].pop(op, None)
            self._keep_answer(request_id, workspace, name, text)
            agent_folder.write_team(workspace, team_lines(self.store.read()))  # Idle now, with its answer.
            return dict(requestId=request_id, **result)
        except BaseException as exc:
            done = receipt() if not isinstance(exc, KeyboardInterrupt) else None
            stored = saved() if not isinstance(exc, KeyboardInterrupt) else None
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
                if done and done['files'] and status != 'rejected':
                    record['changes'] = done  # A failed or canceled turn may still have edited files.
                if stored and status != 'rejected':
                    record['saved'] = stored
                if status != 'rejected':
                    self._note_touched(state, request_id, workspace)
                if status == 'rejected' and str(exc):
                    record['rejectedReason'] = str(exc)  # The relay shows it: the agent never saw the request.
                if status == 'uncertain':
                    entry['running'] = False
                else:
                    state['inflight'].pop(op, None)
            if not isinstance(exc, KeyboardInterrupt) and status != 'rejected':
                self._keep_answer(request_id, workspace, name, text)  # A failed turn may still have answered.
                agent_folder.write_team(workspace, team_lines(self.store.read()))
            raise
        finally:
            path.unlink(missing_ok=True)

    def _run_tests(self, workspace, name, done):
        """The project's test command, after a turn that changed project files (a git receipt); else None."""
        import test_gate
        text = test_gate.command(self.store.root, workspace)
        if not text or not done or not done.get('files'):
            return None
        folder = agent_folder.path(workspace, name)
        log = None
        if folder is not None:
            logs = folder / 'tests'
            taken = [int(match[1]) for match in (re.match(r'(\d+)\.log$', entry.name)
                                                 for entry in (logs.glob('*.log') if logs.is_dir() else [])) if match]
            log = logs / ('%03d.log' % ((max(taken) + 1) if taken else 1))
            agent_folder.ensure(workspace, name)
        return test_gate.run(self.store.root, workspace, text, log)

    def _note_touched(self, state, request_id, workspace):
        """Record the files this turn's own tools edited, deleted or moved, and any another agent edited too.

        The change receipt covers the whole folder, so while agents work at once it includes each other's
        edits; their tool events say who touched what. A file edited by two agents in overlapping turns is
        flagged on the one that finished second (the other's answer may already be out).
        """
        record = state['requests'][request_id]
        record['endedAt'] = time.time()
        path = Path(record['events']) if record.get('events') else None
        root = Path(workspace).resolve()
        touched = []
        try:
            for line in (path.read_text(encoding='utf-8').splitlines() if path and path.is_file() else []):
                event = json.loads(line) if line.strip() else {}
                if event.get('type') != 'activity' or event.get('kind') not in ('edit', 'delete', 'move'):
                    continue
                for location in event.get('locations') or []:
                    raw = Path(location['path'])
                    try:
                        shown = (raw.resolve().relative_to(root) if raw.is_absolute() else raw).as_posix()
                    except ValueError:
                        shown = raw.as_posix()
                    if shown not in touched:
                        touched.append(shown)
        except (OSError, ValueError, KeyError, TypeError):
            return
        record['touched'] = touched
        start = record.get('submittedAt') or 0
        overlaps = []
        for other_id, other in (state.get('requests') or {}).items():
            if (other_id == request_id or other.get('session') == record.get('session') or not other.get('touched')
                    or not other.get('endedAt') or other['endedAt'] < start
                    or (other.get('submittedAt') or 0) > record['endedAt']):
                continue
            overlaps += [dict(path=path, agent=request_label(state, other_id))
                         for path in touched if path in other['touched']]
        if overlaps:
            record['overlaps'] = overlaps

    def _keep_answer(self, request_id, workspace, name, text):
        """Save the turn's full answer in the agent's folder and record the reference box: that file, then the
        files the turn created or changed, then existing ones its answer mentions. Never fails a turn."""
        from dispatch import without_name
        import native_commands
        from state import direct_payload
        try:
            state = self.store.read()
            record = state['requests'][request_id]
            task = direct_payload(text) or text
            task = without_name(task, record['named']) if record.get('named') else task
            if native_commands.name_of(task) is not None:
                return  # An agent's own command: its output is not an answer, and the project stays untouched.
            path = Path(record['events']) if record.get('events') else None
            events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] \
                if path and path.is_file() else []
            words = relay_view.messages(events)
            answer = agent_folder.save_answer(workspace, name, request_label(state, request_id), task, words)
            files = agent_folder.references(workspace, words, record.get('changes'), record.get('saved'), answer)
            tests = record.get('tests') or {}
            if tests.get('log') and not tests.get('passed'):
                files = [tests['log']] + [path for path in files if path != tests['log']]  # The failure, first.
            if answer or files:
                with self.store.edit() as latest:
                    latest['requests'][request_id]['refs'] = dict(answer=answer, files=files)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # The answer still reaches the chat; only its reference box is missing.

    def send(self, text, output=emit, timeout=86400):
        """File/programmatic input joins the same lifecycle as hook input."""
        request_id = uuid.uuid4().hex
        with self.store.edit() as state:
            if state.get('turnRoute', {}).get('route') == 'direct-result':
                raise RuntimeError('This turn was already dispatched; inspect its saved events instead of replaying it.')
            if state.get('turnRoute', {}).get('requestId'):
                raise RuntimeError('This turn has captured input. Observe its request ID instead of submitting it again.')
            if pending_work(state, session=state['main']):
                raise RuntimeError('Session has pending/uncertain work. Inspect or cancel it before submitting.')
            if not state['active'] or state.get('pending') or state.get('helpMenu'):
                raise RuntimeError('Mode is off or a menu is pending; no task was sent.')
            self.store.capture(state, request_id, text)
            state['requests'][request_id]['source'] = 'file'
        return self.send_request(request_id, output, timeout)

    def cancel(self, session=None):
        """Cancel an agent's running turn (the current agent's unless `session`); its queue is unchanged."""
        with self.store.edit() as state:
            owned = agent_entry(state, session)
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
