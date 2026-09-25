"""One provider turn: admission, submission, public relay and settlement."""
import json
import os
import queue
import threading
import time
import uuid

import acpx
import native_agy
import native_commands
import viewer
from operations import emit, menu_holds, pending_work
from presentation import PERMISSION_CODES, permission_stop
from progress import progress_mode, public_progress
from state import agent_entry, agent_label, routing_mode, direct_payload


def without_name(payload):
    """A /d payload without the agent name it starts with (and one separator after it)."""
    rest = payload.lstrip()
    rest = rest[len(rest.split(None, 1)[0]):]
    if rest.startswith('\r\n'):
        return rest[2:]
    return rest[1:] if rest and rest[0].isspace() else rest


class DispatchMixin:
    def prompt(self, owned, text, generation, pending=None, timeout=86400, output=emit, routing_policy=None, request_id=None):
        folder = self.store.root / 'requests' / self.store.key / 'operations'
        folder.mkdir(parents=True, exist_ok=True)
        op = (self.store.read()['requests'][request_id]['operation']
              if request_id is not None else uuid.uuid4().hex)
        prompt_path, events_path = folder / (op + '.txt'), folder / (op + '.jsonl')
        process = None
        completed = False
        not_dispatched = False
        spawn_attempted = False
        provider_outcome = None
        observed_session = None
        try:
            with self.store.edit() as state:
                if request_id is None and routing_policy is not None and routing_mode(state) != routing_policy:
                    raise RuntimeError('Routing mode changed during dispatch; nothing was sent.')
                if request_id is not None:
                    record = state['requests'][request_id]
                    if (record['status'] != 'submitting' or record['generation'] != state['generation']
                            or record.get('cancelRequested')
                            or record['settings'] != (agent_entry(state, record['session']) or {}).get('settings')):
                        raise RuntimeError('Captured request no longer matches the active binding; nothing was sent.')
                if not self.valid(state, generation, pending) or (pending is None and menu_holds(state, owned['name'])):
                    raise RuntimeError('Mode is off or a setup menu is pending; nothing was sent.')
                if pending_work(state, request_id, include_queue=False, session=owned['name']):
                    raise RuntimeError('Session has pending/uncertain work. Inspect or cancel it before resubmitting.')
                if request_id is None:
                    state['inflight'][op] = dict(session=owned['name'], kind='prompt', origin='readiness',
                                                 submitterPid=os.getpid(), running=True)
                prompt_path.write_text(text, encoding='utf-8', newline='')
                # Persist the dispatch boundary before spawning. A crash after
                # this checkpoint is uncertain, never safe to replay silently.
                state['inflight'][op].update(phase='dispatching', events=str(events_path))
            label = agent_label(self.store.read(), owned['name'])
            viewer.ensure(self.store, label)  # Reopens the viewer window when /cli view is on.
            with self.store.edit() as state:
                target = agent_entry(state, owned['name']) or {}
                if (not self.valid(state, generation, pending) or state['inflight'].get(op, {}).get('closed')
                        or (pending is None and menu_holds(state, owned['name']))
                        or (request_id is None and routing_policy is not None and routing_mode(state) != routing_policy)
                        or (request_id and (state['requests'][request_id].get('cancelRequested')
                            or state['requests'][request_id]['settings'] != target.get('settings')
                            or state['requests'][request_id]['session'] != owned['name'] or not target.get('ready')))):
                    raise RuntimeError('Operation canceled before dispatch; nothing was sent.')
                owned = dict(owned, requestId=request_id or op, cancelFile=str(self.store.cancel_path(op)),
                             configCache=self.config_cache(),
                             progressMode=progress_mode(state))
                spawn_attempted = True
                process = self.backend.start(owned, ['-s', owned['name'], '--file', str(prompt_path)], timeout)
                state['inflight'][op].update(pid=process.pid, uncertain=True, running=True)
                if request_id is not None:
                    state['requests'][request_id].update(events=str(events_path))
                if (owned['role'] == 'main' and state.get('turnRoute', {}).get('route') == 'direct'
                        and (request_id is None or state['turnRoute'].get('requestId') == request_id)):
                    state['turnRoute'] = dict(route='direct-result', requestId=request_id,
                                              operation=op, events=str(events_path))
            output({'type': 'dispatched', 'events': str(events_path),
                    **({'requestId': request_id} if request_id is not None else {})})
            lines = queue.Queue()
            def reader(stream, channel):
                try:
                    for line in stream:
                        lines.put((channel, line))
                finally:
                    stream.close()
                    lines.put((channel, None))
            for channel, stream in (('out', process.stdout), ('err', process.stderr)):
                threading.Thread(target=reader, args=(stream, channel), daemon=True).start()
            finished, stop, had_message, errored = 0, None, False, False
            runtime_result = None
            runtime_transport = owned.get('transport') != 'native' and not owned.get('bootstrapPrompt')
            observed = {}
            until = time.monotonic() + timeout + 15
            with events_path.open('w', encoding='utf-8') as log:
                relay = self.adapter.PublicRelay(owned['progressMode'])
                def publish(events):
                    nonlocal stop, had_message, errored
                    for event in events:
                        log.write(json.dumps(event) + '\n')
                        log.flush()
                        output(event)
                        if event['type'] == 'done':
                            stop = event['stopReason']
                        had_message |= event['type'] in ('message', 'artifact')
                        errored |= event['type'] == 'error'
                cancel_sent = False
                while finished < 2:
                    if owned.get('transport') == 'native' and native_agy.marker(owned, '.cancel').exists():
                        process.terminate()
                        raise RuntimeError('Native CLI turn canceled. Background provider tasks, if any, require provider verification.')
                    if owned.get('bootstrapPrompt') and not cancel_sent and self.store.cancel_path(op).exists():
                        # The bridge polls its cancel file; the ACPX CLI used for the
                        # first readiness probe does not. Cancel through the owner
                        # (nothing but this probe can be running before activation),
                        # then keep draining so settlement is still observed.
                        cancel_sent = True
                        try:
                            self.backend.collect(self.backend.start(owned, ['cancel', '-s', owned['name']]))
                        except RuntimeError:
                            pass  # An already settled probe has nothing left to cancel.
                        until = min(until, time.monotonic() + 15)
                    if time.monotonic() > until:
                        raise RuntimeError('Prompt timed out; inspect status/files before retrying. Work may continue.')
                    try:
                        channel, line = lines.get(timeout=.25)
                    except queue.Empty:
                        publish(relay.due())
                        continue
                    publish(relay.due())
                    if line is None:
                        finished += 1
                        continue
                    if channel == 'err':
                        # Arbitrary runtime stderr can contain credentials or private diagnostics.
                        continue
                    try:
                        raw = json.loads(line)
                        if owned.get('transport') == 'native':
                            # Persist native identity before relaying any response, so follow-ups resume it.
                            identity = raw.get('conversation_id') or raw.get('result', {}).get('conversation_id')
                            if isinstance(identity, str) and identity:
                                if owned.get('providerSession') and owned['providerSession'] != identity:
                                    output({'type': 'context_warning', 'message':
                                        'Native Antigravity changed conversation identity; earlier context may not be retained.'})
                                owned['providerSession'] = identity
                                with self.store.edit() as latest:
                                    for item in latest['owned']:
                                        if item['name'] == owned['name']:
                                            item['providerSession'] = identity
                            # CLI-handled commands can return a standalone JSON result envelope.
                            if 'status' in raw and 'event' not in raw:
                                raw = {'event': 'result', 'result': raw}
                            if raw.get('event') == 'result' and not had_message and not relay.text:
                                response = raw.get('result', {}).get('response')
                                if isinstance(response, str) and response:
                                    publish([{'type': 'message', 'text': response}])
                                elif raw.get('result', {}).get('command'):
                                    publish([{'type': 'message', 'text': json.dumps(raw['result']['command'], ensure_ascii=False, indent=2)}])
                            event = native_agy.public_event(raw)
                            if raw.get('event') == 'result' and event and event['type'] == 'error':
                                publish([event])
                                event = {'type': 'done', 'stopReason': 'error'}
                        elif owned.get('bootstrapPrompt'):
                            # Only the first readiness probe uses ACPX's
                            # empty-session recovery. Project the same public
                            # message/completion types as the shared bridge.
                            event = acpx.public_event(raw)
                        else:
                            kind = raw.get('type')
                            if kind == 'runtime_session':
                                observed['acpxRecordId'] = raw['recordId']
                                event = None
                            elif kind == 'prompt_started':
                                with self.store.edit() as latest:
                                    if op in latest['inflight']:
                                        latest['inflight'][op]['phase'] = 'started'
                                event = None
                            elif kind == 'runtime_result':
                                runtime_result = raw
                                outcome = raw['result']
                                provider_outcome = {key: outcome[key] for key in ('status', 'stopReason') if key in outcome}
                                if isinstance(raw.get('providerSession'), str):
                                    observed_session = raw['providerSession']
                                completed = raw.get('settled') is True
                                if raw.get('cursor'):
                                    observed['watchCursor'] = raw['cursor']
                                elif raw.get('resetCursor'):
                                    observed['watchCursor'] = None
                                error = outcome.get('error') or {}
                                if outcome['status'] == 'failed' and error.get('code') in PERMISSION_CODES:
                                    access = owned['settings'].get('accessName') or owned['settings']['access']
                                    publish([{'type': 'error', 'code': error['code'],
                                              'message': permission_stop(label, access)}])
                                elif outcome['status'] != 'completed':
                                    publish([{'type': 'error', 'message': error.get('message', 'Agent turn canceled.')}])
                                elif not raw.get('outputComplete'):
                                    publish([{'type': 'error', 'message': 'Turn completed but output observation is incomplete. Inspect the session; do not replay work.'}])
                                event = {'type': 'done', 'stopReason': outcome.get('stopReason', 'error')}
                            elif kind == 'runtime_failure':
                                not_dispatched = raw.get('dispatchStarted') is False
                                completed = not_dispatched
                                event = {'type': 'error', 'message': raw['message']}
                            elif kind in ('message', 'plan', 'artifact'):
                                event = raw
                            elif kind in ('activity', 'usage'):
                                event = public_progress(raw)
                            else:
                                event = None
                    except (ValueError, AttributeError, TypeError):
                        continue
                    if event:
                        publish(relay.feed(event))
                publish(relay.flush() + relay.activity.flush())
                log.flush()
                os.fsync(log.fileno())  # Persist all buffered public output before advancing the journal cursor.
            code = process.wait(timeout=5)
            if not runtime_transport:
                completed = stop is not None
            if observed:
                owned.update(observed)
                with self.store.edit() as latest:
                    for item in latest['owned']:
                        if item['name'] == owned['name']:
                            item.update(observed)
            # Some advertised ACP commands complete without a public message
            # (Grok /context). This exception never applies to ordinary prompts
            # or unknown commands, including readiness probes.
            known_command = native_commands.name_of(text) in (owned.get('advertisedCommands') or [])
            if (runtime_transport and runtime_result is None) or code or errored or stop != 'end_turn' or (
                    not had_message and not known_command and owned.get('transport') != 'native'):
                raise RuntimeError(self.adapter.LABEL + ' did not complete successfully (exit=' + str(code) +
                                   ', stop=' + str(stop) + '). Inspect public events: ' + str(events_path))
            result = {'events': str(events_path), 'stopReason': stop}
            if observed_session:
                result['providerSession'] = observed_session
            if known_command and not had_message:
                result['noPublicOutput'] = True
            return result
        finally:
            if not spawn_attempted:
                not_dispatched = completed = True
            if process and process.poll() is None:
                process.kill()  # Submitter only; owned agent is canceled through off/cancel.
                process.wait(timeout=5)
            prompt_path.unlink(missing_ok=True)
            if process or op in self.store.read()['inflight']:
                if owned.get('transport') == 'native':
                    native_agy.marker(owned, '.running').unlink(missing_ok=True)
                with self.store.edit() as state:
                    if request_id is not None and op in state['inflight']:
                        state['inflight'][op]['settled'] = completed
                        state['inflight'][op]['notDispatched'] = not_dispatched
                        if provider_outcome:
                            state['requests'][request_id]['providerOutcome'] = provider_outcome
                    elif completed or state['inflight'].get(op, {}).get('closed'):
                        state['inflight'].pop(op, None)
                    elif op in state['inflight']:
                        state['inflight'][op]['running'] = False
                    canceled = not self.valid(state, generation, pending)
                if canceled:
                    self.cleanup(owned)

    def _send(self, text, output=emit, timeout=86400, request_id=None):
        state = self.store.read()
        if request_id is None and state.get('turnRoute', {}).get('route') == 'direct-result':
            raise RuntimeError('This turn was already dispatched; inspect its saved events instead of replaying it.')
        if request_id is None and state.get('turnRoute', {}).get('requestId'):
            raise RuntimeError('This turn has captured input. Observe its request ID instead of submitting it again.')
        record = state['requests'][request_id] if request_id is not None else {}
        if request_id is not None and record['generation'] != state['generation']:
            raise RuntimeError('Captured request binding changed; nothing was sent.')
        session = record.get('session') or state['main']
        target = agent_entry(state, session)
        self.use(target['backend'] if target else self.agent_of(state))
        if (not state['active'] or menu_holds(state, session) or (request_id is None and state.get('pending'))
                or (request_id is None and state.get('helpMenu'))):
            raise RuntimeError('Mode is off or a menu is pending; no task was sent.')
        policy = record['routingMode'] if request_id is not None else routing_mode(state)
        direct = policy == 'direct'
        if direct:
            payload = direct_payload(text)
            if payload is not None and record.get('named'):
                payload = without_name(payload)  # The agent's name picked the agent; it is not part of the task.
            if payload is None or not payload.strip():
                raise RuntimeError('Direct mode requires /d or $d followed by a task; nothing was sent.')
            text = payload
        # Host controls were parsed before stripping the Direct prefix. An
        # explicitly targeted /help now belongs to the provider, not CLI-MODE.
        provider_command = (native_commands.name_of(text) is not None if direct
                            else self.adapter.command_request(text))
        if any(item['role'] != 'main' for item in state['owned']):
            raise RuntimeError('Legacy or invalid session ownership. Run off before activating again.')
        owned = next((x for x in state['owned'] if x['name'] == session and x['ready']), None)
        if not owned:
            raise RuntimeError('No ready owned session matches this dispatch.')
        if hasattr(self.backend, 'validate_prompt'):
            self.backend.validate_prompt(owned)
        if hasattr(self.backend, 'prepare'):
            self.backend.prepare(owned)
            with self.store.edit() as latest:
                for item in latest['owned']:
                    if item['name'] == owned['name'] and owned.get('acpxRuntime'):
                        item['acpxRuntime'] = owned['acpxRuntime']
        before = None
        # The bridge already refuses a changed conversation, so only provider
        # commands (which need the current advertised command list) and
        # transports without that guard read session metadata first.
        if owned.get('transport') != 'native' and (
                provider_command or not getattr(self.backend, 'bridge_checks_identity', False)):
            before = self.backend.metadata(owned)
            if self.adapter.provider_identity(before) != owned.get('providerSession'):
                raise RuntimeError('Bound provider conversation changed before dispatch. Inspect it and stop/rebind; nothing was sent.')
            self.capture_commands(owned, before, state['generation'])
        if provider_command and hasattr(self.backend, 'validate_command'):
            admitted = self.backend.validate_command(owned, text)
            if admitted:
                owned = dict(owned, nativeAliases=admitted)
        if (self.adapter.NATIVE_HANDOFF and provider_command
                and owned.get('transport') != 'native'):
            owned = self.native_handoff(owned, state['generation'], output, routing_policy=policy, request_id=request_id)
        if owned.get('transport') == 'native':
            return self.prompt(owned, text, state['generation'], output=output, timeout=timeout,
                               routing_policy=policy, request_id=request_id)
        provider = self.adapter.provider_identity(before) if before is not None else owned.get('providerSession')
        result = self.prompt(owned, text, state['generation'], output=output, timeout=timeout,
                             routing_policy=policy, request_id=request_id)
        current_provider = result.pop('providerSession', None)
        if current_provider is None:
            try:
                after = self.backend.metadata(owned)
            except (OSError, RuntimeError, ValueError):
                # Completion is authoritative even if a later inspection fails.
                result['metadataWarning'] = 'Turn completed; follow-up session metadata could not be read. Do not replay work.'
                output({'type': 'context_warning', 'message': result['metadataWarning']})
                return result
            current_provider = self.adapter.provider_identity(after)
        if provider != current_provider or owned.get('providerSession') != provider:
            result['contextWarning'] = 'Provider session changed. Verify memory and rehydrate a compact brief; do not replay work.'
            output({'type': 'context_warning', 'message': result['contextWarning']})
        with self.store.edit() as latest:
            if self.valid(latest, state['generation']):
                for item in latest['owned']:
                    if item['name'] == owned['name']:
                        item['providerSession'] = current_provider
        return result

    def native_handoff(self, owned, generation, output, routing_policy=None, request_id=None):
        model = native_agy.prepare(owned['settings'])
        transition = uuid.uuid4().hex
        with self.store.edit() as state:
            if request_id is None and routing_policy is not None and routing_mode(state) != routing_policy:
                raise RuntimeError('Routing mode changed during dispatch; nothing was sent.')
            if (not self.valid(state, generation) or state['pending']
                    or pending_work(state, request_id, include_queue=False, session=owned['name'])):
                raise RuntimeError('Settle current work before switching to native Antigravity commands.')
            state.update(active=False, pending={'id': transition, 'stage': 'verifying', 'phase': 'native'})
        try:
            self.backend.close(owned)
            native = dict(owned, transport='native', nativeModel=model, providerSession=None,
                          nativeControl=str(self.store.root / 'native' / owned['name']))
            with self.store.edit() as state:
                if not self.valid(state, generation, transition):
                    raise RuntimeError('Native transport switch was canceled; no command sent.')
                state.update(active=True, pending=None, owned=[native if item['name'] == native['name'] else item
                                                               for item in state['owned']])
            output({'type': 'context_warning', 'message':
                'Antigravity native commands require a new native CLI conversation. The ACP session is closed; '
                'its history is retained but not copied. This command and subsequent replies use the native conversation. '
                'CLI-only interactive commands may report unavailable in headless mode.'})
            return native
        except BaseException:
            with self.store.edit() as state:
                if (state.get('pending') or {}).get('id') == transition:
                    state['pending'] = None
            raise
