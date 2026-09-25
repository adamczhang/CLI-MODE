"""Owned-session lifecycle: activation, controls, readiness and shutdown."""
from copy import deepcopy
import os
import time
import uuid

import frontends
import installer
import names
import native_agy
import native_commands
from operations import operation_running, pending_work
from state import agent_entry, agent_label, agent_limit, last_used, live_agents


class OwnerNotRunning(RuntimeError):
    """A setting refused before sending it: no running ACPX owner holds the session. Nothing reached the agent."""


class BindingMixin:
    def bind(self, agent='agy', require_hooks=True, message_output=None, confirm=False, name=None):
        """Start a new agent (bind = spawn) with its kind's saved settings; `name` is a custom name.

        An explicit bind authorizes defaults, but never skips readiness gates. `confirm` asks for the
        confirmation as text when there is no view path.
        """
        adapter = self.use(agent)
        if name:
            error = names.custom_error(name, live_agents(self.store.read(), every=True))
            if error:
                raise ValueError(error)
        state = self.frontend(agent)
        if not frontends.confirmed(self.store.root, agent):
            result = self.first_time_check(agent)
            if not result['confirmed']:
                raise RuntimeError(result['message'])
        if name:
            with self.store.edit() as latest:
                latest['pending']['name'] = name.upper()
        saved = self.kind_settings(state, agent)
        defaults = adapter.DEFAULTS
        model, access = saved.get('model', defaults['model']), saved.get('access', defaults['access'])
        effort = saved.get('effort', defaults.get('effort'))
        confirm = confirm or bool(message_output)
        prefetched = self.prefetch_usage(agent, model, access, effort) if confirm else None
        result = self.activate(model, access, effort=effort, agent=agent, require_hooks=require_hooks)
        if confirm:
            # One command activates and returns the confirmation to display.
            result = dict(result, activation=self.activation_message(message_output, prefetched))
        return result

    @staticmethod
    def kind_settings(state, agent):
        """Saved settings for a new `agent`: its kind's newest running agent's, else the current agent's when it is
        the same kind. Another kind's model and effort are never carried across."""
        same = [item for item in state.get('owned') or [] if item.get('backend') == agent and item.get('settings')]
        if same:
            return max(same, key=lambda item: last_used(state, item['name']))['settings']
        return (state.get('settings') or {}) if state.get('backend') in (None, agent) else {}

    def disable(self):
        # Admission stops before any slow ACPX operation. Late completions cannot reactivate.
        with self.store.edit() as state:
            self.store.discard_captured(state)
            self.store.signal_cancel(state)
            state.pop('runners', None)
            state.pop('closeMenu', None)
            installer_run = (state.get('pending') or {}).get('installerRun')
            if installer_run:
                state['stoppedInstallerRun'] = installer_run  # For off(): the routing hook disables first.
            state.update(active=False, pending=None, main=None, modeMenu=False, helpMenu=None)
            state['generation'] += 1
        return state

    def cleanup(self, owned):
        try:
            self.backend.close(owned)
        except (OSError, RuntimeError, ValueError) as exc:
            # Another cleanup may have verified closure while this call was waiting.
            if not any(item['name'] == owned['name'] for item in self.store.read()['owned']):
                return None
            return {'session': owned['name'], 'error': str(exc)}
        with self.store.edit() as state:
            state['owned'] = [x for x in state['owned'] if x['name'] != owned['name']]
            state['inflight'] = {key: value for key, value in state['inflight'].items()
                                 if value['session'] != owned['name'] or operation_running(value)}
            # Keep live submitters tracked until they unwind, but remember verified closure.
            # A second close may reject an already closed session; it must not strand work.
            for value in state['inflight'].values():
                if value['session'] == owned['name']:
                    value['closed'] = True
            for request_id, record in state.get('requests', {}).items():
                if (record['session'] == owned['name'] and record['status'] in ('submitting', 'uncertain')
                        and not any(op.get('requestId') == request_id for op in state['inflight'].values())):
                    record['status'] = 'canceled'
        return None

    def off(self):
        state = self.disable()
        with self.store.edit() as saved:
            installer_run = saved.pop('stoppedInstallerRun', None)
        setup_cancel = None
        if installer_run:
            try:
                setup_cancel = installer.call('Cancel', run_id=installer_run)
            except RuntimeError:
                setup_cancel = {'status': 'unknown', 'message': 'Close the setup window; installer cancellation could not be confirmed.'}
        failures = [error for owned in state['owned'] if (error := self.cleanup(owned))]
        latest = self.settle_shutdown()
        remaining = {item['name'] for item in latest['owned']}
        failures = [error for error in failures if error['session'] in remaining]
        result = dict(active=False, shutdownComplete=not remaining and not latest['inflight'],
                      failures=failures, inflight=latest['inflight'], shutdownScope='owned-sessions',
                      processTreeVerified=False)
        if any(item.get('transport') == 'native' for item in state['owned']):
            result['backgroundTasksVerified'] = False
            result['note'] = 'Owned native CLI turns stopped; provider-managed background teams require separate verification.'
        if setup_cancel is not None:
            result['installerCancellation'] = setup_cancel
        return result

    def close(self, name=None):
        """`/cli close [name|all]` (= stop = off): one agent of several, a chooser, or everything.

        Closing the last agent, or all of them, is `off`. With several and no name, the reply is a chooser;
        its answer comes back as `close --name`.
        """
        state = self.store.read()
        agents = live_agents(state, every=True)
        if not name:
            if len(agents) <= 1:
                return self.off()
            with self.store.edit() as latest:
                latest['closeMenu'] = list(agents.values())
            return dict(latest, activationMenu=frontends.close_menu(latest))
        if name.casefold() == 'all':
            return self.off()
        found = names.resolve(name, agents)
        if not found or found[0] != 'match':
            raise RuntimeError('No running agent is named ' + name.upper() + '. /cli list shows them.')
        return self.off() if len(agents) == 1 else self.close_one(found[1])

    def close_one(self, session):
        """Close one agent while the others keep working: its queue, its running turn, its session."""
        with self.store.edit() as state:
            owned = agent_entry(state, session)
            if owned is None:
                raise RuntimeError('That agent is already closed.')
            label = agent_label(state, session)
            for record in state.get('requests', {}).values():
                if record['session'] == session and record['status'] == 'captured':
                    record['status'] = 'superseded'
                elif record['session'] == session and record['status'] in ('submitting', 'uncertain'):
                    record['cancelRequested'] = True
            self.store.signal_cancel(state, session)
            (state.get('runners') or {}).pop(session, None)
            if (state.get('pending') or {}).get('session') == session:
                state['pending'] = None  # Its settings menu closes with it.
            state.pop('closeMenu', None)
            others = [item for item in state['owned'] if item['name'] != session and item.get('ready')]
            was_current = state['main'] == session
            if was_current:
                # The most recently used of the others becomes the current agent.
                current = max(others, key=lambda item: last_used(state, item['name'])) if others else None
                state.update(main=current['name'] if current else None,
                             backend=current['backend'] if current else state['backend'],
                             settings=current['settings'] if current else state['settings'])
            state['active'] = bool(others)
            for item in state['owned']:
                if item['name'] == session:
                    item['ready'] = False  # Nothing more is sent to it.
            owned = deepcopy(owned)
            current = agent_label(state) if was_current and state['main'] else None  # Said only when it changed.
        failure = self.cleanup(owned)
        latest = self.settle_shutdown(session)
        closed = not any(item['name'] == session for item in latest['owned'])
        unwinding = {key: value for key, value in latest['inflight'].items() if value['session'] == session}
        lines = [label + ' is closed.' if closed and not unwinding else label + ' is closing, but shutdown is not complete.']
        if failure and not closed:
            lines.append('Could not close it: ' + failure['error'])
        if current:
            lines.append(current + ' is the current agent.')
        return dict(latest, closed=owned.get('alias'), shutdownComplete=closed and not unwinding,
                    failures=[failure] if failure and not closed else [], message=' '.join(lines))

    def make_current(self, name):
        """`/cli use <name>`: plain /d prompts go to this agent from now on."""
        with self.store.edit() as state:
            found = names.resolve(name, live_agents(state))
            if not found or found[0] != 'match':
                raise RuntimeError('No running agent is named ' + name.upper() + '. /cli list shows them.')
            owned = agent_entry(state, found[1])
            state.update(main=owned['name'], backend=owned['backend'], settings=owned['settings'])
            owned['lastUsedAt'] = time.time()
            label = agent_label(state)
        return dict(state, message=label + ' is the current agent: /d prompts without a name go to it.')

    def agents(self, limit=None):
        """`/cli list` (= agents): every running agent by name; `limit` sets how many may run at once."""
        if limit is not None:
            if not 1 <= limit <= 8:
                raise ValueError('Choose an agent limit from 1 to 8.')
            with self.store.edit() as state:
                state['agentLimit'] = limit
        state = self.store.read()
        message = frontends.agents_text(state, self.agent_activity(state))
        if limit is not None:
            message = 'Up to ' + str(limit) + ' agents can run at once.\n' + message
        return dict(state, message=message)

    @staticmethod
    def agent_activity(state):
        """Each agent's queue at a glance: session -> 'working', 'N queued' or 'idle'."""
        activity = {}
        for item in state.get('owned') or []:
            records = [record for record in (state.get('requests') or {}).values()
                       if record.get('session') == item['name']]
            queued = sum(record['status'] == 'captured' for record in records)
            working = any(record['status'] == 'submitting' for record in records)
            text = 'working' if working else 'idle'
            if queued:
                text += ', ' + str(queued) + ' queued'
            activity[item['name']] = text if item.get('ready') else 'not ready'
        return activity

    def config_cache(self):
        """Where the bridge may keep `acpx config show` output between processes."""
        folder = self.store.root / 'cache'
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / ('acpx-config-' + self.store.key[:16] + '.json'))

    SHUTDOWN_SETTLE = 10.0

    def settle_shutdown(self, session=None):
        """Give submitters of closed sessions a moment to unwind, so one `off`
        reports a settled shutdown instead of the host sleeping and retrying.
        With `session`, only that closed agent's submitters are waited for."""
        until = time.monotonic() + self.SHUTDOWN_SETTLE
        while True:
            unwinding = False
            with self.store.edit() as latest:
                # One liveness check per submitter per pass, so the prune and the
                # decision to keep waiting always agree.
                kept = {}
                for key, value in latest['inflight'].items():
                    alive = operation_running(value)
                    if value.get('closed') and not alive:
                        continue  # A closed session's submitter that exited has nothing left to report.
                    kept[key] = value
                    unwinding |= alive and session in (None, value['session'])
                latest['inflight'] = kept
            if session is not None:
                if not unwinding or time.monotonic() >= until:
                    return latest
                time.sleep(.25)
                continue
            remaining = {item['name'] for item in latest['owned']}
            if not remaining and not latest['inflight']:
                return latest
            # Unclosed sessions and stranded operations are real failures: report them
            # at once. Only a submitter that is still unwinding is worth waiting for.
            if remaining or not unwinding or time.monotonic() >= until:
                return latest
            time.sleep(.25)

    def valid(self, state, generation, pending=None):
        if state['generation'] != generation:
            return False
        return (state['pending'] or {}).get('id') == pending if pending else state['active']

    def control(self, owned, args, generation, pending=None):
        op = uuid.uuid4().hex
        process = None
        spawn_attempted = completed = False
        with self.store.edit() as state:
            if not self.valid(state, generation, pending):
                raise RuntimeError('Operation canceled by mode transition.')
            if pending_work(state, session=owned['name']):
                raise RuntimeError('Session has pending/uncertain work. Inspect or cancel it before changing settings.')
            # Like prompts, controls can reach the owner before the submitter
            # receives a response. Save custody before any external mutation.
            state['inflight'][op] = dict(session=owned['name'], kind='control', phase='dispatching',
                                         control=list(args), submitterPid=os.getpid(),
                                         running=True, uncertain=True)
        try:
            with self.store.edit() as state:
                if (not self.valid(state, generation, pending)
                        or state['inflight'].get(op, {}).get('closed')
                        or self.store.cancel_path(op).exists()):
                    raise RuntimeError('Operation canceled before dispatch; nothing was sent.')
                # Spawn is ordered against off; the slow response wait is not.
                spawn_attempted = True
                process = self.backend.start(dict(owned, cancelFile=str(self.store.cancel_path(op)),
                                                  configCache=self.config_cache()), args)
                state['inflight'][op]['pid'] = process.pid
            result = self.backend.collect(process)
            completed = True
            if isinstance(result, dict) and result.get('ownerRunning') is False:
                # Refused before sending, so no custody is kept: the caller may wake the owner and retry.
                raise OwnerNotRunning('No running ACPX owner holds this session; the setting was not sent.')
            return result
        finally:
            try:
                if process and process.poll() is None:
                    process.kill()  # Only this submitter; accepted owner work may continue.
                    process.wait(timeout=5)
            finally:
                with self.store.edit() as state:
                    if completed or not spawn_attempted or state['inflight'].get(op, {}).get('closed'):
                        state['inflight'].pop(op, None)
                    elif op in state['inflight']:
                        state['inflight'][op]['running'] = False
                    canceled = not self.valid(state, generation, pending)
                if canceled:
                    self.cleanup(owned)

    def provision(self, owned, generation, pending=None):
        name = owned['name']
        bootstrapped = (not owned.get('providerSession') and
                        getattr(self.backend, 'bootstrap_with_cli_readiness', False))
        # One readiness prompt per activation. Each is a full provider turn, so
        # a second one after settings would only repeat what the metadata check
        # below already proves; a model the plan cannot serve surfaces on the
        # first real prompt instead.
        answered = False
        steps = self.adapter.setting_steps(owned['settings'])
        unprompted = None  # The conversation a prompt-free settings change must leave in place.
        if bootstrapped:
            # ACPX may need to replace an empty provider session on its first
            # prompt. Do this only before user work, then pin the resulting
            # provider identity for all controls and later turns.
            self.control(owned, ['sessions', 'ensure', '--name', name], generation, pending)
            self.readiness(dict(owned, bootstrapPrompt=True), generation, pending)
            answered = True
            initial = self.backend.metadata(owned)
            owned['providerSession'] = self.adapter.provider_identity(initial)
            if not owned['providerSession']:
                raise RuntimeError('Initial ACPX readiness has no provider session identity.')
        elif owned.get('providerSession') and hasattr(self.backend, 'prepare'):
            # A setting change on a live session goes straight to its running shared owner. Only when the
            # owner has idled out does a readiness prompt wake it (strict resume) first: owner-only controls
            # never open a fresh provider conversation. Waking it every time cost a full provider turn per
            # /cli model, effort or access change (2026-09-23 audit).
            try:
                if steps:
                    self.control(owned, ['-s', name] + steps[0], generation, pending)
                    steps, unprompted = steps[1:], owned['providerSession']
                else:
                    self.readiness(owned, generation, pending)
            except OwnerNotRunning:
                self.readiness(owned, generation, pending)
            answered = True
        else:
            self.control(owned, ['sessions', 'ensure', '--name', name], generation, pending)
        for step in steps:
            self.control(owned, ['-s', name] + step, generation, pending)
        if not answered:
            owned['providerSession'] = self.backend.verify(owned)
            self.readiness(owned, generation, pending)
        record = self.backend.metadata(owned)
        identity = self.backend.verify(owned, record=record)
        if unprompted and identity != unprompted:
            # The wake prompt used to check this (the bridge refuses a changed conversation); without it,
            # the settings change checks it here.
            raise RuntimeError('Bound provider conversation changed before dispatch; inspect it and stop/rebind. '
                               'No prompt was sent.')
        owned['providerSession'] = identity
        self.capture_commands(owned, record, generation, pending)
        return identity

    def readiness(self, owned, generation, pending=None):
        marker = 'CLI_MODE_READY_' + uuid.uuid4().hex
        messages = []
        self.prompt(owned, 'Confirm readiness and identify your working directory. Include the exact marker '
                    + marker + ' in your reply. Do not edit files, run tools, or perform other work. Reply briefly.',
                    generation, pending, timeout=60,
                    output=lambda event: messages.append(event['text']) if event['type'] == 'message' else None)
        response = ''.join(messages)
        if marker not in response:
            tokens = [name for name in getattr(self.adapter, 'TOKEN_VARIABLES', ()) if os.environ.get(name)]
            note = (' ' + tokens[0] + ' is set, and ' + self.adapter.LABEL + ' signs in with it before its own login: '
                    'if it has expired, refresh or remove it.') if tokens else ''
            raise RuntimeError(self.adapter.LABEL + ' did not confirm readiness. '
                'Check sign-in, subscription/quota, and the provider response: ' + response[:600] + note)

    def capture_commands(self, owned, record, generation, pending=None):
        names = native_commands.from_record(record)
        if names is None:
            return
        owned['advertisedCommands'] = names
        with self.store.edit() as state:
            if self.valid(state, generation, pending):
                for item in state['owned']:
                    if item['name'] == owned['name']:
                        item['advertisedCommands'] = names

    def activate(self, model, access, effort=None, agent=None, require_hooks=False, expected_pending=None):
        if require_hooks:
            host_access = frontends.access_readiness()
            if not host_access['ready']:
                raise RuntimeError(host_access['message'])
        target = self.use(agent or self.agent_of(self.store.read())).ID
        settings = self.adapter.selection(self.store.root, model, access, effort)
        with self.store.edit() as state:
            if expected_pending is not None and state.get('pending') != expected_pending:
                raise RuntimeError('Menu changed before activation; no settings were applied.')
            if require_hooks:
                routing = frontends.routing_readiness(state)
                if not routing['ready']:
                    raise RuntimeError(routing['message'])
            if not state['pending'] or state['pending']['stage'] != 'menu':
                raise RuntimeError('Frontend selection is required before activation.')
            if state['pending'].get('phase') == 'agent':
                raise RuntimeError('Choose an agent before activating.')
            if require_hooks and not frontends.confirmed(self.store.root, target):
                raise RuntimeError('Run First Time User Check before activation; install missing prerequisites separately.')
            # Tuning changes the agent it opened on; the activation page, bind and spawn start a new one.
            session = state['pending'].get('session')
            old = agent_entry(state, session) if session else None
            if session and old is None:
                raise RuntimeError('That agent was closed; no settings were applied.')
            if old is not None and pending_work(state, session=session):
                raise RuntimeError('Settle current work before changing settings.')
            if ((not state['active'] and state['owned']) or any(
                    not item['ready'] or item['role'] != 'main' for item in state['owned'])):
                raise RuntimeError('Unfinished session cleanup remains. Run off successfully before activating.')
            if old is None and len(state['owned']) >= agent_limit(state):
                raise RuntimeError(str(len(state['owned'])) + ' agents are running, the limit. Close one first '
                                   '(/cli close <name>), or raise the limit with /cli agents max <n>.')
            name = state['pending'].get('name')
            if old is None and name and any((item.get('alias') or '').casefold() == name.casefold()
                                            for item in state['owned']):
                raise RuntimeError('An agent named ' + name + ' is already running.')
            activation_id = state['pending']['id']
            origin_route = deepcopy(state.get('turnRoute'))
            completed_control = state['pending'].get('tuning') or (origin_route or {}).get('route') == 'bind'
            state['pending']['stage'] = 'verifying'
            generation = state['generation']
            if old is not None and old.get('backend') not in (None, target):
                raise RuntimeError('This agent is ' + str(old.get('backend')) + '; start a ' + target +
                                   ' agent with /cli spawn instead.')
            reuse = old is not None
            owned = deepcopy(old) if reuse else dict(name='cli-mode-' + self.store.key[:12] + '-' + uuid.uuid4().hex[:12],
                    workspace=self.store.workspace, backend=target, role='main', settings=settings, ready=False)
            if not reuse:
                if any(item['name'] == owned['name'] for item in state['owned']):
                    owned['name'] += '-' + str(len(state['owned']))  # Each agent's session name is its own.
                if not name:
                    taken = list(state.get('usedNames') or []) + [item.get('alias') for item in state['owned']]
                    name = names.generate(target, owned['name'], [alias for alias in taken if alias])
                    state['usedNames'] = (state.get('usedNames') or []) + [name]  # Never given out again.
                owned['alias'] = name
            if not reuse and getattr(self.backend, 'profile', None):
                owned['acpxProfile'] = self.backend.profile
            if hasattr(self.backend, 'prepare'):
                self.backend.prepare(owned)
            if reuse:
                # Reconfigure the same session. Partial failure must gate dispatch to it,
                # since the provider may have accepted only some of the settings.
                owned.update(settings=settings, ready=False)
                state['owned'] = [owned if item['name'] == owned['name'] else item for item in state['owned']]
                state['active'] = any(item['ready'] for item in state['owned'])
            else:
                state['owned'].append(owned)
        # Activation goes ahead: when this process shows the confirmation, its usage lookup (a local query
        # taking up to 40 s for some agents) runs alongside the provider work instead of after it.
        self.prefetched = (self.prefetch_usage(target, model, access, effort)
                           if getattr(self, 'prefetching', False) else None)
        try:
            if owned.get('transport') == 'native':
                owned['nativeModel'] = native_agy.prepare(settings)
                provider = owned.get('providerSession')  # Antigravity native transport only.
            else:
                provider = self.provision(owned, generation, activation_id)
            with self.store.edit() as state:
                if not self.valid(state, generation, activation_id):
                    raise RuntimeError('Activation canceled; routing remains off.')
                for item in state['owned']:
                    if item['name'] == owned['name']:
                        item.update(ready=True, providerSession=provider, lastUsedAt=time.time())
                        if owned.get('transport') == 'native':
                            item['nativeModel'] = owned['nativeModel']
                state.update(active=True, pending=None)
                if not reuse or owned['name'] == state['main'] or not state['main']:
                    # A new agent becomes the current one; changing another agent's settings leaves it be.
                    state.update(main=owned['name'], settings=settings, backend=target)
                # A new user prompt owns its own route, even if its command is identical.
                if (completed_control and origin_route and state.get('turnRoute') == origin_route
                        and origin_route['route'] in ('bind', 'tune', 'setup')):
                    state['turnRoute']['route'] = 'control-result'
            return dict(self.store.read(), activated=owned['name'])
        except BaseException:
            if not reuse:
                self.cleanup(owned)
            with self.store.edit() as state:
                if (state['pending'] or {}).get('id') == activation_id:
                    state['pending']['stage'] = 'menu'
            raise
