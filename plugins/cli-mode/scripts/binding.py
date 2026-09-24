"""Owned-session lifecycle: activation, controls, readiness and shutdown."""
from copy import deepcopy
import os
import time
import uuid

import frontends
import installer
import native_agy
import native_commands
from operations import operation_running, pending_work


class OwnerNotRunning(RuntimeError):
    """A setting refused before sending it: no running ACPX owner holds the session. Nothing reached the agent."""


class BindingMixin:
    def bind(self, agent='agy', require_hooks=True, message_output=None, confirm=False):
        # An explicit bind authorizes defaults, but never skips readiness gates.
        # `confirm` asks for the confirmation as text when there is no view path.
        adapter = self.use(agent)
        state = self.frontend(agent)
        if not frontends.confirmed(self.store.root, agent):
            result = self.first_time_check(agent)
            if not result['confirmed']:
                raise RuntimeError(result['message'])
        # Saved defaults belong to this backend only; another backend's model
        # and effort are never carried across.
        saved = (state.get('settings') or {}) if state.get('backend') in (None, agent) else {}
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

    def disable(self):
        # Admission stops before any slow ACPX operation. Late completions cannot reactivate.
        with self.store.edit() as state:
            self.store.discard_captured(state)
            self.store.signal_cancel(state)
            state.pop('runner', None)
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

    def config_cache(self):
        """Where the bridge may keep `acpx config show` output between processes."""
        folder = self.store.root / 'cache'
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / ('acpx-config-' + self.store.key[:16] + '.json'))

    SHUTDOWN_SETTLE = 10.0

    def settle_shutdown(self):
        """Give submitters of closed sessions a moment to unwind, so one `off`
        reports a settled shutdown instead of the host sleeping and retrying."""
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
                    unwinding |= alive
                latest['inflight'] = kept
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
            if pending_work(state):
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
            if pending_work(state):
                raise RuntimeError('Settle current work before changing settings.')
            if ((not state['active'] and state['owned']) or len(state['owned']) > 1 or any(
                    not item['ready'] or item['role'] != 'main' or item['name'] != state['main']
                    for item in state['owned'])):
                raise RuntimeError('Unfinished session cleanup remains. Run off successfully before activating.')
            activation_id = state['pending']['id']
            origin_route = deepcopy(state.get('turnRoute'))
            completed_control = state['pending'].get('tuning') or (origin_route or {}).get('route') == 'bind'
            state['pending']['stage'] = 'verifying'
            generation = state['generation']
            old = next((x for x in state['owned'] if x['name'] == state['main']), None)
            if old is not None and old.get('backend') not in (None, target):
                raise RuntimeError('This conversation owns a ' + str(old.get('backend')) +
                                   ' session. Run off successfully before activating another backend.')
            reuse = old is not None
            owned = deepcopy(old) if reuse else dict(name='cli-mode-' + self.store.key[:12] + '-' + uuid.uuid4().hex[:12],
                    workspace=self.store.workspace, backend=target, role='main', settings=settings, ready=False)
            if not reuse and getattr(self.backend, 'profile', None):
                owned['acpxProfile'] = self.backend.profile
            if hasattr(self.backend, 'prepare'):
                self.backend.prepare(owned)
            if reuse:
                # Reconfigure the same session. Partial failure must gate dispatch,
                # since the provider may have accepted only some of the settings.
                owned.update(settings=settings, ready=False)
                state['owned'] = [owned]
                state['active'] = False
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
                        item.update(ready=True, providerSession=provider)
                        if owned.get('transport') == 'native':
                            item['nativeModel'] = owned['nativeModel']
                state.update(active=True, pending=None, main=owned['name'], settings=settings, backend=target)
                # A new user prompt owns its own route, even if its command is identical.
                if (completed_control and origin_route and state.get('turnRoute') == origin_route
                        and origin_route['route'] in ('bind', 'tune', 'setup')):
                    state['turnRoute']['route'] = 'control-result'
            return self.store.read()
        except BaseException:
            if not reuse:
                self.cleanup(owned)
            with self.store.edit() as state:
                if (state['pending'] or {}).get('id') == activation_id:
                    state['pending']['stage'] = 'menu'
            raise
