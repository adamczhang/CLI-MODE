"""Setup, activation and settings menus. No provider prompts are sent here."""
from copy import deepcopy
import uuid

import adapters
import frontends
import host
import installer
import menu_view
from operations import pending_work
from presentation import menu_block
from progress import PROGRESS_MODES, progress_mode
from state import ROUTING_MODES, routing_mode
import viewer


class MenuMixin:
    def saved_settings(self, state, agent):
        """Accepted settings belong to the backend that accepted them.

        Showing them on another backend's page would preload a model, effort or
        access this runtime never advertised, and its menu may not even be able
        to render them.
        """
        if agent == 'home' or state.get('backend') not in (None, agent):
            return None
        return state.get('settings')

    def prefetch_usage(self, agent, model, access, effort=None):
        """Start the native usage lookup now, so it overlaps activation.

        The lookup is a local CLI query, never a model prompt. Its result is
        used only if activation accepts the same backend and model.
        """
        import concurrent.futures
        import threading
        import confirmation
        try:
            model = adapters.module(agent).selection(self.store.root, model, access, effort)['model']
        except (OSError, ValueError, KeyError):
            return None
        future = concurrent.futures.Future()

        def lookup():
            try:
                future.set_result(confirmation.usage(agent, {'model': model}, self.store.workspace))
            except BaseException as exc:
                future.set_exception(exc)
        # A daemon thread: an activation that fails at once exits at once, without waiting for the lookup.
        threading.Thread(target=lookup, name='cli-mode-usage', daemon=True).start()
        return agent, model, future

    def activation_message(self, destination, prefetched=None):
        import confirmation
        state = self.store.read()
        owned = next((item for item in state['owned'] if item['name'] == state['main']), None)
        if not state['active'] or state.get('pending') or not owned or not owned.get('ready'):
            raise RuntimeError('No verified active agent to confirm.')
        agent, settings = state['backend'], state['settings']
        prefetched, self.prefetched = prefetched or getattr(self, 'prefetched', None), None
        if prefetched and prefetched[:2] == (agent, settings['model']):
            summary = prefetched[2].result()
        else:
            summary = confirmation.usage(agent, settings, self.store.workspace)
        latest = self.store.read()
        if any(latest.get(key) != state.get(key) for key in ('active', 'generation', 'main', 'backend', 'settings', 'pending')):
            raise RuntimeError('Agent changed during usage lookup; activation confirmation was not rendered.')
        # Chat text (no view path) on Claude Code: its title in dark green, unless /cli color off.
        color = destination is None and host.chat_color(self.store.root)
        text = confirmation.activation(agent, settings, summary, color=color)
        if destination is None:
            return dict(text=text, usage=summary)  # Text hosts show the confirmation as chat text.
        return dict(text=text, usage=summary, messageView=dict(
            path=menu_view.write_message(text, destination, adapters.module(agent).LABEL, 'activation'),
            format='inline-html', kind='activation', label=adapters.module(agent).LABEL))

    def agent_of(self, state, fallback=None):
        """The backend that owns this conversation's current operation."""
        pending = state.get('pending') or {}
        entry = pending.get('backend') or state.get('backend')
        if entry is None and pending.get('entrypoint') not in (None, 'home'):
            entry = pending['entrypoint']
        return entry or fallback or self.agent

    def settings_menu(self, dismiss=False):
        """The active agent's settings page; no provider calls or activation."""
        with self.store.edit() as state:
            if not state['active']:
                raise RuntimeError('Activate a CLI before opening its settings.')
            if pending_work(state):
                raise RuntimeError('Settle current work before changing settings.')
            if (state.get('pending') or {}).get('stage') == 'verifying':
                raise RuntimeError('Settings are being verified; wait before opening the menu.')
            if dismiss:
                if (state.get('pending') or {}).get('phase') != 'settings' and not (state.get('pending') or {}).get('tuning'):
                    raise RuntimeError('The agent settings menu is not open.')
                state['pending'] = None
                state['turnRoute'] = dict(route='settings-result')
            else:
                state['pending'] = dict(id=uuid.uuid4().hex, stage='menu', phase='settings',
                    backend=state['backend'], entrypoint=state['backend'], draft={})
                state['turnRoute'] = dict(route='settings')
            state['modeMenu'] = False
        if dismiss:
            return dict(state, message='Settings closed. CLI remains active.')
        return dict(state, activationMenu=frontends.active_settings_menu(
            state['backend'], state['settings'], routing_mode(state), progress_mode(state)))

    def progress(self, choice=None):
        """A host display preference; never reconfigure or prompt the provider."""
        if choice is not None and choice not in PROGRESS_MODES:
            raise ValueError('Choose activity or quiet.')
        with self.store.edit() as state:
            if choice is not None:
                state['progressMode'] = choice
            state['turnRoute'] = dict(route='progress-result')
        result = dict(state, message='Progress: ' + progress_mode(state).title() +
            '. Use /cli progress activity for tool activity and usage, or /cli progress quiet for messages and plans. Changes apply to the next turn.')
        if (state.get('pending') or {}).get('phase') == 'settings':
            result['activationMenu'] = frontends.active_settings_menu(
                state['backend'], state['settings'], routing_mode(state), progress_mode(state))
        return result

    def view(self, choice=None):
        """/cli view on|off: the read-only agent viewer window, saved for every session."""
        if choice is not None and choice not in viewer.CHOICES:
            raise ValueError('Choose on or off.')
        with self.store.edit() as state:
            state['turnRoute'] = dict(route='view-result')
        if choice is not None:
            viewer.save(self.store.root, choice)
        if not viewer.enabled(self.store.root):
            message = 'Agent viewer: Off. Use /cli view on to watch each turn in a PowerShell window.'
            return dict(state, view='off', message=message if choice is None else
                        'Agent viewer: Off. An open viewer window closes itself.')
        label = self.adapter.LABEL if state.get('backend') else 'Agent'
        opened = viewer.launch(self.store, label)
        message = {'opened': 'Agent viewer: On. A PowerShell window now shows each turn as it runs.',
                   'running': 'Agent viewer: On. Its window is already open.',
                   'unsupported': 'Agent viewer: On. Its window opens only on Windows.'}[opened]
        return dict(state, view='on', message=message + ' Closing the window is safe; the next turn reopens it. '
                    'Use /cli view off to stop.')

    def mode(self, choice=None, dismiss=False):
        """Change host routing only; never start, reconfigure or close the CLI."""
        if choice is not None:
            choice = choice.casefold()
            if choice not in ROUTING_MODES:
                raise ValueError('Choose Passthrough or Direct.')
        with self.store.edit() as state:
            if dismiss:
                state['modeMenu'] = False
                state['turnRoute'] = dict(route='mode-result')
            elif choice is None:
                state['modeMenu'] = True
                state['turnRoute'] = dict(route='mode-menu')
            else:
                state['routingMode'] = choice
                state['modeMenu'] = False
                state['turnRoute'] = dict(route='mode-result')
        pending = state.get('pending') or {}
        if (dismiss or choice is not None) and pending.get('phase') == 'settings':
            return dict(state, activationMenu=frontends.active_settings_menu(
                state['backend'], state['settings'], routing_mode(state), progress_mode(state)))
        if (dismiss or choice is not None) and pending.get('phase') == 'activation' and not pending.get('onboarding'):
            agent = pending.get('backend') or pending['entrypoint']
            return dict(state, activationMenu=frontends.menu(self.store.root, agent,
                self.saved_settings(state, agent), frontends.routing_readiness(state),
                frontends.access_readiness(), routing_mode=routing_mode(state)))
        if dismiss:
            return dict(state, message='Routing mode menu closed. CLI activation and settings are unchanged.')
        if choice is None:
            return dict(state, activationMenu=frontends.routing_mode_menu(routing_mode(state)))
        return dict(state, message='CLI-MODE routing: ' + choice.title() + '. ' + (
            'Only prompts starting with /d or $d go to the active CLI; other prompts stay with ' + host.name() + '.'
            if choice == 'direct' else 'Ordinary prompts go to the active CLI.'))

    def frontend(self, agent='agy', page=1):
        if agent != 'home':
            self.use(agent)
        current = self.store.read()
        if (current.get('pending') or {}).get('onboarding') == 'installing':
            return self.setup_status()
        routing = frontends.routing_readiness(current)
        access = frontends.access_readiness()
        menu = frontends.menu(self.store.root, agent, self.saved_settings(current, agent), routing, access, page,
                             routing_mode=routing_mode(current))
        if not access['ready']:
            # Display onboarding without writing outside the workspace or asking to escalate.
            return dict(current, activationMenu=menu, routingReadiness=routing, hostAccess=access)
        with self.store.edit() as state:
            if pending_work(state):
                raise RuntimeError('Settle current work before changing settings.')
            if (state.get('pending') or {}).get('stage') == 'verifying':
                raise RuntimeError('Activation is already being verified; cancel it with off first.')
            state['pending'] = dict(id=uuid.uuid4().hex, stage='menu',
                                    phase='agent' if agent == 'home' else 'activation', entrypoint=agent,
                                    backend=None if agent == 'home' else agent, draft={}, page=page,
                                    choices=[{'label': b['displayName'], 'value': b['id']} for b in frontends.backends()] if agent == 'home' else [])
            if frontends.first_start(self.store.root, agent, routing):
                state['pending']['onboarding'] = 'select-agent'
        return dict(state, activationMenu=menu, routingReadiness=routing, hostAccess=access)

    def first_time_check(self, agent='agy', check=None):
        self.use(agent)
        if (self.store.read().get('pending') or {}).get('onboarding') == 'installing':
            return self.setup_status()
        access = frontends.access_readiness()
        if not access['ready']:
            routing = frontends.routing_readiness(self.store.read())
            return dict(confirmed=False, checks=[], setupReady=False, hostAccess=access,
                        routingReadiness=routing, message=access['message'],
                        activationMenu=frontends.setup_menu(dict(confirmed=False, checks=[], backend=agent), routing, access))
        if (self.store.read().get('pending') or {}).get('stage') == 'verifying':
            raise RuntimeError('Activation is already being verified; wait before checking setup.')
        result = frontends.check_and_save(self.store.root, agent, check)
        with self.store.edit() as state:
            routing = frontends.routing_readiness(state)
            ready = result['confirmed'] and routing['ready']
            pending = state.get('pending')
            if pending and pending['stage'] == 'menu' and pending.get('onboarding'):
                pending.update(entrypoint=agent, backend=agent, phase='activation')
                if ready:
                    pending.pop('onboarding')
                else:
                    pending['onboarding'] = 'check'
            elif pending and pending['stage'] == 'menu' and pending.get('phase') == 'agent':
                # A returning user chose an agent that needed setup from Select CLI Agent:
                # continue on that agent's page, not the agent list.
                pending.update(entrypoint=agent, backend=agent, phase='activation', choices=[])
                if not ready:
                    pending['onboarding'] = 'check'
        target = (state['pending'] or {}).get('entrypoint', agent)
        menu = (frontends.menu(self.store.root, target, self.saved_settings(state, target), routing,
                              routing_mode=routing_mode(state)) if ready
                else frontends.setup_menu(result, routing, access))
        return dict(result, setupReady=ready, routingReadiness=routing, hostAccess=access, activationMenu=menu)

    def setup_start(self, approved=False, agent=None):
        if not approved:
            raise ValueError('Ask permission to install the listed missing components and guide sign-in first.')
        if not frontends.access_readiness()['ready']:
            raise RuntimeError('Full Access is required for installation.')
        with self.store.edit() as state:
            if state['active'] or state['owned'] or state['inflight']:
                raise RuntimeError('Stop active agent work before installation.')
            target = self.use(agent or self.agent_of(state, 'agy')).ID
            result = installer.call('Start', approved=True, backend=target)
            state['pending'] = dict(id=uuid.uuid4().hex, stage='menu', phase='activation',
                entrypoint=target, backend=target, onboarding='installing',
                installerRun=result['runId'], draft={})
        return result

    def setup_status(self):
        pending = self.store.read().get('pending') or {}
        if not pending.get('installerRun'):
            raise RuntimeError('No installer belongs to this ' + ('session' if host.claude() else 'task') +
                               '. Scan setup again.')
        target = pending.get('backend') or pending.get('entrypoint') or self.agent
        result = installer.call('Status', run_id=pending['installerRun'], backend=target)
        if result['status'] == 'complete':
            installer.refresh_paths()
            # Do not resurrect a canceled or replaced menu after a slow status call.
            with self.store.edit() as state:
                if (state.get('pending') or {}).get('id') != pending['id']:
                    return dict(result, resumed=False)
                state['pending']['onboarding'] = 'check'
            return dict(result, setup=self.first_time_check(target))
        if result['status'] in ('failed', 'interrupted', 'canceled'):
            result['activationMenu'] = menu_block('CLI-MODE\nSetup CLI Agent\n\nSetup '+result['status']+
                '\nI. Retry (asks permission)\nM. Install manually/separately\nR. Recheck setup\nAfter manual install, rerun /cli.')
            with self.store.edit() as state:
                if (state.get('pending') or {}).get('id') == pending['id']:
                    state['pending']['onboarding'] = 'check'
        return result

    def tune(self, phase):
        if phase not in ('model', 'effort', 'access'):
            raise ValueError('Unknown tuning phase.')
        self.use(self.agent_of(self.store.read()))
        snapshot = self.adapter.catalog(self.store.root)
        with self.store.edit() as state:
            if not state['active'] or not state['main']:
                raise RuntimeError('No bound agent. /cli to activate. Say ' +
                                   ('/cli help' if host.claude() else '/help') + ' to see options.')
            if pending_work(state) or (state.get('pending') or {}).get('stage') == 'verifying':
                raise RuntimeError('Settle current work before changing settings.')
            state['pending'] = dict(id=uuid.uuid4().hex, stage='menu', phase=phase,
                entrypoint=self.agent, backend=self.agent, tuning=True,
                draft=dict(settings=deepcopy(state['settings']), snapshot=snapshot))
            # Only after admission may compaction restore a saved setup phase.
            if state.get('turnRoute', {}).get('route') == 'tune':
                state['turnRoute']['route'] = 'setup'
        return state['pending']

    def tune_choice(self, phase):
        """Apply `/cli model|effort|access <text>` against the advertised options.

        The hook saved the typed text; the controller matches it, so the host
        never interprets a catalog. A unique match is applied to the same
        session; otherwise the phase menu is returned for a numbered reply.
        """
        choice = ((self.store.read().get('turnRoute') or {}).get('choice') or '').strip()
        pending = self.tune(phase)
        draft = pending['draft']
        options = frontends.phase_options(self.store.root, self.agent, phase, draft['settings'], draft['snapshot'])
        matches = frontends.match_choice(options, choice, phase) if choice else []
        if len(matches) != 1:
            result = self.options(phase)
            if choice:
                result['message'] = ('Several ' + phase + ' options match "' + choice + '"; choose one by number.'
                                     if matches else 'No advertised ' + phase + ' matches "' + choice +
                                     '"; choose one by number.')
            return result
        value = matches[0]
        settings = draft['settings']
        model, access, effort = settings['model'], settings['access'], settings.get('effortValue')
        if phase == 'access':
            access = value
        elif phase == 'model':
            model = value
        elif adapters.descriptor(self.agent)['effortRepresentation'] == 'combined':
            model = value  # The effort is part of the model ID (Antigravity).
        else:
            effort = value
        try:
            return self.activate(model, access, effort=effort, agent=self.agent)
        except ValueError:
            if phase != 'model':
                raise
            # The new model does not offer the current effort: use its default.
            return self.activate(model, access, effort=None, agent=self.agent)

    def draft(self, phase, choices, expected=None):
        with self.store.edit() as state:
            if not state['pending'] or state['pending']['stage'] != 'menu':
                raise RuntimeError('Open the frontend before updating menu choices.')
            if expected is not None and state['pending'] != expected:
                raise RuntimeError('Menu changed before selection; reopen the current page.')
            previous = state['pending'].get('draft') or {}
            merged = dict(previous, **choices)
            merged['settings'] = dict(previous.get('settings') or {}, **(choices.get('settings') or {}))
            state['pending'].update(phase=phase, draft=merged)
        return state['pending']

    def options(self, phase, agent=None, page=1):
        """Render and persist one transaction's exact choices and catalog."""
        with self.store.edit() as state:
            target = agent or self.agent_of(state)
            adapter = adapters.module(target)
            pending = state.get('pending') or {}
            if pending and (pending.get('backend') != target or pending.get('stage') != 'menu'):
                raise RuntimeError('Open this backend menu before choosing settings.')
            draft = pending.get('draft') or {}
            settings = dict(self.saved_settings(state, target) or adapter.selection(self.store.root, **adapter.DEFAULTS), **(draft.get('settings') or {}))
            snapshot = draft.get('snapshot') or adapter.catalog(self.store.root)
            result = frontends.phase_menu(self.store.root, target, phase, settings, page,
                                          snapshot, pending.get('tuning', False))
            if pending:
                pending.update(phase=phase, page=result['page'], choices=result['choices'])
                pending['draft'] = dict(draft, snapshot=snapshot, settings=settings)
        result['activationMenu'] = result.pop('menu')
        return result

    def navigate(self, action):
        state = self.store.read()
        pending = state.get('pending') or {}
        if pending.get('stage') != 'menu':
            raise RuntimeError('No navigable menu is open.')
        phase = pending.get('phase')
        if action == 'r':
            if phase != 'model':
                raise RuntimeError('Refresh is available on the model page.')
            return self.refresh()
        if action == 'b':
            if pending.get('tuning'):
                return self.settings_menu()
            previous = {'access': 'effort', 'effort': 'model'}.get(phase)
            if previous:
                return self.options(previous)
            return self.frontend(pending['backend'] if phase == 'model' else 'home')
        if action not in ('>', '<'):
            raise ValueError('Unknown navigation action.')
        page = max(1, pending.get('page', 1) + (1 if action == '>' else -1))
        if phase == 'agent':
            return self.frontend('home', page)
        if phase not in ('model', 'effort', 'access'):
            raise RuntimeError('This menu has no additional pages.')
        return self.options(phase, page=page)

    def choose(self, number, require_hooks=False):
        """Apply a numbered choice against the displayed transaction, never a newer catalog."""
        state = self.store.read()
        pending = state.get('pending') or {}
        choices = pending.get('choices') or []
        if pending.get('stage') != 'menu' or not 1 <= number <= len(choices):
            raise ValueError('Choose a number from the displayed menu.')
        choice = choices[number - 1]['value']
        phase = pending['phase']
        if phase == 'agent':
            if pending.get('onboarding') or not frontends.confirmed(self.store.root, choice):
                return self.first_time_check(choice)
            return self.frontend(choice)
        if phase not in ('model', 'effort', 'access'):
            raise ValueError('This page does not have a settings choice list.')
        draft = deepcopy(pending.get('draft') or {})
        settings = draft.setdefault('settings', {})
        if phase == 'model':
            settings['model'] = choice
            settings['effortValue'] = None
            self.draft('effort', draft, expected=pending)
            return self.options('effort')
        if phase == 'effort':
            descriptor = adapters.descriptor(pending['backend'])
            if descriptor['effortRepresentation'] == 'combined':
                settings['model'] = choice
            else:
                settings['effortValue'] = choice
            self.draft('access', draft, expected=pending)
            return self.options('access')
        settings['access'] = choice
        applied = self.draft('access', draft, expected=pending)
        adapter = adapters.module(pending['backend'])
        return self.activate(settings.get('model', adapter.DEFAULTS['model']), choice,
                             effort=settings.get('effortValue'), agent=pending['backend'],
                             require_hooks=require_hooks, expected_pending=applied)

    def refresh(self):
        """Refresh advertised metadata without launching or reconfiguring a provider."""
        import catalogs
        state = self.store.read()
        pending = state.get('pending') or {}
        if pending.get('stage') != 'menu' or pending.get('phase') != 'model':
            raise RuntimeError('Open the model menu before refreshing.')
        if pending_work(state):
            raise RuntimeError('Wait for the running operation before refreshing.')
        target = self.agent_of(state)
        self.use(target)
        owned = next((item for item in state['owned'] if item['name'] == state['main']
                      and item['backend'] == target), None)
        if not owned or owned.get('transport') == 'native':
            result = self.options('model')
            result.update(refreshed=False, catalogStatus='cached',
                          message='Cached choices retained. Metadata refresh requires this agent’s owned ACP session; no extra session was opened.')
            return result
        try:
            data = catalogs.from_metadata(target, self.adapter.catalog(self.store.root), self.backend.metadata(owned))
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            result = self.options('model')
            result.update(refreshed=False, catalogStatus='cached', message='Refresh failed; cached choices retained. ' + str(exc))
            return result
        with self.store.edit() as latest:
            if latest['generation'] != state['generation'] or latest.get('pending') != pending or pending_work(latest):
                raise RuntimeError('Menu changed during refresh; catalog was not replaced.')
            catalogs.save(self.store.root, target, data)
            # Reset draft choices explicitly. Accepted runtime settings remain untouched.
            latest['pending']['draft'] = dict(snapshot=data, settings={})
        result = self.options('model')
        result.update(refreshed=True, catalogStatus='session-metadata', message=data['source'])
        return result
