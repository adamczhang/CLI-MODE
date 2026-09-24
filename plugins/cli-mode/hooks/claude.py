"""Claude Code hook: route prompts, approve CLI-MODE's own commands, keep relays going.

Claude Code runs this file for SessionStart, UserPromptSubmit, PreToolUse (the
Agent tool, and shell commands that start with `python`) and Stop. The shared
route.decide() records what each prompt is, exactly as for Codex. This file
decides how Claude Code shows it:

- Local controls (menus, help, queue, stop) answer at once, without a model
  turn: the hook runs the controller in process and shows its text.
- Agent turns and slow controls (activation, installation) give Claude the
  exact controller command and say how its result is shown.
- CLI-MODE's own controller commands for this session are approved; nothing
  else is.
- A relay that Claude ends early is resumed from its saved cursor.

This runs for every prompt, every `python` command and every turn end, so it
returns early, before importing the controller, whenever it can.
"""
import os
import sys


def nothing_to_do(raw):
    """True for an event CLI-MODE has no part in, decided before anything heavy loads.

    Claude Code runs this hook for every session start, prompt, `python` command,
    subagent and turn end in EVERY session, and most sessions never use CLI-MODE.
    Only certain answers are given here; anything else goes to handle(), which
    decides everything again from scratch (this is purely a shortcut).
    """
    if '"PreToolUse"' in raw and 'controller.py' not in raw and '"Agent"' not in raw:
        return True  # Some other `python` command: only CLI-MODE's own controller is approved here.
    if '"SessionStart"' in raw and '"compact"' not in raw:
        return True  # Only a compaction has a relay or menu to restore.
    import json
    try:
        event = json.loads(raw)
        name, session = event['hook_event_name'], event['session_id']
        root = os.environ.get('CLI_MODE_DATA') or os.environ.get('CLAUDE_PLUGIN_DATA')
        if name not in ('UserPromptSubmit', 'Stop', 'PreToolUse') or not root:
            return False
        if name == 'PreToolUse' and event.get('tool_name') != 'Agent':
            return False  # Possibly CLI-MODE's controller: always checked for approval, active or not.
        if name == 'UserPromptSubmit' and event.get('prompt', '').lstrip()[:1] in ('/', '$'):
            return False  # A command, possibly CLI-MODE's.
        try:
            from _sha2 import sha256  # Python's own SHA-256: hashlib first loads OpenSSL (about 6 ms).
        except ImportError:
            from hashlib import sha256
        path = os.path.join(root, 'sessions', sha256(session.encode()).hexdigest() + '.json')
        if not os.path.isfile(path):
            return True  # CLI-MODE was never used in this session, and this is not a command.
        with open(path, encoding='utf-8') as source:
            saved = json.load(source)
        if saved.get('active'):
            # A prompt may be the agent's (passthrough), a subagent may need denying, a relay may be running.
            return name == 'Stop' and (saved.get('turnRoute') or {}).get('route') not in RELAY_ROUTES
        # Nothing is active: only an open menu, setup or help card can make a plain reply (such as X) CLI-MODE's.
        return not (any(saved.get(key) for key in ('pending', 'helpMenu', 'modeMenu')) or
                    (saved.get('turnRoute') or {}).get('route') == 'help')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False  # handle() reports what is wrong, as it always has.


RELAY_ROUTES = ('direct', 'delegate', 'direct-result')
if __name__ == '__main__':
    RAW = sys.stdin.buffer.read().decode('utf-8', 'replace')  # Claude Code sends UTF-8, whatever the code page.
    if nothing_to_do(RAW):
        print('{}')
        sys.exit(0)

import hashlib  # noqa: E402  (the rest loads only when CLI-MODE has something to do)
import json  # noqa: E402
from pathlib import Path  # noqa: E402
import re  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / 'scripts'))
sys.path.insert(0, str(PLUGIN / 'hooks'))
import host  # noqa: E402  (small; the controller loads only when a turn needs it)

CONTROLLER = (PLUGIN / 'scripts' / 'controller.py').as_posix()
# Characters that read the same unquoted in Git Bash and in PowerShell.
BARE = re.compile(r'[A-Za-z0-9_./:-]+')
ARGUMENT = re.compile(r'[A-Za-z0-9_.:-]+')
# Controller commands Claude may run without asking. The others (send, pump,
# route, draft, observe, format-*) read files or bypass the queue.
ALLOWED = frozenset((
    'relay', 'queue', 'status', 'bind', 'activate', 'choose', 'navigate', 'settings', 'mode', 'progress', 'view', 'tune',
    'activation-message', 'frontend', 'options', 'first-time-check', 'setup-status', 'setup-manual', 'setup-start',
    'off', 'cancel', 'resume', 'refresh', 'commands', 'catalog'))
RESET = ('/cli reset', '$cli reset', '/cli-mode:cli reset')
MAX_NUDGES = 3
# The skill describes CLI-MODE in general; opening it costs a turn and adds nothing to a context
# that already names the exact command.
COMPLETE = (' This context is complete for the turn: the CLI-MODE skill and its guides are not needed, and no other '
            'tool is.')
ALONE = ('shown exactly as given and on its own, with nothing added before or after it (no summary, commentary or '
         'insight blocks), whatever the output style')
# A failed control prints only {"error": ...} and exits 1. Its words are CLI-MODE's reply like any other
# (P7b run 3: Claude reworded a bind error and added advice of its own).
ERROR = (' A command that fails exits with code 1 and returns only `error`: the reply is then `CLI-MODE: ` followed by '
         'that error, shown the same way, with no advice or alternatives added.')
ACTIVATION_TIMEOUT_MS = 180000
RELAY_TIMEOUT_MS = 30000


# Commands ----------------------------------------------------------------

def quote(token):
    """One argument, quoted the same way for Git Bash and PowerShell when possible."""
    if BARE.fullmatch(token):
        return token
    return "'" + token.replace("'", "'\\''") + "'"


def data_root(root=None):
    return Path(root) if root else host.data_root(host.CLAUDE)


def workspace(event):
    # Always Claude's rule (the session's project folder), whatever this process selected.
    return host.workspace(event, host.CLAUDE)


def base(event, root=None):
    """The controller and this session's identity, exactly as every command starts."""
    return ['python', CONTROLLER, '--host', host.CLAUDE, '--thread', event['session_id'],
            '--workspace', Path(workspace(event)).resolve().as_posix(),
            '--data-root', data_root(root).as_posix()]


def command(event, root, *words):
    return ' '.join(quote(token) for token in base(event, root) + list(words))


def controller(event, root, *words):
    """Run one controller command in this process and return its result."""
    import controller as control
    return control.run(control.build_parser().parse_args(base(event, root)[2:] + list(words)))


def relay_words(requests, cursor=0, always_cursor=False):
    """One relay command for all of a turn's requests: Claude posts only its last command's final text reliably."""
    words = ['relay']
    for request in requests:
        words += ['--request', request]
    return words + (['--cursor', str(cursor)] if cursor or always_cursor else [])


# Replies -----------------------------------------------------------------

def context(event, text):
    return {'hookSpecificOutput': {'hookEventName': event['hook_event_name'], 'additionalContext': text}}


# How CLI-MODE's own replies appear, chosen with /cli display:
# chat (default): Claude posts the reply as a normal message (one small model turn);
# instant: the hook answers in place of the prompt (no model turn; Claude Code frames it as a hook notice).
DISPLAYS = {'instant': 'block', 'chat': 'model'}
DEFAULT_STYLE = DISPLAYS['chat']  # The instant notice reads like an error in the desktop app.
STYLE = DEFAULT_STYLE
COLOR = True  # Green titles and names in chat (host.chat_color); /cli color off for the terminal.


def display_path(root=None):
    return data_root(root) / 'display.json'


def display_style(root=None):
    """CLI_MODE_CLAUDE_INSTANT (block, stop or model) wins; then the saved /cli display choice."""
    forced = os.environ.get('CLI_MODE_CLAUDE_INSTANT')
    if forced in ('block', 'stop', 'model'):
        return forced
    try:
        return DISPLAYS.get(json.loads(display_path(root).read_text(encoding='utf-8')).get('display'), DEFAULT_STYLE)
    except (OSError, ValueError, AttributeError):
        return DEFAULT_STYLE


def show(event, fenced, plain=None):
    """A local control's reply, in the display style this user chose (see DISPLAYS)."""
    plain = fenced if plain is None else plain
    style = STYLE
    if style == 'model':
        import presentation
        return context(event, 'CLI-MODE answered this control itself; no command needs to run.' + COMPLETE +
                              ' Its reply is below, to be ' + ALONE + ':\n\n' + presentation.chat_menu(fenced, COLOR))
    if style == 'stop':
        return {'continue': False, 'stopReason': '\n' + plain}
    return {'decision': 'block', 'reason': plain, 'suppressOriginalPrompt': True}


def show_result(event, result):
    import presentation
    if isinstance(result.get('setup'), dict):
        result = result['setup']  # A finished install: the menu setup continues with, not the setup window's words.
    return show(event, presentation.result_text(result), presentation.result_text(result, fenced=False))


def show_text(event, text):
    return show(event, text, text)


def manual_text(result):
    """Setup's manual installation help as plain lines."""
    lines = [result.get('message') or 'Manual installation help.']
    lines += [name + ': ' + link for name, link in (result.get('links') or {}).items()]
    if result.get('acpxCommand'):
        lines.append('ACPX: ' + result['acpxCommand'])
    return '\n'.join(lines)


# Hook events -------------------------------------------------------------

def handle(event, root=None):
    host.select(host.CLAUDE)
    name = event['hook_event_name']
    if name == 'PreToolUse':
        return pre_tool_use(event, root)
    if name == 'Stop':
        return stop(event, root)
    if name not in ('SessionStart', 'UserPromptSubmit'):
        return {}
    global STYLE, COLOR
    try:
        STYLE = display_style(root)  # Before any reply, including /cli reset's.
        COLOR = host.chat_color(data_root(root), host.CLAUDE)
    except ValueError:
        STYLE, COLOR = DEFAULT_STYLE, True
    if name == 'UserPromptSubmit' and event.get('prompt', '').strip().casefold() in RESET:
        return reset(event, root)
    import route
    prompt = event.get('prompt', '')
    capture = host.unwrap_prompt(prompt, host.CLAUDE)
    store, state, decision, worker, cancellation = route.decide(event, root, workspace=workspace(event), capture=capture)
    if decision['route'] == 'help-invalid':
        # On Claude Code help is a card, not a mode: any other message closes it and is handled as usual,
        # so a question typed after the card still reaches Claude (or the agent).
        with store.edit() as saved:
            saved['helpMenu'] = None
        store, state, decision, worker, cancellation = route.decide(event, root, workspace=workspace(event),
                                                                    capture=capture)
    if name == 'UserPromptSubmit' and route.task_through_settings(state, prompt):
        # An explicit /d task typed while Agent Settings is open is a task, not a menu reply (a user was
        # asked to close the menu and send it again): the menu closes and the task goes to the agent.
        with store.edit() as saved:
            saved['pending'] = None
        store, state, decision, worker, cancellation = route.decide(event, root, workspace=workspace(event),
                                                                    capture=capture)
    if name == 'SessionStart':
        return session_start(event, root, state, decision)
    return prompt_reply(event, root, state, decision, worker, cancellation)


def pre_tool_use(event, root):
    tool = event.get('tool_name', '')
    if tool in ('Bash', 'PowerShell'):
        text = (event.get('tool_input') or {}).get('command') or ''
        if not text.startswith('python ' + quote(CONTROLLER) + ' '):
            return {}  # Not CLI-MODE's controller: leave it to Claude Code's permissions.
        return approve(event, text, root) or {}
    if tool == 'Agent':
        try:
            import route
            state = route.Store(event['session_id'], workspace(event), root).read()
            if route.delegated_turn(state):
                return {'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                    'permissionDecisionReason': 'This CLI-MODE turn belongs to the active agent, which runs its own '
                                                'subagents. Claude Code subagents stay off for delegated turns.'}}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass  # Unreadable state never blocks a tool here.
    return {}


def approve(event, text, root):
    """Allow exactly CLI-MODE's controller for this session, and nothing else.

    The command must be this session's controller with an allowed subcommand
    and plain arguments, and quoting its words again must give back the same
    text, so no shell syntax can hide inside it.
    """
    import shlex
    if '\n' in text or '\r' in text:
        return None
    try:
        tokens = shlex.split(text, posix=True)
        expected = base(event, root)
    except (ValueError, KeyError):
        return None
    rest = tokens[len(expected):]
    if (tokens[:len(expected)] != expected or not rest or rest[0] not in ALLOWED
            or not all(ARGUMENT.fullmatch(token) for token in rest)
            or any("'" in token for token in tokens)
            or ' '.join(quote(token) for token in tokens) != text):
        return None
    access = rest[rest.index('--access') + 1:][:1] if '--access' in rest else []
    if ((rest[0] == 'setup-start' and '--approved' in rest) or (rest[0] == 'activate' and access != ['prompt']
                                                                and '--access' in rest)
            or (rest[0] in ('tune', 'choose') and widens_access(event, rest, root))):
        # Installing, or letting an agent act without asking: the user's yes comes from Claude Code itself.
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                                       'permissionDecisionReason': 'CLI-MODE ' + (
                                           'installs what setup needs' if rest[0] == 'setup-start' else
                                           'activates the agent with wider access') + '; this needs your yes.'}}
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                   'permissionDecisionReason': 'CLI-MODE controller command for this session.'}}


def widens_access(event, rest, root):
    """True when a `tune --phase access` or an access-menu `choose` would give the agent wider access than it has.

    `/cli access allow` and the settings access menu reach the controller as these commands, not as
    `activate --access`, so the target level comes from the saved turn: the typed choice, or the menu row.
    Unreadable state asks rather than allows.
    """
    try:
        import frontends
        import route
        store = route.Store(event['session_id'], workspace(event), root)
        state = store.read()
        pending = state.get('pending') or {}
        if rest[0] == 'tune':
            if rest[rest.index('--phase') + 1:][:1] != ['access']:
                return False
            choice = ((state.get('turnRoute') or {}).get('choice') or '').strip()
            if not choice:
                return False  # No choice typed: it only opens the access menu.
            agent = state.get('backend') or pending.get('backend')
            options = frontends.phase_options(store.root, agent, 'access', state.get('settings'))
            matches = frontends.match_choice(options, choice, 'access')
            if len(matches) != 1:
                return False  # Ambiguous or unknown: the controller shows the menu instead.
            target = matches[0]
        else:
            if pending.get('phase') != 'access' or not rest[1:2] or not rest[1].isdigit():
                return False
            choices = pending.get('choices') or []
            number = int(rest[1])
            if not 1 <= number <= len(choices):
                return False
            target = choices[number - 1]['value']
        current = (state.get('settings') or {}).get('access') if state.get('active') else 'prompt'
        order = frontends.ACCESS_ORDER  # Widest first.
        return target in order and (current not in order or order.index(target) < order.index(current))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError):
        return True


def state_path(event, root):
    key = hashlib.sha256(event['session_id'].encode()).hexdigest()
    return data_root(root) / 'sessions' / (key + '.json')


def stop(event, root):
    """If Claude ends a turn before its relay is done, give it the next relay command."""
    try:
        path = state_path(event, root)
        if not path.is_file():
            return {}  # CLI-MODE was never used in this session.
        # Most turn ends are not relays: decide that from the saved JSON alone.
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not saved.get('active') or (saved.get('turnRoute') or {}).get('route') not in RELAY_ROUTES:
            return {}
        import route
        store = route.Store(event['session_id'], workspace(event), root)
        with store.edit() as state:
            turn = state.get('turnRoute') or {}
            request = turn.get('requestId')
            if not state['active'] or turn.get('route') not in RELAY_ROUTES or not request:
                return {}
            if ((state.get('relayProgress') or {}).get(request) or {}).get('done'):
                return {}
            count = (state.get('relayNudges') or {}).get(request, 0)
            if count >= MAX_NUDGES:
                return {}
            state['relayNudges'] = {request: count + 1}
            requests, cursor = relay_position(state, request)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {}  # A turn end is never blocked by unreadable state.
    return context(event, 'CLI-MODE request ' + request + ' has not finished relaying; the agent\'s remaining '
                          'output reaches the user only through it. Its next relay command is `' +
                   command(event, root, *relay_words(requests, cursor, always_cursor=True)) + '`.')


def relay_position(state, request):
    """The requests and stream cursor that continue `request`'s relay, with any earlier requests it carries."""
    progress = (state.get('relayProgress') or {}).get(request) or {}
    chain = progress.get('chain')
    if chain:
        return chain['requests'], chain['cursor']
    earlier = unrelayed(state, exclude=request)
    return earlier + [request], (0 if earlier else progress.get('cursor', 0))


def reset(event, root):
    """Set aside this session's CLI-MODE state without reading it (it may be unreadable)."""
    import time
    try:
        path = state_path(event, root)
    except ValueError as exc:
        return show_text(event, 'CLI-MODE: ' + str(exc))
    if not path.exists():
        return show_text(event, 'CLI-MODE has no saved state for this session. /cli starts setup.')
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        idle = not any(saved.get(key) for key in ('active', 'pending', 'owned', 'inflight'))
    except (OSError, ValueError, AttributeError):
        idle = False
    if idle:
        path.unlink()  # Nothing ran here; the file only recorded that hooks work.
        return show_text(event, 'CLI-MODE had nothing running in this session; its saved state was cleared. '
                                '/cli starts setup.')
    target = path.with_name(path.name + '.bad-' + time.strftime('%Y%m%d-%H%M%S'))
    path.replace(target)
    return show_text(event, 'CLI-MODE state for this session was set aside as ' + target.name + '. An agent '
                            'session it owned may keep running in ACPX until it idles out. /cli starts fresh.')


# Prompt replies ----------------------------------------------------------

def label_of(state, decision):
    import adapters
    agent = decision.get('agent') or (state.get('pending') or {}).get('backend') or state.get('backend') or 'agy'
    try:
        return adapters.module(agent)
    except ValueError:
        return adapters.module('agy')


def instant(event, root, *words, render=None):
    """Run a control in process and show its reply at once."""
    try:
        result = controller(event, root, *words)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        return show_text(event, 'CLI-MODE: ' + str(exc))
    return show_text(event, render(result)) if render else show_result(event, result)


def prompt_reply(event, root, state, decision, worker, cancellation):
    import presentation
    kind = decision['route']
    adapter = label_of(state, decision)
    pending = state.get('pending') or {}
    if kind in ('host', 'restore'):
        return {}
    if kind == 'hint':
        return show_text(event, decision['text'])
    if kind == 'help':
        return instant(event, root, 'commands')
    if kind == 'help-invalid':
        return {}  # handle() closes help and routes the message again; never hold it here.
    if kind == 'display':
        return display(event, root, decision.get('choice', ''))
    if kind == 'color':
        return color_choice(event, root, decision.get('choice', ''))
    if kind == 'shortcuts':
        import claude_shortcuts
        return show_text(event, claude_shortcuts.summary(claude_shortcuts.install()))
    if kind == 'help-dismiss':
        return show_text(event, 'Help closed.')
    if kind == 'home':
        return instant(event, root, 'frontend', '--agent', 'home')
    if kind == 'frontend':
        return instant(event, root, 'frontend', '--agent', adapter.ID)
    if kind == 'settings' and not re.match(r'(?i)\s*[/$]cli(-mode:cli)?\b', event.get('prompt', '')):
        # A reply the open settings page does not take (a task typed after a routing change, say): it stays
        # here, so say so instead of silently showing the page again.
        try:
            result = controller(event, root, 'settings')
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            return show_text(event, 'CLI-MODE: ' + str(exc))
        result['message'] = ('Agent Settings is open, so that message was not sent to ' + adapter.LABEL + '. Reply '
                             'with a number from the menu, or X to close it, then send your message again.')
        return show_result(event, result)
    if kind in ('settings', 'settings-dismiss'):
        return instant(event, root, 'settings', *(['--dismiss'] if kind == 'settings-dismiss' else []))
    if kind == 'mode':
        return instant(event, root, 'mode', *(['--choice', decision['choice']] if decision.get('choice') else []))
    if kind == 'mode-dismiss':
        return instant(event, root, 'mode', '--dismiss')
    if kind == 'progress':
        return instant(event, root, 'progress', *(['--choice', decision['choice']] if decision.get('choice') else []))
    if kind == 'view':
        return instant(event, root, 'view', *(['--choice', decision['choice']] if decision.get('choice') else []))
    if kind == 'navigate':
        return instant(event, root, 'navigate', decision['action'])
    if kind == 'queue':
        return instant(event, root, 'queue', render=presentation.queue_text)
    if kind == 'off':
        return instant(event, root, 'off', render=presentation.shutdown_text)
    if kind == 'cancel':
        note = ('Cancel requested for the running agent turn; queued follow-ups still run.'
                if cancellation and cancellation.get('canceled') else 'No agent turn was running; the queue is unchanged.')
        return instant(event, root, 'queue', render=lambda result: note + '\n\n' + presentation.queue_text(result))
    if kind == 'choose':
        if pending.get('phase') == 'access':  # The last choice activates the agent.
            return activation(event, root, adapter, 'applies the chosen access level and activates ' +
                              adapter.DISPLAY_NAME, 'choose', str(decision['number']))
        return instant(event, root, 'choose', str(decision['number']))
    if kind == 'tune':
        if not decision.get('text'):
            return instant(event, root, 'tune', '--phase', decision['phase'], '--apply')
        return activation(event, root, adapter, 'applies "' + decision['text'] + '" as ' + adapter.DISPLAY_NAME +
                          '\'s ' + decision['phase'] + ' when it matches one advertised option exactly (otherwise its '
                          'result is the options menu, with a message)', 'tune', '--phase', decision['phase'], '--apply')
    if kind == 'bind':
        if pending.get('stage') == 'verifying':
            return show_text(event, 'Activation is already being verified. /cli queue shows its progress.')
        return activation(event, root, adapter, 'activates ' + adapter.DISPLAY_NAME + ' with its saved defaults (the '
                          'first time: ' + json.dumps(adapter.DEFAULTS) + ')', 'bind', '--agent', adapter.ID)
    if kind == 'setup':
        return setup_reply(event, root, state, adapter, event.get('prompt', ''))
    if kind in ('direct', 'delegate'):
        request = decision.get('requestId')
        if not request:
            return show_text(event, 'CLI-MODE: no captured request. Send the message again.')
        lead = ('CLI-MODE forwarded this message' + (' (without its /d trigger)' if kind == 'direct' else '') +
                ' unchanged to the active ' + adapter.LABEL + ' session. Only its text was forwarded: images or files '
                'attached to it stay with Claude Code, so if it had any, one short line saying the agent did not '
                'receive them follows the opening line below, in the same message.')
        earlier = unrelayed(state, exclude=request)
        if earlier:
            lead += (' The relay also carries earlier requests whose output the user has not seen yet (a relay was '
                     'interrupted), first and in order; they are not sent again.')
        return context(event, relay_context(event, root, adapter, earlier + [request], lead, worker, passing=True))
    if kind == 'resume':
        return resume_reply(event, root, adapter, worker, state)
    return {}


def unrelayed(state, exclude=None, limit=3):
    """This activation's requests whose relay never finished: their output has not reached the user."""
    progress = state.get('relayProgress') or {}
    rows = [(record.get('capturedAt') or 0, key) for key, record in (state.get('requests') or {}).items()
            if key != exclude and record.get('generation') == state.get('generation')
            and record.get('session') == state.get('main')
            and record.get('status') in ('captured', 'submitting', 'uncertain', 'completed')
            and not record.get('relayed') and not (progress.get(key) or {}).get('done')]
    return [key for _, key in sorted(rows)[-limit:]]


def display(event, root, choice):
    """/cli display [chat|instant]: how CLI-MODE's own replies appear, saved for every session."""
    global STYLE
    current = next((name for name, style in DISPLAYS.items() if style == STYLE), 'instant')
    if choice not in DISPLAYS:
        return show_text(event, 'CLI-MODE replies appear as ' + current + ' replies. Use /cli display chat (normal '
                                'chat messages, one small model turn) or /cli display instant (at once, no model '
                                'turn, framed by Claude Code as a hook notice).')
    save_display(root, display=choice)
    STYLE = DISPLAYS[choice]
    return show_text(event, 'CLI-MODE replies now appear as ' + ('normal chat messages.' if choice == 'chat' else
                            'instant replies, without a model turn.'))


def save_display(root, **choices):
    """Keep this choice with the others in display.json (one file for /cli display and /cli color)."""
    path = display_path(root)
    saved = host.display_choices(data_root(root))
    saved.update(choices)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved) + '\n', encoding='utf-8')


def color_choice(event, root, choice):
    """/cli color [on|off]: green titles and names in chat, or plain bold (the terminal shows the
    LaTeX that colours them as raw text). Saved for every session."""
    global COLOR
    current = 'on' if COLOR else 'off'
    if choice not in ('on', 'off'):
        return show_text(event, 'CLI-MODE colour is ' + current + '. Use /cli color on (green titles and names) '
                                'or /cli color off (plain bold, for the terminal).')
    save_display(root, color=choice)
    COLOR = choice == 'on'
    return show_text(event, 'CLI-MODE titles and names are now ' + ('green.' if COLOR else 'plain bold.'))


def activation(event, root, adapter, what, *words):
    """A control that can take minutes (it starts or reconfigures the agent): Claude runs it once."""
    return context(event, (
        'CLI-MODE: this control ' + what + '. Its command is `' + command(event, root, *words) + '`. It checks '
        'readiness and sends the agent one short readiness prompt, which can take a minute or two, so it runs once, '
        'with a timeout of ' + str(ACTIVATION_TIMEOUT_MS) + ' ms, and is not polled or retried. Its JSON result '
        'carries `activation.text`, the confirmation the user expects ' + ALONE + '; or else a menu '
        '(`activationMenu`, with any `message`) shown the same way.' + ERROR + ' An error says what setup still '
        'needs; installing or signing in happens only when the user asks for it.' + COMPLETE))


def setup_reply(event, root, state, adapter, reply):
    """A reply while setup or activation is open, mapped to its exact control."""
    import frontends
    pending = state.get('pending') or {}
    agent = pending.get('backend') or pending.get('entrypoint') or adapter.ID
    answer = reply.strip().casefold()
    onboarding = pending.get('onboarding')
    if onboarding == 'installing':
        return instant(event, root, 'setup-status')
    if onboarding == 'check' and agent != 'home':
        if answer == 'r':
            return instant(event, root, 'first-time-check', '--agent', agent)
        if answer == 'm':
            return instant(event, root, 'setup-manual', render=manual_text)
        if answer == 'i':
            return context(event, (
                'CLI-MODE setup: the user chose I, to install what ' + adapter.DISPLAY_NAME + ' is missing. The '
                'installer command is `' + command(event, root, 'setup-start', '--approved', '--agent', agent) +
                '`; it opens a setup window. Installing and signing in need the user\'s explicit yes to exactly that: '
                'Claude Code asks for it as a permission prompt when the command runs, so it is not asked again in '
                'chat. Once it runs, `' + command(event, root, 'setup-status') +
                '` reports progress; checking every 5 to 10 seconds until it completes is enough. Results are menus '
                '(`activationMenu`, with any `message`) ' + ALONE + '.' + ERROR + ' Nothing is activated until the user '
                'chooses Yes afterwards.' + COMPLETE))
    if pending.get('phase') == 'activation' and pending.get('stage') == 'menu' and not onboarding and agent != 'home':
        if answer in ('1', 'yes', 'y'):
            if not frontends.routing_readiness(state)['ready']:
                return instant(event, root, 'frontend', '--agent', agent)  # 1 is Recheck routing on this page.
            return activation(event, root, adapter, 'activates ' + adapter.DISPLAY_NAME + ' with the settings on its '
                              'menu', 'activate', '--agent', agent)
        if answer == '2':
            return instant(event, root, 'options', '--phase', 'model', '--agent', agent)
    return context(event, (
        'CLI-MODE setup is open; this reply answers its menu and is not a task for any agent. Its controls: rerun the '
        'check `' + command(event, root, 'first-time-check', '--agent', agent) + '`; manual steps `' +
        command(event, root, 'setup-manual') + '`; install after the user\'s explicit yes `' +
        command(event, root, 'setup-start', '--approved', '--agent', agent) + '`; progress `' +
        command(event, root, 'setup-status') + '`; back to the agents `' +
        command(event, root, 'frontend', '--agent', 'home') + '`; stop `' + command(event, root, 'off') + '`. '
        'Results are menus (`activationMenu`, with any `message`) ' + ALONE + '.' + ERROR + COMPLETE + ' Saved setup: ' +
        json.dumps(pending)))


def relay_context(event, root, adapter, requests, lead, worker=None, cursor=0, passing=False):
    """The facts Claude needs to relay agent output, and how the user expects to see it.

    Claude Code's desktop app folds a turn's text between tool calls out of view (the user saw "Passing to"
    only inside a collapsed group), so the opening line comes before any command and the agent's output is
    the turn's last message, with nothing posted in between.
    """
    opening = ''
    if passing:
        import presentation
        # The line is its text block's last text while the relay runs, and the desktop app keeps a "$" at the
        # very end of a still-streaming block as plain text (it may become "$$"): a zero-width space after it
        # lets the green line render at once rather than when the turn ends.
        line = presentation.strong(adapter.PASSING, COLOR) + ('​' if COLOR else '')
        opening = (' The turn opens with this line, exactly as written on the next line, posted before any '
                   'command:\n' + line + '\n')
    text = lead + opening + (
             ' The agent\'s output reaches the user only through the relay command `' +
             command(event, root, *relay_words(requests, cursor)) + '`' +
             (', which covers these ' + str(len(requests)) + ' requests in order: its final part carries each '
              'one\'s output, oldest first' if len(requests) > 1 else '') + '. '
             'It runs in the Bash or PowerShell tool with a timeout of ' + str(RELAY_TIMEOUT_MS) + ' ms. Each call '
             'waits up to 25 seconds and prints plain text. While the agent is still working, that is one line with '
             'the `--cursor` for the next call, and the user expects the same command run again at once, with no '
             'sleep and no text between calls (the app folds text between tool calls out of view). Once the agent '
             'has finished, a first line says so, and everything after it is the agent\'s output, posted exactly '
             'as printed as the last message of the turn. (A very long answer comes in parts: a first line saying '
             'so means that part is posted exactly before the next call.) Relayed text carries nothing added: no '
             'summary, commentary, rewording or insight blocks, whatever the output style. The task belongs to ' +
             adapter.LABEL + ', which plans and runs it with its own tools and subagents, so it is not answered, '
             'planned or split here and the Agent tool stays unused. The worker checks provider slash commands '
             'before dispatch and reports unsupported ones as an error in the relayed output. ' +
             ('Supported ' + adapter.LABEL + ' slash commands move this conversation to its native CLI, which starts a '
              'fresh conversation.' if adapter.NATIVE_HANDOFF else
              'Supported ' + adapter.LABEL + ' slash commands run inside the same agent session, which keeps its history.')
             + COMPLETE)
    if worker and worker.get('worker') in ('blocked', 'start-failed'):
        text += (' The queue worker needs attention (' + json.dumps(worker) + '); /cli queue shows the blocked '
                 'operation, and nothing is sent again.')
    return text


def resume_reply(event, root, adapter, worker, state):
    import presentation
    worker = worker or {}
    if worker.get('blocked'):
        started = worker.get('worker') or {}
        reason = ('The queue worker could not start: ' + str(started.get('error')) + '.'
                  if started.get('worker') == 'start-failed' else
                  'The queue worker is blocked by an unresolved agent turn; nothing is sent again.')
        return instant(event, root, 'queue', render=lambda result: reason + '\n\n' + presentation.queue_text(result))
    # In-flight work, and finished work whose output was never shown, oldest first.
    captured = state.get('requests') or {}
    pending = set(unrelayed(state)) | set(worker.get('requestIds') or [])
    requests = sorted(pending, key=lambda key: (captured.get(key) or {}).get('capturedAt') or 0)
    requests = requests or ([worker['latestRequestId']] if worker.get('latestRequestId') else [])
    if not requests:
        return instant(event, root, 'queue', render=lambda result: 'No captured request to monitor.\n\n' +
                       presentation.queue_text(result))
    lead = ('CLI-MODE reattached to ' + adapter.LABEL + '\'s captured work; these requests are not sent to the agent '
            'again, only monitored.')
    return context(event, relay_context(event, root, adapter, requests, lead, worker))


def session_start(event, root, state, decision):
    """After compaction, restore what the last turn was doing; otherwise nothing is needed."""
    if event.get('source') != 'compact':
        return {}
    kind = decision.get('route')
    adapter = label_of(state, decision)
    request = decision.get('requestId')
    if kind in ('direct', 'delegate', 'direct-result') and request and state['active']:
        if ((state.get('relayProgress') or {}).get(request) or {}).get('done'):
            return {}
        lead = ('The conversation was compacted while CLI-MODE was relaying request ' + request + ' from ' +
                adapter.LABEL + '; the request is not sent again, and relaying continues from its saved cursor.')
        requests, cursor = relay_position(state, request)
        return context(event, relay_context(event, root, adapter, requests, lead, cursor=cursor))
    if kind == 'setup' and state.get('pending'):
        return context(event, 'CLI-MODE setup is open (saved setup: ' + json.dumps(state['pending']) + '); the next '
                              'reply answers its menu, not an agent task.')
    if kind == 'help':
        return context(event, 'CLI-MODE help was showing; X closes it.')
    return {}


if __name__ == '__main__':
    try:
        payload = json.loads(RAW)
    except ValueError:
        print('{}')
        sys.exit(0)
    event_name = payload.get('hook_event_name') if isinstance(payload, dict) else None
    try:
        print(json.dumps(handle(payload)))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        if event_name != 'UserPromptSubmit':
            print('{}')  # Only a prompt is held back when state cannot be read.
            sys.exit(0)
        if isinstance(exc, RuntimeError):
            # A control that could not finish (state busy, queue full, a failed cancel): the state is fine.
            print(json.dumps({'decision': 'block', 'reason': 'CLI-MODE: ' + str(exc)}))
            sys.exit(0)
        # Never treat unreadable saved state as "off": the prompt might have been meant for the agent.
        print(json.dumps({'decision': 'block', 'reason': 'CLI-MODE state could not be read: ' + str(exc) +
                          '. /cli reset sets it aside for this session.'}))
