"""Local hook: restore routing and start a detached provider queue worker."""
import json
from pathlib import Path
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from state import Store, route
from presentation import HOST_NOTE_RULE

PLUGIN = Path(__file__).resolve().parents[1]


SUBAGENT_TOOLS = re.compile(r'(?:functions\.)?(?:collaboration\.)?(?:spawn_agent|followup_task|send_message|Agent)')


def delegated_turn(state):
    """True while this turn belongs to the agent, so host subagents stay off."""
    return state['active'] and state.get('turnRoute', {}).get('route') in ('direct', 'direct-result')


def task_through_settings(state, prompt):
    """True for a Direct task (/d with text) typed while an agent is ready and a menu is open (Agent Settings, or
    another agent's activation page): the task is not a menu reply. A running installer keeps its menu."""
    from state import direct_payload
    pending = state.get('pending') or {}
    return bool(state.get('active') and pending.get('stage') == 'menu' and pending.get('onboarding') != 'installing'
                and (direct_payload(prompt) or '').strip())


def activation_reply(state, prompt):
    """(agent, answer) for a 1/Yes or 2 reply to an agent's activation menu, else None."""
    pending = state.get('pending') or {}
    agent = pending.get('backend') or pending.get('entrypoint')
    answer = prompt.strip().casefold()
    answer = '1' if answer in ('1', 'yes', 'y') else answer
    if (pending.get('phase') == 'activation' and pending.get('stage') == 'menu' and not pending.get('onboarding')
            and agent and agent != 'home' and answer in ('1', '2')):
        return agent, answer
    return None


def attached(event):
    """The prompt event with a /d's attached files taken off its text into `attachments` (host.split_attachments).

    Only a /d changes: any other prompt with files attached is the host's, exactly as it arrived.
    """
    import host
    from state import direct_payload
    typed, files = host.split_attachments(event.get('prompt') or '')
    if not files or direct_payload(typed) is None:
        return event
    return dict(event, prompt=typed, attachments=files)


def attach_files(store, state, request_ids, files):
    """Copy a /d's attached files into the working folder of each agent it went to, and name them on its request.

    A file that is gone or too large is left out; the task names only the copies the agent can open.
    """
    import agent_folder
    from state import agent_entry
    for request_id in request_ids:
        record = state['requests'][request_id]
        entry = agent_entry(state, record['session']) or {}
        copied, _ = agent_folder.attach(entry.get('workspace') or store.workspace, entry.get('alias'), files)
        if copied:
            record['attachments'] = copied


def answered(state, request_ids, approval):
    """A new /d to an agent settles the permission question its stopped turn asked; `approval` is the answer
    (`/cli approve` allows that kind of request on this turn, `always` on every turn from now on)."""
    from state import agent_entry
    for request_id in request_ids:
        record = state['requests'][request_id]
        entry = agent_entry(state, record['session']) or {}
        entry.pop('approval', None)
        if approval and approval['answer'] == 'approve' and approval.get('rule'):
            record['approve'] = [approval['rule']]
            if approval.get('always') and approval['rule'] not in entry.setdefault('approveAlways', []):
                entry['approveAlways'].append(approval['rule'])


def handle(event, root=None):
    event = attached(event) if event['hook_event_name'] == 'UserPromptSubmit' else event
    name = event['hook_event_name']
    if name == 'PreToolUse':
        state = Store(event['session_id'], event['cwd'], root).read()
        tool = event.get('tool_name', '')
        if delegated_turn(state) and SUBAGENT_TOOLS.fullmatch(tool):
            return {'hookSpecificOutput': dict(hookEventName=name, permissionDecision='deny',
                permissionDecisionReason='This CLI-MODE turn is delegated. Pass its payload to the one main provider session; that provider owns internal delegation. Native Codex subagents remain disabled for delegated turns.')}
        return {}
    if name not in ('SessionStart', 'UserPromptSubmit'):
        return {}
    store, state, decision, worker, cancellation = decide(event, root)
    if name == 'UserPromptSubmit' and task_through_settings(state, event.get('prompt', '')):
        # An explicit /d task typed while Agent Settings is open is a task, not a menu reply: the menu closes
        # and the task goes to the agent (as on Claude Code).
        with store.edit() as saved:
            saved['pending'] = None
        store, state, decision, worker, cancellation = decide(event, root)
    return codex_output(event, store, state, decision, worker, cancellation)


def decide(event, root=None, workspace=None, capture=None):
    """The part of a prompt or session start both hosts share.

    It records the turn's route, captures a delegated message for the queue,
    and does what the hook itself must (cancel, resume monitoring, disable
    routing, start the worker). It returns what each host needs to reply.
    `workspace` and `capture` let a host name its own workspace and the exact
    text to forward; routing always reads the prompt as typed.
    """
    name = event['hook_event_name']
    store = Store(event['session_id'], workspace or event['cwd'], root)
    decision = {'route': 'restore'}
    worker = None
    cancellation = None
    with store.edit() as state:
        if name == 'UserPromptSubmit':
            decision = route(event.get('prompt', ''), state)
            if decision['route'] == 'help':
                state['helpMenu'] = 'commands'
            elif decision['route'] != 'help-invalid':
                state['helpMenu'] = None
            if decision['route'] != 'close-menu':
                state.pop('closeMenu', None)  # The close chooser takes only the next reply.
        state['hookSeen'] = dict(event=name, time=time.time(), plugin=str(PLUGIN), data=str(store.root))
        if name == 'UserPromptSubmit':
            # Remember the route, never user prose. Compaction may happen while a control is being handled.
            state['turnRoute'] = {'route': decision['route'], 'id': uuid.uuid4().hex}
            if decision['route'] == 'direct':
                request_id = state['turnRoute']['id']
                # One request per agent the prompt names (commas between them), each in its agent's own queue.
                targets = decision.get('targets') or [dict(session=decision.get('session'))]
                request_ids = [request_id] + [uuid.uuid4().hex for _ in targets[1:]]
                text = event.get('prompt', '') if capture is None else capture
                approval = decision.get('approval')
                if approval:
                    text = approval['text']  # /cli approve or deny: the agent's next /d says so and goes on.
                try:
                    from state import MAX_QUEUED_REQUESTS
                    queued = sum(record['status'] == 'captured' for record in (state.get('requests') or {}).values())
                    if queued + len(targets) > MAX_QUEUED_REQUESTS:
                        raise RuntimeError('CLI-MODE queue is full (32 messages); wait for earlier work to finish.')
                    for each, target in zip(request_ids, targets):
                        store.capture(state, each, text, session=target.get('session'),
                                      named=decision.get('named', False))
                except RuntimeError as exc:
                    decision = {'route': 'hint', 'text': str(exc)}
                    state['turnRoute'] = {'route': 'hint', 'id': request_id, 'text': str(exc)}
                else:
                    state['turnRoute']['requestId'] = request_id
                    decision['requestId'] = request_id
                    if len(request_ids) > 1:
                        state['turnRoute']['requestIds'] = decision['requestIds'] = request_ids
                    if event.get('attachments'):
                        attach_files(store, state, request_ids, event['attachments'])
                    answered(state, request_ids, approval)
            if decision['route'] == 'hint':
                state['turnRoute']['text'] = decision['text']
            if decision.get('session'):
                state['turnRoute']['session'] = decision['session']  # The agent a named control is for.
            if decision['route'] == 'tune':
                state['turnRoute']['phase'] = decision['phase']
                # The typed setting (not task prose), matched by the controller.
                state['turnRoute']['choice'] = (decision.get('text') or '')[:80]
            if decision['route'] in ('mode', 'progress', 'view'):
                state['turnRoute']['choice'] = decision['choice']
        elif event.get('source') == 'compact':
            decision = state.get('turnRoute', decision)
            if decision['route'] in ('home', 'frontend', 'setup'):
                decision = {'route': 'setup' if state.get('pending') else 'restore'}
            elif decision['route'] == 'help-invalid' and state.get('helpMenu'):
                decision = {'route': 'help'}
    # A compaction restores what the last turn showed; it never repeats a cancel or an off.
    restored = name != 'UserPromptSubmit'
    if decision['route'] == 'cancel' and not restored:
        from controller import Controller
        cancellation = Controller(store).cancel(decision.get('session'))
    if decision['route'] == 'resume':
        from controller import Controller
        worker = Controller(store).resume_monitoring()
    if decision['route'] == 'off' and not restored:
        # Imported here, not at module scope: this hook runs as a fresh process
        # on every message, and only an off turn needs the controller.
        from controller import Controller
        state = Controller(store).disable()
    elif decision['route'] == 'direct' and state['active']:
        from controller import Controller
        control = Controller(store)
        for target in decision.get('targets') or [dict(session=decision.get('session'))]:
            try:
                started = control.ensure_pump(target.get('session'))
            except (OSError, RuntimeError) as exc:  # The request is captured; the next pump sends it.
                started = {'worker': 'start-failed', 'error': str(exc)}
            if worker is None or started.get('worker') in ('blocked', 'start-failed'):
                worker = started
    return store, state, decision, worker, cancellation


def codex_output(event, store, state, decision, worker, cancellation):
    """What Codex reads for this turn: the exact commands to run and the rules for them."""
    name = event['hook_event_name']
    if (not state['active'] and not state.get('pending') and not state.get('helpMenu')
            and decision['route'] in ('host', 'restore')
            and not (name == 'SessionStart' and event.get('source') != 'compact')):
        # Ordinary Codex work with no agent: nothing to inject, and no need to
        # load every adapter on each prompt.
        return {}
    import adapters
    from state import agent_entry, agent_label
    target = agent_entry(state, decision.get('session')) if decision.get('session') else None
    agent = (decision.get('agent') or (target or {}).get('backend') or (state.get('pending') or {}).get('backend')
             or state.get('backend') or 'agy')
    try:
        adapter = adapters.module(agent)
    except ValueError:
        adapter = adapters.module('agy')
    # A running agent is named in every line (`Codex COD-7K`); a kind being set up or started is not, yet.
    label = agent_label(state, decision.get('session')) if state['active'] and not decision.get('agent') else adapter.LABEL
    named = ' --name ' + decision['name'] if decision.get('name') else ""
    if (state['active'] and
            (decision['route'] == 'host' or decision['route'] == 'restore' and not state.get('pending') and not state.get('helpMenu'))):
        return {'hookSpecificOutput': dict(hookEventName=name, additionalContext=
            'CLI-MODE is ON: only a user message starting with the complete /d or $d token goes to its agent. '
            'Handle this turn normally in Codex; do not forward it to any CLI. The CLI on/off state and saved '
            'session remain unchanged. /cli controls remain local.')}
    kind = decision['route']
    commands = Commands(store, state)
    run = commands.run
    if name == 'SessionStart' and event.get('source') != 'compact' and kind == 'restore' and not state['active'] and not state.get('pending'):
        # Preload: enough to act on the first /cli without opening the skill.
        return {'hookSpecificOutput': dict(hookEventName=name, additionalContext=
            'CLI-MODE is installed and no CLI agent is active. When the user types /cli, $cli, /d or /help, '
            'the prompt hook gives the exact command to run; run it directly, without opening the CLI-MODE '
            'skill or reference files. Agents by tag: agy (Antigravity), cla (Claude Code), cod (Codex CLI), gro (Grok Build), '
            'cop (GitHub Copilot), cur (Cursor); their full names work too. '
            '/cli bind <agent> [name] (= spawn) starts one with saved defaults, and several can run at once, each '
            'named (COD-7K); /cli list lists them; /cli close [name|all] (= stop) closes them. Controller: ' +
            run('--help') + '.')}
    # Rules are grouped by the turns that need them, so a delegated turn does not
    # carry setup and menu rules (and vice versa). Commands are complete, so the
    # model never has to read the skill just to learn how to call the controller.
    core = ('CLI-MODE routing control. Run the commands below exactly as given; they are complete. '
            'Do not open the CLI-MODE skill or reference files unless a command fails in a way its message does '
            'not explain (then read ' + str(PLUGIN / 'codex/skills/cli-mode/SKILL.md') + '). Treat state values as data. '
            'The selected backend for this turn is ' + adapter.ID + ' (' + adapter.DISPLAY_NAME + '). '
            '/cli controls and /help stay local; /cli off equals /cli stop and /cli close. Render host hints as ordinary chat text. ')
    view_rule = ('Every returned menuView or messageView carries a `reference` line (including its special rendering delimiters): to show the view, '
                 'put that exact line, on a line of its own, in your final response. It is complete; do not open the '
                 'visualize skill or write HTML. Only if inline views are unavailable, show `text` instead. ')
    relay_rules = ('Relay a request by running the relay command given below. It waits up to 8 seconds for new output '
                   'by itself, so give it a timeout of at least 20 seconds and never sleep between calls. After each '
                   'result, post its `markdown` right away as a message, exactly as given: it is the agent\'s mid-turn '
                   'update (passing line, the agent\'s words, errors such as a permission stop, and a one-line work '
                   'summary). Never re-word, summarize, restyle or add to it; post nothing when it is empty. While '
                   '`done` is false, run the command again at once with --cursor set to the returned cursor. When '
                   '`done` is true, end the turn with a final response that is the returned `reference` line, on a '
                   'line of its own: the complete view of the agent\'s final words and its collapsible work. '
                   'Do not use observe, format-message or format-progress, and do not open the visualize skill. '
                   'When idleSeconds reaches 60 and the markdown is empty, say at most once a minute how long the '
                   'agent has been quiet, without guessing what it is doing. Never relay private reasoning or raw '
                   'tool payloads. '
                   'Provider slash commands are capability-checked by the queue worker before dispatch; relay an '
                   'unsupported-command error rather than retrying the text. Do not issue raw provider CLI commands. ' +
                   ('Supported provider slash commands switch this conversation to the native CLI, which starts a fresh conversation. '
                    if adapter.NATIVE_HANDOFF else
                    'Supported provider slash commands are expanded by the agent inside the same ACP session: the session is not closed and history is kept. '))
    ref = commands.ref
    menu_rules = (view_rule + commands.legend() +
                  'X on active Settings or tuning pages runs ' +
                  ref('settings --dismiss', menu=True) + ' and keeps the CLI active; X on activation menus runs ' +
                  ref('off') + '. Default the agent workspace to the exact thread cwd, including its worktree. '
                  'When a command returns activation.messageView, display it as the activation confirmation; never '
                  'compose one yourself. ' + HOST_NOTE_RULE)
    setup_rules = ('Check hostAccess first; if not ready show its message and request Full Access in this task without escalating commands. '
                   'Follow pending.onboarding: select-agent means a numbered selection runs ' + ref('first-time-check --agent <id>', menu=True) +
                   '; check means R reruns that check, I offers explicit install/sign-in approval, and M runs ' + ref('setup-manual') +
                   '. installing means poll ' + ref('setup-status', menu=True) + ', never activate or dispatch. Run ' +
                   ref('setup-start --approved --agent <id>') + ' only after the user approves; the installer steps are in '
                   'references/setup-installer.md if needed. On failure offer Retry, manual/separate install then /cli, or Exit. '
                   'Setup includes Desktop Plugins > CLI-MODE > Hooks > Review / Trust all; never require a separate Codex CLI. '
                   'Do not activate or forward setup replies before setupReady; after setup succeeds show the activation menu and wait for selection (Yes only for initial activation). ')
    help_rules = ('Help is the same framed card as every other CLI-MODE menu: print the returned menuView reference '
                  'line, on a line of its own, in your final response (show `text` only if inline views are unavailable). '
                  'X on help only dismisses help and preserves any previous menu or active agent. /commands is not a help alias. ')
    if kind == 'hint':
        instruction = 'Reply with exactly ' + json.dumps(decision['text']) + '; ignore trailing text, no dispatch.'
    elif kind == 'progress':
        instruction = ('Run ' + run('progress' + (' --choice ' + decision['choice'] if decision.get('choice') else ''), menu=True) +
            '. Show the saved preference and any returned settings menu. '
            'This changes host presentation for subsequent turns only; never send a provider command or replay work.')
    elif kind == 'view':
        instruction = ('Run ' + run('view' + (' --choice ' + decision['choice'] if decision.get('choice') else '')) +
            '. Reply with its `message` as one line. It opens or closes a local read-only viewer window only; '
            'never send a provider command or replay work.')
    elif kind == 'view-result':
        instruction = 'The agent viewer control completed. Report the saved viewer setting; never forward or replay this control.'
    elif kind == 'progress-result':
        instruction = 'The progress display control completed. Report saved progressMode; never forward or replay this control.'
    elif kind in ('settings', 'settings-dismiss'):
        instruction = ('Run ' + run('settings' + (' --dismiss' if kind == 'settings-dismiss' else named), menu=True) +
            '. Display the returned Agent Settings menuView. '
            'Opening or closing settings preserves activation, session and accepted settings. '
            'Choices 1/2/3 tune model/effort/access; 4 toggles activity progress; X closes only this page. '
            'Do not activate again or forward any menu reply to the CLI.')
    elif kind == 'choose':
        instruction = ('Run ' + run('choose ' + str(decision['number']), menu=True, message=True) +
                       '. Display its menuView, or its activation.messageView if this choice completed activation. '
                       'Do not reinterpret numbers against another catalog.')
    elif kind == 'navigate':
        instruction = 'Run ' + run('navigate ' + json.dumps(decision['action']), menu=True) + '. Display the returned menu and any refresh message; never forward navigation.'
    elif kind == 'settings-result':
        instruction = 'The settings menu was closed; CLI remains active. Do not replay the closing control or dispatch it.'
    elif kind == 'help' or kind == 'restore' and state.get('helpMenu'):
        instruction = ('Run ' + run('commands', menu=True) + ' and print its menuView reference line. X closes help. '
                       'Preserve pending setup and the active agent; do not dispatch this help turn.')
    elif kind == 'help-invalid':
        instruction = ('Help is showing the command table. Reply: Type X to close help, or use a /cli command. '
                       'Do not forward the reply or change the saved help page.')
    elif kind == 'help-dismiss':
        instruction = ('Help closed. The prior menu and agent on/off state are unchanged. '
                       'If a setup or settings menu was open, its displayed choices remain valid. '
                       'Do not run controller off, activate or dispatch an agent task for this X.')
    elif kind == 'bind':
        if (state.get('pending') or {}).get('stage') == 'verifying':
            instruction = 'Binding verification is already running. Inspect its recorded operation and wait for the result; do not bind or dispatch again.'
        else:
            defaults = adapter.DEFAULTS
            instruction = ('Run ' + run('bind --agent ' + adapter.ID + named, message=True) + '. This explicit command authorizes the saved '
                           'defaults of this backend (initially ' + json.dumps(defaults) + '); do not ask for menu confirmation. '
                           'It starts a new agent (other running agents are unchanged), checks prerequisites, sends one '
                           'readiness prompt to the agent and activates, which can take '
                           'a minute: give it a timeout of at least 120 seconds and wait for it to finish rather than polling. '
                           'It returns activation.messageView with the agent\'s name and the accepted model, effort, access '
                           'and usage: show it by its reference line and nothing else. '
                           'Report any setup blocker from its error; never install or sign in automatically.')
    elif kind == 'tune':
        instruction = ('Run ' + run('tune --phase ' + decision['phase'] + ' --apply' + named, menu=True, message=True) +
                       '. The controller matches the text typed after the command against ' + label + "'s advertised "
                       + decision['phase'] + ' options itself and applies a unique match to the same session. If the '
                       'result has activation.messageView, the setting was applied: print its reference line. Otherwise '
                       'print the returned menu\'s reference line and its message, and the user replies with a number. '
                       'Never interpret the catalog yourself, forward control text or invent options.')
    elif kind == 'control-result':
        instruction = ('The binding or tuning control completed. Report the saved accepted settings; do not rerun the command '
                       'or forward it as task text. If no activation view was shown yet, run ' +
                       run('activation-message', message=True) + ' and display it.')
    elif kind == 'off':
        instruction = 'Routing is now OFF. Run ' + run('off') + ' to cancel/close owned sessions and report shutdown accurately.'
    elif kind == 'close':
        instruction = ('Run ' + run('close' + named) + ' to close this one agent (its running turn and queued requests); '
                       'the other agents keep working. Reply with its `message` as given.')
    elif kind == 'close-menu':
        instruction = ('Run ' + run('close', menu=True) + ' and display the returned menuView: several agents are running, '
                       'so the user chooses which to close. The reply (a number, a name, A for all or X) comes back '
                       'through this hook as its own control; do not close anything yet.')
    elif kind == 'use':
        instruction = ('Run ' + run('use' + named) + ' and reply with its `message` as given. /d prompts without a '
                       'name now go to this agent; nothing is sent to any agent.')
    elif kind == 'timeout':
        instruction = ('Run ' + run('timeout' + named + (' --minutes ' + str(decision['minutes'])
                                                          if decision.get('minutes') else '')) +
                       ' and reply with its `message` exactly as given. It saves a local setting only; nothing is sent '
                       'to any agent.')
    elif kind == 'attach':
        instruction = ('Run ' + run('attach' + (' --target ' + decision['target'] if decision.get('target') else '')) +
                       ' and reply with its `message` exactly as given. Attaching moves an open agent from an earlier '
                       'task in this folder into this one; nothing is sent to it now.')
    elif kind == 'diff':
        instruction = ('Run ' + run('diff' + named) + ' and reply with its `text` exactly as given: a line saying what '
                       'the agent\'s last turn changed, then the diff as a fenced diff block. It reads local files '
                       'only; nothing is sent to any agent.')
    elif kind == 'undo':
        instruction = ('Run ' + run('undo' + named) + ' and reply with its `text` exactly as given. It puts back the '
                       'files the agent\'s last turn changed, only if none changed since; nothing is sent to any agent.')
    elif kind in ('test', 'brief'):
        # Your own text (a test command, a brief point) is applied here, in the hook, never through a shell line.
        import controller as control
        words = (['test'] + (['--command=' + decision['command']] if decision.get('command') else []) if kind == 'test'
                 else ['brief', '--action', decision['action']] + (['--text=' + decision['text']]
                                                                   if decision.get('text') else []))
        result = control.run(control.build_parser().parse_args(
            ['--thread', store.thread, '--workspace', str(store.workspace), '--data-root', str(store.root),
             '--host', 'codex'] + words))
        instruction = 'Reply with exactly ' + json.dumps(result['text']) + '; it is already done, no command runs.'
    elif kind == 'dir':
        instruction = ('Run ' + run('dir' + named) + ' and reply with its `text` exactly as given, as plain lines in a '
                       'code block: where the agent saves its files, as a full path and as the path in the project. '
                       'It reads local files only; nothing is sent to any agent.')
    elif kind == 'usage':
        instruction = ('Run ' + run('usage' + named) + ' and reply with its `text` exactly as given, as plain lines in '
                       'a code block: each running agent\'s plan usage from its own CLI, or that it can\'t report it. '
                       'It reads usage only; no model request is made. It can take up to a minute: give it a timeout '
                       'of at least 150 seconds and wait rather than polling.')
    elif kind == 'agents':
        instruction = ('Run ' + run('agents' + (' --max ' + str(decision['max']) if decision.get('max') else '')) +
                       ' and reply with its `message` exactly as given, as plain lines in a code block. It reads local '
                       'state only; nothing is sent to any agent.')
    elif kind == 'cancel':
        instruction = (('The hook has signaled cancellation of ' + label + '\'s running turn. '
                        if cancellation and cancellation.get('canceled') else
                        'No provider turn was active; waiting requests are unchanged. ') +
                       'Do not forward this control or cancel queued follow-ups. Run ' + run('queue') + ' to inspect progress; '
                       'the queue worker will continue after any canceled turn settles.')
    elif kind == 'queue':
        instruction = ('Run ' + run('queue') + ' and report request IDs, statuses and any blocked operation. '
                       'This control reads local state only; do not send it to the provider or replay work.')
    elif kind == 'resume':
        request_ids = worker.get('requestIds', []) if worker else []
        if worker and worker.get('blocked'):
            if worker.get('worker', {}).get('worker') == 'start-failed':
                instruction = ('The queue worker could not start: ' + worker['worker']['error'] + '. Run ' +
                               run('queue') + ' and report the captured requests. Do not replay or submit them. '
                               'After the process launch issue is fixed, /cli resume can retry monitoring.')
            else:
                instruction = ('The queue worker is blocked by an unresolved provider turn. Run ' + run('queue') +
                               ' and report the uncertain request and operation. Do not restart or replay it. '
                               'After explicit reconciliation, /cli resume can reattach monitoring.')
        elif request_ids:
            instruction = ('The hook reattached to the existing provider work. Resume public status monitoring in this '
                           'conversation by relaying these captured requests in order: ' +
                           '; '.join(run('relay --request ' + request_id + ' --cursor 0') for request_id in request_ids) +
                           '. For each request, post its markdown updates and keep calling relay with its returned '
                           'cursor until done. '
                           'Then monitor the next listed request. Do not submit or reconstruct a prompt. ')
        elif worker and worker.get('latestRequestId'):
            instruction = ('No provider turn is pending. Show the last saved result with ' +
                           run('relay --request ' + worker['latestRequestId'] + ' --cursor 0') +
                           ' and report its terminal status. Do not submit the prompt again. ')
        else:
            instruction = 'No captured provider request exists to monitor. '
        if not worker or not worker.get('blocked'):
            instruction += ('Run ' + run('queue') + ' to report the worker state and any blocked operation. '
                            'If a request is uncertain, report that state without retrying inference.')
    elif kind == 'home':
        instruction = ('Run ' + run('frontend --agent home', menu=True) + ' and display its returned menu. Follow onboarding when present; '
                       'otherwise a numbered agent selection runs ' + ref('frontend --agent <that backend id>', menu=True) +
                       '. Never forward menu replies.')
    elif kind == 'frontend':
        instruction = ('Run ' + run('frontend --agent ' + adapter.ID, menu=True) + ' and display its returned menu. '
                       'Follow onboarding when present. Wait for selection; do not activate yet.')
    elif kind == 'direct-result':
        request_id = decision.get('requestId')
        instruction = ('This turn was already dispatched. Do not dispatch the original task again. ' +
                       ('Continue relaying it with ' + run('relay --request ' + request_id + ' --cursor <last cursor, or 0>') + '. '
                        if request_id else 'Read its saved public events and relay the existing result. ') +
                       'Inspect uncertain completion instead of retrying. Dispatch record: ' +
                       json.dumps({key: decision.get(key) for key in ('requestId', 'operation', 'events')}))
    elif kind == 'setup' and activation_reply(state, event.get('prompt', '')):
        # The activation menu's own choices, mapped to their exact controls (as on Claude Code).
        agent, answer = activation_reply(state, event.get('prompt', ''))
        import frontends
        if answer == '2':
            instruction = ('Run ' + run('options --phase model --agent ' + agent, menu=True) + ' and display the returned '
                           'model menu; the user replies with a number. Do not activate yet or forward this reply.')
        elif frontends.routing_readiness(state)['ready']:
            instruction = ('Run ' + run('activate --agent ' + agent, message=True) + '. It activates ' + label + ' with the '
                           'settings on its menu, sending one readiness prompt, which can take a minute: give it a timeout '
                           'of at least 120 seconds. It returns activation.messageView: show it by its reference line.')
        else:  # 1 is Recheck routing on this page.
            instruction = 'Run ' + run('frontend --agent ' + agent, menu=True) + ' and display its returned menu.'
    elif kind == 'setup' or (kind == 'restore' and state.get('pending')):
        instruction = 'A setup menu is pending. Restore its saved phase/draft and treat the user reply as setup, not agent work. Do not forward it.'
    elif kind == 'direct' and state['active'] and len(decision.get('requestIds') or []) > 1:
        requests = decision['requestIds']
        records = state.get('requests') or {}
        named = [agent_label(state, None, records.get(each)) for each in requests]
        instruction = ('CLI-MODE is ON. This message explicitly targets the CLI. The hook queued it once for each '
                       'agent it names, removing /d and the names: ' +
                       '; '.join(label + ' as request ' + each for label, each in zip(named, requests)) +
                       '. Each agent\'s queue worker forwards its copy, and they work at the same time. Relay them one '
                       'after another, in this order, each until done: ' +
                       '; then '.join(run('relay --request ' + each) for each in requests) +
                       '. Post each one\'s markdown updates and its final view as the relay rules say. Do not strip the '
                       'trigger yourself, call send, perform this task in Codex or use Codex subagents.')
    elif kind == 'direct' and state['active']:
        request_id = decision.get('requestId')
        instruction = ('CLI-MODE is ON. This message explicitly targets the CLI. ' +
            ('The hook queued the exact original message as request ' + request_id + ', and ' + label + '\'s queue '
             'worker forwards it to that agent (removing the /d or $d trigger exactly once' +
             (', and the agent name after it' if decision.get('named') else '') + '). Relay it with ' +
             run('relay --request ' + request_id) + '. ' if request_id else
             'No captured request is available. Wait for a fresh user message; do not reconstruct or dispatch an old prompt. ') +
            'Do not strip the trigger yourself, call send, perform this task in Codex or use Codex subagents. '
            'If the request is queued behind another turn, the view says so.')
    elif state['active']:
        request_id = decision.get('requestId') or state.get('turnRoute', {}).get('requestId')
        instruction = ('CLI-MODE is ON. The queue worker forwards complete original messages unchanged to the one main ' + label + ' session in arrival order. ' +
                       ('The hook queued this message as request ' + request_id + '. Relay it with ' +
                        run('relay --request ' + request_id) + '. Do not reconstruct or submit it again. ' if request_id else
                        'No captured request is available. Wait for a fresh user message; do not reconstruct or dispatch an old prompt. ') +
                       'Do not append context, rewrite, plan, split tasks or offer orchestration, even for a complex project or parallel work. '
                       + label + ' decides its own execution and native subagents. Never spawn native Codex subagents or additional provider sessions. '
                       'Restore saved state; idle expiry does not disable routing. User control triggers take precedence.')
    elif kind == 'restore' and not state.get('pending'):
        return {}
    else:
        return {}
    if worker and worker.get('worker') in ('blocked', 'start-failed'):
        instruction += ' Queue worker needs attention: ' + json.dumps(worker) + '. Do not replay accepted work; inspect receipts and resume after reconciliation.'
    return {'hookSpecificOutput': dict(hookEventName=name, additionalContext=context(
        kind, state, instruction, core, relay_rules, menu_rules, setup_rules, help_rules))}


class Commands:
    """Complete controller command lines for this conversation.

    Every instruction carries the exact command, including a fresh view path,
    so the model never has to read the skill to learn how to call the controller.
    """
    def __init__(self, store, state):
        self.base = ('python "' + str(PLUGIN / 'scripts' / 'controller.py') + '" --thread "' + store.thread +
                     '" --workspace "' + store.workspace + '"')
        turn = ((state.get('turnRoute') or {}).get('id') or uuid.uuid4().hex)[:12]
        self.views = Path(store.root) / 'views' / store.key
        self.menu = ' --menu-output "' + str(self.views / (turn + '-menu.html')) + '"'
        self.message = ' --message-output "' + str(self.views / (turn + '-message.html')) + '"'

    def run(self, command, menu=False, message=False):
        return ('`' + self.base + (self.menu if menu else '') + (self.message if message else '') +
                ' ' + command + '`')

    def ref(self, command, menu=False):
        """A secondary command, written against the legend() placeholders."""
        return '`<controller>' + (' <menu-output>' if menu else '') + ' ' + command + '`'

    def legend(self):
        return ('In the commands below, <controller> means `' + self.base + '` and <menu-output> means `' +
                self.menu.strip() + '`. ')


# Turns that relay provider output rather than render CLI-MODE menus.
RELAY_ROUTES = ('direct', 'direct-result', 'cancel', 'queue', 'resume')
HELP_ROUTES = ('help', 'help-invalid', 'help-dismiss')
# Relay turns need only these saved fields; menus need the pending transaction.
RELAY_STATE = ('active', 'routingMode', 'progressMode', 'backend', 'main')
MENU_STATE = ('active', 'routingMode', 'helpMenu', 'progressMode', 'pending', 'backend', 'settings', 'main')


def context(kind, state, instruction, core, relay_rules, menu_rules, setup_rules, help_rules):
    pending = state.get('pending') or {}
    helping = kind in HELP_ROUTES or (kind == 'restore' and state.get('helpMenu'))
    relaying = not helping and (kind in RELAY_ROUTES or (kind == 'restore' and state['active']
                                and not pending))
    if helping:
        rules, fields = help_rules, MENU_STATE
    elif relaying:
        rules, fields = relay_rules, RELAY_STATE
    elif kind in ('hint', 'off', 'close', 'use', 'agents', 'diff', 'dir', 'usage', 'undo', 'test', 'brief', 'timeout',
                  'attach'):
        rules, fields = '', RELAY_STATE
    else:
        rules, fields = menu_rules, MENU_STATE
        if not state['active'] or pending.get('onboarding') or kind in ('home', 'frontend', 'setup'):
            rules += setup_rules
    return (core + rules + instruction + ' Saved state: ' +
            json.dumps({key: state.get(key) for key in fields}))


if __name__ == '__main__':
    try:
        # Codex sends UTF-8; Windows Python would otherwise read stdin as the ANSI code page.
        print(json.dumps(handle(json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace')))))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        # Never silently interpret unreadable saved on-state as an off-state.
        print(json.dumps({'systemMessage': 'CLI-MODE state could not be restored: ' + str(exc)}))
        print('CLI-MODE routing cannot be determined. Inspect state before continuing.', file=sys.stderr)
        sys.exit(2)
