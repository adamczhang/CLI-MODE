"""Claude Code hook: route prompts, approve CLI-MODE's own commands, keep relays going.

Claude Code runs this file for SessionStart, UserPromptSubmit, PreToolUse (the
Agent and scheduling tools, and shell commands that start with `python`) and
Stop. The shared route.decide() records what each prompt is, exactly as for
Codex. This file decides how Claude Code shows it:

- Local controls (menus, help, queue, stop) and activation answer in the hook,
  without a command for Claude: the hook runs the controller in process.
- A /d turn posts "Passing to ...", starts a background `follow` and ends; the
  follow's notification wakes Claude for one relay. Installing and widening
  access stay commands Claude runs, so Claude Code asks first.
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
    if '"PreToolUse"' in raw and 'controller.py' not in raw and 'BRIEF.md' not in raw and not any(
            '"' + tool + '"' in raw for tool in AGENT_TURN_TOOLS):
        return True  # Some other `python` command: only CLI-MODE's own controller (or the brief) is approved here.
    if '"SessionStart"' in raw and '"compact"' not in raw:
        return True  # Only a compaction has a relay or menu to restore.
    import json
    try:
        event = json.loads(raw)
        name, session = event['hook_event_name'], event['session_id']
        root = os.environ.get('CLI_MODE_DATA') or os.environ.get('CLAUDE_PLUGIN_DATA')
        if name not in ('UserPromptSubmit', 'Stop', 'PreToolUse') or not root:
            return False
        if name == 'PreToolUse' and event.get('tool_name') not in AGENT_TURN_TOOLS:
            return False  # Possibly CLI-MODE's controller: always checked for approval, active or not.
        if name == 'UserPromptSubmit' and event.get('prompt', '').lstrip()[:1] in ('/', '$', '@'):
            return False  # A command, possibly CLI-MODE's (after the files the desktop app attached, as @"path").
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
            # A prompt may be a /d task or a notification, a tool may need denying, a relay may be running.
            return name == 'Stop' and (saved.get('turnRoute') or {}).get('route') not in RELAY_ROUTES
        # Nothing is active: only an open menu, setup or help card can make a plain reply (such as X) CLI-MODE's.
        return not (any(saved.get(key) for key in ('pending', 'helpMenu')) or
                    (saved.get('turnRoute') or {}).get('route') == 'help')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False  # handle() reports what is wrong, as it always has.


RELAY_ROUTES = ('direct', 'direct-result')
# Claude Code tools a turn that belongs to the agent never needs: the agent runs its own subagents, and the
# follow's end wakes the conversation by itself (live run 3 scheduled a wake-up 20 minutes out instead).
# claude/hooks.json's PreToolUse matcher lists the same tools.
AGENT_TURN_TOOLS = ('Agent', 'ScheduleWakeup', 'CronCreate', 'Monitor')
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
    'relay', 'follow', 'queue', 'status', 'bind', 'activate', 'choose', 'navigate', 'settings', 'progress', 'view', 'tune',
    'activation-message', 'frontend', 'options', 'first-time-check', 'setup-status', 'setup-manual', 'setup-start',
    'off', 'close', 'use', 'agents', 'diff', 'dir', 'usage', 'undo', 'timeout', 'attach', 'cancel', 'resume', 'refresh', 'commands',
    'catalog'))
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
FOLLOW_LABEL = 30  # Characters of the prompt in a follow's background-task row.


def background():
    """Claude Code runs background tasks unless CLAUDE_CODE_DISABLE_BACKGROUND_TASKS turns them off."""
    return os.environ.get('CLAUDE_CODE_DISABLE_BACKGROUND_TASKS', '').strip().lower() not in ('1', 'true', 'yes', 'on')


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


def show(event, fenced, plain=None, step=None):
    """A local control's reply, in the display style this user chose (see DISPLAYS).

    `step` is one thing Claude does first in the same turn (writing the host's note in the project brief); only a
    chat reply has a model turn to do it in.
    """
    plain = fenced if plain is None else plain
    style = STYLE
    if style != 'model':
        import presentation
        plain = presentation.plain_strong(plain)  # A hook notice shows green LaTeX (the activation card's) raw.
    if style == 'model':
        import presentation
        lead = ('CLI-MODE answered this control itself; no command needs to run.' + COMPLETE if not step else
                'CLI-MODE answered this control itself; no command needs to run. ' + step + ' Then the reply below is '
                'this turn\'s last message,')
        return context(event, lead + ' Its reply is below, to be ' + ALONE + ':\n\n' +
                       presentation.chat_menu(fenced, COLOR))
    if style == 'stop':
        return {'continue': False, 'stopReason': '\n' + plain}
    return {'decision': 'block', 'reason': plain, 'suppressOriginalPrompt': True}


def show_result(event, result):
    import presentation
    if isinstance(result.get('setup'), dict):
        result = result['setup']  # A finished install: the menu setup continues with, not the setup window's words.
    return show(event, presentation.result_text(result), presentation.result_text(result, fenced=False),
                step=presentation.host_note_step(result.get('hostNote')))


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
    if name == 'UserPromptSubmit' and NOTIFICATION.match(event.get('prompt', '')):
        return notification_reply(event, root)
    if name == 'UserPromptSubmit' and event.get('prompt', '').strip().casefold() in RESET:
        return reset(event, root)
    import route
    if name == 'UserPromptSubmit':
        event = with_images(route.attached(event), event.get('prompt') or '', root)
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


def with_images(event, typed, root=None):
    """A /d event with the images pasted into it added to its `attachments`; `typed` is the prompt as sent.

    The desktop app saves every attached file in `uploads/<session>/` just before the prompt is sent. A file
    reaches the prompt text as an @"path" mention (route.attached), but an image does not appear in it at all.
    So an image of this prompt is a file in that folder that no earlier message of the conversation named: the
    transcript names each earlier message's files. The transcript may already end with this very prompt, so
    that last user message is not counted as earlier. Uploads a /d has already sorted are kept in the session's
    state (`seenUploads`), so the transcript is read only when a new file appears.
    """
    from state import direct_payload
    if direct_payload(event.get('prompt') or '') is None:
        return event
    folder = host.home(host.CLAUDE) / 'uploads' / str(event.get('session_id') or '')
    try:
        uploads = sorted((path for path in folder.iterdir() if path.is_file()), key=lambda path: path.stat().st_mtime)
    except OSError:
        return event
    # Uploads already sorted by an earlier /d are never this prompt's: only a new one needs the transcript, which
    # holds every pasted image as base64 and grows to tens of MB in a long session.
    try:
        import route
        store = route.Store(event['session_id'], workspace(event), root)
        seen = set(store.read().get('seenUploads') or [])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        store, seen = None, set()
    every = {path.name for path in uploads}
    known = {os.path.normcase(os.path.abspath(path)) for path in event.get('attachments') or []}
    uploads = [path for path in uploads if os.path.normcase(str(path)) not in known and path.name not in seen]
    if not uploads:
        return event
    try:
        lines = Path(event['transcript_path']).read_text(encoding='utf-8', errors='replace').splitlines()
    except (OSError, KeyError, TypeError):
        return event  # Without the transcript, which uploads are new can't be told: none are added.
    if store is not None:
        try:
            with store.edit() as state:  # Every upload there now is this prompt's or an earlier message's.
                state['seenUploads'] = sorted(set(state.get('seenUploads') or []) | every)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
    def text_of(content):
        return content if isinstance(content, str) else ''.join(
            part.get('text', '') for part in content or [] if isinstance(part, dict) and part.get('type') == 'text')

    def record_of(line):
        try:
            return json.loads(line)
        except ValueError:
            return {}
    # A prompt sent while Claude was busy is first logged as a queued command, with its files: not an earlier one.
    lines = [line for line in lines if '"queued_command"' not in line or
             text_of((record_of(line).get('attachment') or {}).get('prompt')).strip() != typed.strip()]
    for index in range(len(lines) - 1, -1, -1):
        if '"user"' not in lines[index] or '"tool_result"' in lines[index]:
            continue
        record = record_of(lines[index])
        if record.get('type') != 'user' or not isinstance(record.get('message'), dict):
            continue
        if text_of(record['message'].get('content')).strip() == typed.strip():
            lines = lines[:index]  # This prompt's own message (and the files listed after it) is not an earlier one.
        break  # Only the latest person's message can be this prompt.
    earlier = '\n'.join(lines)
    images = [str(path) for path in uploads if path.name not in earlier]
    if not images:
        return event
    return dict(event, attachments=(event.get('attachments') or []) + images)


def brief_note_approval(event):
    """Allow the host's note in the project brief, and only that: an Edit of this project's BRIEF.md that replaces
    one of its "not written yet" lines (it must be in the file, whole) with plain lines, no heading.

    Asking the user for this one edit on every agent start would make the note a chore; any other edit of the
    brief, or of any other file, is left to Claude Code's own permissions.
    """
    import agent_folder
    request = event.get('tool_input') or {}
    old, new = request.get('old_string') or '', request.get('new_string') or ''
    try:
        brief = agent_folder.brief_path(workspace(event))
        if os.path.normcase(os.path.realpath(request.get('file_path') or '')) != os.path.normcase(
                os.path.realpath(brief)):
            return None
        lines = brief.read_text(encoding='utf-8').splitlines()
    except (OSError, KeyError, TypeError, ValueError):
        return None
    waiting = agent_folder.HOST_NOTE_WAITING[:-2]  # `(The host has not written this note yet`
    if (request.get('replace_all') or not old.startswith(waiting) or '\n' in old or lines.count(old) != 1
            or not new.strip() or len(new) > 2000
            or any(line.lstrip().startswith('#') for line in new.splitlines())):
        return None
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                   'permissionDecisionReason': 'CLI-MODE: the host\'s note in the project brief.'}}


def pre_tool_use(event, root):
    tool = event.get('tool_name', '')
    if tool == 'Edit':
        return brief_note_approval(event) or {}
    if tool in ('Bash', 'PowerShell'):
        text = (event.get('tool_input') or {}).get('command') or ''
        if not text.startswith('python ' + quote(CONTROLLER) + ' '):
            return {}  # Not CLI-MODE's controller: leave it to Claude Code's permissions.
        return approve(event, text, root) or {}
    if tool in AGENT_TURN_TOOLS:
        try:
            import route
            state = route.Store(event['session_id'], workspace(event), root).read()
            if route.delegated_turn(state):
                reason = ('This CLI-MODE turn belongs to the active agent, which runs its own subagents. Claude Code '
                          'subagents stay off for delegated turns.' if tool == 'Agent' else
                          'This CLI-MODE turn belongs to the active agent. Its background follow wakes this '
                          'conversation when the agent finishes, so no wake-up, schedule or monitor is needed: the '
                          'turn ends now.')
                return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                               'permissionDecisionReason': reason}}
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
    if rest[0] == 'follow':
        return follow_approval(event, root, rest)
    access = rest[rest.index('--access') + 1:][:1] if '--access' in rest else []
    if ((rest[0] == 'setup-start' and '--approved' in rest) or (rest[0] == 'activate' and access != ['prompt']
                                                                and '--access' in rest)
            or (rest[0] in ('tune', 'choose') and widens_access(event, rest, root))):
        # Installing, or letting an agent act without asking: the user's yes comes from Claude Code itself.
        return labelled(event, root, rest, {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                                            'permissionDecisionReason': 'CLI-MODE ' + (
                                                'installs what setup needs' if rest[0] == 'setup-start' else
                                                'activates the agent with wider access') + '; this needs your yes.'})
    return labelled(event, root, rest, {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                        'permissionDecisionReason': 'CLI-MODE controller command for this session.'})


# The only controller commands that can run past the desktop app's row threshold (about 2-3 s) as a command
# Claude runs: an activation that widens access (13-42 s), and the relay loop without background tasks (25 s).
ROW_LABELS = {'bind': 'starting', 'activate': 'starting', 'choose': 'starting', 'tune': 'starting', 'relay': 'answer'}


def labelled(event, root, rest, output):
    """Name the command's row, should it get one, after the agent rather than Claude's own words."""
    if rest[0] in ROW_LABELS:
        try:
            import adapters
            import route
            from state import agent_label
            state = route.Store(event['session_id'], workspace(event), root).read()
            pending = state.get('pending') or {}
            if pending.get('stage') and not pending.get('session'):
                name = adapters.module(pending.get('backend') or 'agy').LABEL  # A new agent: no name yet.
            else:
                name = agent_label(state, pending.get('session') or (state.get('turnRoute') or {}).get('session'))
            output['updatedInput'] = dict(event.get('tool_input') or {},
                                          description=name + ' · ' + ROW_LABELS[rest[0]])
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass  # Only the row's name: Claude's own stays.
    return {'hookSpecificOutput': output}


def follow_approval(event, root, rest):
    """A follow runs as a background task with its own row in Claude Code, and once per request.

    Claude Code replaces the whole tool input with `updatedInput`, so the command it runs is the one
    approve() just checked; only the background flag and the row's label (agent and prompt) are added.
    """
    import operations
    import route
    from queue_worker import request_label
    if len(rest) != 3 or rest[1] != '--request':
        return None
    try:
        store = route.Store(event['session_id'], workspace(event), root)
        state = store.read()
        if rest[2] not in (state.get('requests') or {}):
            return None  # Not a request of this session: Claude Code's own permissions decide.
        running = operations.following(store, rest[2])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None
    if running:
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                       'permissionDecisionReason': 'CLI-MODE is already following this request; '
                                                                   'when that follow ends, its relay runs.'}}
    remember_follow(event, root, rest[2])
    # The label is only the row's name: an older request without one still gets its follow, named by the agent.
    label = (state.get('followLabels') or {}).get(rest[2])
    if not isinstance(label, str) or not label:
        label = request_label(state, rest[2])
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                   'permissionDecisionReason': 'CLI-MODE follows the agent in the background.',
                                   'updatedInput': dict(event.get('tool_input') or {}, run_in_background=True,
                                                        description=label)}}


FOLLOW_LABELS_KEPT = 20
# Claude Code wakes a session with a background task's notification as a prompt of its own, and runs
# UserPromptSubmit on it (probe P6, 2026-09-24). It is never the user's: routed, it became a host turn (which
# dropped the Stop guard and tool refusals of the /d turn), and an open menu would take it as a reply.
NOTIFICATION = re.compile(r'\s*<task-notification>')
TOOL_USE_ID = re.compile(r'<tool-use-id>\s*([A-Za-z0-9_-]{1,128})\s*</tool-use-id>')


def remember_follow(event, root, request):
    """Record which tool call is the request's follow: its notification names that call's ID."""
    tool_use = event.get('tool_use_id')
    if not isinstance(tool_use, str) or not tool_use:
        return
    import route
    try:
        with route.Store(event['session_id'], workspace(event), root).edit() as state:
            follows = state.setdefault('followTasks', {})
            follows.pop(tool_use, None)
            follows[tool_use] = request
            for stale in list(follows)[:-FOLLOW_LABELS_KEPT]:
                follows.pop(stale)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass  # The wake-up turn still has the /d turn's relay command.


def notification_reply(event, root):
    """A background task's notification: never routed; for CLI-MODE's own follow, the exact relay to run."""
    import route
    try:
        state = route.Store(event['session_id'], workspace(event), root).read()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {}
    # One prompt can carry several notifications (Claude's own tasks among them): the first follow of ours counts.
    follows = state.get('followTasks') or {}
    request = next((follows[found] for found in TOOL_USE_ID.findall(event.get('prompt', '')) if found in follows),
                   None)
    if not request or not state.get('active') or request not in (state.get('requests') or {}):
        return {}  # Claude's own background work, or an agent no longer active: not CLI-MODE's to answer.
    from queue_worker import request_label
    label = request_label(state, request)
    if ((state.get('relayProgress') or {}).get(request) or {}).get('done'):
        return context(event, 'CLI-MODE: this notification is the end of the background follow of request ' + request +
                              ', whose relay has already run: its output is posted exactly as that relay printed it '
                              '(if it is not posted yet), and nothing else is needed, no command and no other text.' +
                       COMPLETE)
    requests, cursor = relay_position(state, request)
    others = finished_elsewhere(state, requests) if not cursor else []
    if others:  # All of them have finished, so any order relays; oldest first reads naturally.
        requests = sorted(others + requests, key=lambda key: state['requests'][key].get('capturedAt') or 0)
    together = ('' if not others else ', together with what ' + ', '.join(sorted({
        request_label(state, other) for other in others})) + ' finished at the same time')
    return context(event, (
        'CLI-MODE: this notification is the end of the background follow of request ' + request + ', so ' + label +
        ' has finished. Its output' + together + ' reaches the user only through the relay command `' +
        command(event, root, *relay_words(requests, cursor)) + '`' +
        (', which covers these ' + str(len(requests)) + ' requests in order, oldest first' if len(requests) > 1
         else '') + '. It runs once now, in the Bash or PowerShell tool with a timeout of ' + str(RELAY_TIMEOUT_MS) +
        ' ms, and prints plain text: a first line saying the agent has finished, and everything after it is the '
        'agent\'s output, posted exactly as printed as the last message of this turn. If another agent finishes '
        'during this turn and its relay runs too, the last message carries both outputs, in the order the relays '
        'ran, each exactly as printed. (A very long answer comes in '
        'parts: a first line saying so means that part is posted exactly before the same command runs again with '
        'the `--cursor` it names.) Relayed text carries nothing added: no summary, commentary, rewording or insight '
        'blocks, whatever the output style, and no other tool is used.' + COMPLETE))


def finished_elsewhere(state, requests):
    """Other agents' requests that have finished but whose output was never relayed, oldest first.

    Agents that finish together wake Claude together. Given one relay each, Claude posts only the last relay's
    answer ("as the last message of the turn") and the others are lost (live, 2026-09-25: GRO-CC's answer
    dropped behind COD-9N's). So a wake-up relays them all with one command, as relay_chain already does for one
    agent's interrupted requests. Requests still running are left to their own follow's end.
    """
    records = state.get('requests') or {}
    return [key for key in unrelayed(state, limit=16) if key not in requests
            and records[key].get('status') not in ('captured', 'submitting')]


def remember_label(event, root, request, agent, named=False):
    """Name the request's follow row now, while its prompt is at hand: `<Agent NAME> · <start of the prompt>`.

    The captured text file is gone once the worker submits it (usually within a second), so the label
    is saved with the session state, under a key only this hook uses.
    """
    import route
    from state import direct_payload
    text = host.unwrap_prompt(event.get('prompt', ''), host.CLAUDE)
    text = direct_payload(text) or text  # What the agent was sent, without the /d trigger.
    words = ' '.join(text.split())
    for _ in range(int(named or 0)):
        words = words.split(' ', 1)[1] if ' ' in words else ''  # The agents' names are not part of the prompt.
    label = agent + ' · ' + (words[:FOLLOW_LABEL].rstrip() + '…' if len(words) > FOLLOW_LABEL else words)
    try:
        with route.Store(event['session_id'], workspace(event), root).edit() as state:
            labels = state.setdefault('followLabels', {})
            labels.pop(request, None)
            labels[request] = label
            for stale in list(labels)[:-FOLLOW_LABELS_KEPT]:
                labels.pop(stale)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass  # Only the row's name is lost; the follow still runs.


def followed(event, store, request):
    """True while a background follow of `request` runs, so its end will wake Claude for the relay."""
    tasks = event.get('background_tasks')
    if isinstance(tasks, list):  # Claude Code's own list of what can still wake this session.
        return any(isinstance(task, dict) and task.get('status') == 'running'
                   and ' follow --request ' + request in (task.get('command') or '') for task in tasks)
    import operations
    return operations.following(store, request)


def follow_plan(event, root, request):
    """How a turn waits for `request`: 'start' a follow, one is already 'running', or None (relay directly).

    A settled request needs no waiting (its relay returns at once), and without background tasks
    the relay command waits itself, as before.
    """
    if not background():
        return None
    try:
        import operations
        import route
        store = route.Store(event['session_id'], workspace(event), root)
        status = ((store.read().get('requests') or {}).get(request) or {}).get('status')
        if status not in ('captured', 'submitting'):
            return None
        return 'running' if operations.following(store, request) else 'start'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def widens_access(event, rest, root):
    """True when a `tune --phase access` or an access-menu `choose` would give the agent wider access than it has.

    `/cli access allow` and the settings access menu reach the controller as these commands, not as
    `activate --access`, so the target level comes from the saved turn: the typed choice, or the menu row.
    Unreadable state asks rather than allows.
    """
    try:
        import frontends
        import route
        from state import agent_entry
        store = route.Store(event['session_id'], workspace(event), root)
        state = store.read()
        pending = state.get('pending') or {}
        if rest[0] == 'tune':
            if rest[rest.index('--phase') + 1:][:1] != ['access']:
                return False
            choice = ((state.get('turnRoute') or {}).get('choice') or '').strip()
            if not choice:
                return False  # No choice typed: it only opens the access menu.
            target = agent_entry(state, (state.get('turnRoute') or {}).get('session')) or {}
            agent = target.get('backend') or pending.get('backend')
            options = frontends.phase_options(store.root, agent, 'access', target.get('settings'))
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
        # A setting change compares with that agent's access; a new agent starts from Prompt.
        session = ((state.get('turnRoute') or {}).get('session') or state.get('main') if rest[0] == 'tune'
                   else pending.get('session'))
        entry = agent_entry(state, session) if state.get('active') and session else None
        current = ((entry or {}).get('settings') or {}).get('access') if entry else 'prompt'
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
            if not state['active'] or turn.get('route') not in RELAY_ROUTES or not turn.get('requestId'):
                return {}
            # A prompt sent to several agents is one request each: the first that still needs its follow or
            # relay gets the nudge.
            ids = turn.get('requestIds') or [turn['requestId']]
            for request in ids:
                if ((state.get('relayProgress') or {}).get(request) or {}).get('done'):
                    continue
                requests, cursor = relay_position(state, request)
                waiting = background() and (state['requests'].get(requests[-1]) or {}).get('status') in (
                    'captured', 'submitting')
                if waiting and followed(event, store, requests[-1]):
                    continue  # The follow's end wakes Claude, and that turn runs the relay.
                count = (state.get('relayNudges') or {}).get(request, 0)
                if count >= MAX_NUDGES:
                    continue
                nudges = {key: value for key, value in (state.get('relayNudges') or {}).items() if key in ids}
                state['relayNudges'] = dict(nudges, **{request: count + 1})  # Only this turn's requests.
                break
            else:
                return {}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {}  # A turn end is never blocked by unreadable state.
    if waiting:
        return context(event, 'CLI-MODE request ' + requests[-1] + ' is still with the agent, and nothing follows '
                              'it: its output reaches the user only through the relay after it ends. Its follow '
                              'command `' + command(event, root, 'follow', '--request', requests[-1]) + '` runs in '
                              'the background (CLI-MODE makes it a background task), and then the turn ends; the '
                              'follow\'s end starts a new turn for the relay command `' +
                       command(event, root, *relay_words(requests, cursor)) + '`.')
    return context(event, 'CLI-MODE request ' + request + ' has not finished relaying; the agent\'s remaining '
                          'output reaches the user only through it. Its next relay command is `' +
                   command(event, root, *relay_words(requests, cursor, always_cursor=True)) + '`.')


def relay_position(state, request):
    """The requests and stream cursor that continue `request`'s relay, with any earlier requests it carries."""
    progress = (state.get('relayProgress') or {}).get(request) or {}
    chain = progress.get('chain')
    if chain:
        return chain['requests'], chain['cursor']
    earlier = unrelayed(state, exclude=request, session=((state.get('requests') or {}).get(request) or {}).get('session'))
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
    """The adapter of the agent this turn is about: named, being set up, or the current one."""
    import adapters
    from state import agent_entry
    named = agent_entry(state, decision.get('session')) if decision.get('session') else None
    agent = (decision.get('agent') or (named or {}).get('backend') or (state.get('pending') or {}).get('backend')
             or state.get('backend') or 'agy')
    try:
        return adapters.module(agent)
    except ValueError:
        return adapters.module('agy')


def name_of(state, decision):
    """How this turn's agent is named in CLI-MODE's lines: `Codex COD-7K` once it runs."""
    from state import agent_label
    return agent_label(state, decision.get('session')) if state.get('active') else label_of(state, decision).LABEL


def named(decision):
    return ['--name', decision['name']] if decision.get('name') else []


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
        return instant(event, root, 'settings', *(['--dismiss'] if kind == 'settings-dismiss' else named(decision)))
    if kind == 'close':
        return instant(event, root, 'close', *named(decision), render=presentation.close_text)
    if kind == 'close-menu':
        return instant(event, root, 'close', render=presentation.close_text)
    if kind == 'use':
        return instant(event, root, 'use', *named(decision))
    if kind == 'agents':
        return instant(event, root, 'agents', *(['--max', str(decision['max'])] if decision.get('max') else []))
    if kind in ('diff', 'dir', 'undo', 'usage'):
        return instant(event, root, kind, *named(decision), render=lambda result: result['text'])
    if kind == 'test':  # Your own text: passed as one argument, never through a shell line.
        return instant(event, root, 'test', *(['--command=' + decision['command']] if decision.get('command') else []),
                       render=lambda result: result['text'])
    if kind == 'brief':
        return instant(event, root, 'brief', '--action', decision['action'],
                       *(['--text=' + decision['text']] if decision.get('text') else []),
                       render=lambda result: result['text'])
    if kind == 'timeout':
        return instant(event, root, 'timeout', *named(decision),
                       *(['--minutes', str(decision['minutes'])] if decision.get('minutes') else []))
    if kind == 'attach':
        return instant(event, root, 'attach', *(['--target', decision['target']] if decision.get('target') else []))
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
        note = ('Cancel requested for ' + name_of(state, decision) + '\'s running turn; queued follow-ups still run.'
                if cancellation and cancellation.get('canceled') else 'No agent turn was running; the queue is unchanged.')
        return instant(event, root, 'queue', render=lambda result: note + '\n\n' + presentation.queue_text(result))
    if kind == 'choose':
        words = ('choose', str(decision['number']))
        if pending.get('phase') == 'access' and widens_access(event, list(words), root):
            return activation(event, root, adapter, 'applies the chosen access level and activates ' +
                              adapter.DISPLAY_NAME, *words)
        return instant(event, root, *words)  # The access list's choice activates here (see activation()).
    if kind == 'tune':
        words = ('tune', '--phase', decision['phase'], '--apply', *named(decision))
        if decision.get('text') and widens_access(event, list(words), root):
            return activation(event, root, adapter, 'applies "' + decision['text'] + '" as ' + name_of(state, decision) +
                              '\'s ' + decision['phase'] + ' when it matches one advertised option exactly (otherwise '
                              'its result is the options menu, with a message)', *words)
        return instant(event, root, *words)
    if kind == 'bind':
        if pending.get('stage') == 'verifying':
            return show_text(event, 'Activation is already being verified. /cli queue shows its progress.')
        return instant(event, root, 'bind', '--agent', adapter.ID, *named(decision))
    if kind == 'setup':
        return setup_reply(event, root, state, adapter, event.get('prompt', ''))
    if kind == 'direct' and len(decision.get('requestIds') or []) > 1:
        return context(event, several_context(event, root, state, decision, worker))
    if kind == 'direct':
        request = decision.get('requestId')
        if not request:
            return show_text(event, 'CLI-MODE: no captured request. Send the message again.')
        label = name_of(state, decision)
        if background():
            remember_label(event, root, request, label, decision.get('named', False))
        lead = ('CLI-MODE forwarded this message (without its /d trigger' +
                (' and the agent name after it' if decision.get('named') else '') + ') unchanged to ' + label +
                '. Only its text was forwarded: images or files '
                'attached to it stay with Claude Code, so if it had any, one short line saying the agent did not '
                'receive them follows the opening line below, in the same message.')
        earlier = unrelayed(state, exclude=request, session=(state['requests'].get(request) or {}).get('session'))
        if earlier:
            lead += (' The relay also carries earlier requests whose output the user has not seen yet (a relay was '
                     'interrupted), first and in order; they are not sent again.')
        return context(event, relay_context(event, root, adapter, earlier + [request], lead, worker, passing=True,
                                            label=label))
    if kind == 'resume':
        return resume_reply(event, root, adapter, worker, state)
    return {}


def unrelayed(state, exclude=None, limit=3, session=None):
    """This activation's requests whose relay never finished: their output has not reached the user.

    With `session`, only that agent's (a turn's relay carries its own agent's earlier requests); otherwise
    every running agent's.
    """
    progress = state.get('relayProgress') or {}
    sessions = {session} if session else {item['name'] for item in state.get('owned') or []}
    rows = [(record.get('capturedAt') or 0, key) for key, record in (state.get('requests') or {}).items()
            if key != exclude and record.get('generation') == state.get('generation')
            and record.get('session') in sessions
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
    """An activation that widens the agent's access: Claude runs it once, so Claude Code asks the user first.

    Every other activation (bind, the activation menu, a narrower or equal setting) runs in this hook
    through instant(): it takes 13-42 s while the agent starts and answers one readiness prompt, and as a
    command Claude runs, it would be a row of its own in the desktop app's background tasks, next to the
    agent's rows. Here the prompt shows the hook's status line meanwhile (its timeout allows 300 s).
    """
    return context(event, (
        'CLI-MODE: this control ' + what + '. Its command is `' + command(event, root, *words) + '`. It checks '
        'readiness and sends the agent one short readiness prompt, which can take a minute or two, so it runs once, '
        'with a timeout of ' + str(ACTIVATION_TIMEOUT_MS) + ' ms, and is not polled or retried. Its JSON result '
        'carries `activation.text`, the confirmation the user expects ' + ALONE + '; or else a menu '
        '(`activationMenu`, with any `message`) shown the same way.' + ERROR + ' An error says what setup still '
        'needs; installing or signing in happens only when the user asks for it. ' + host_note_rule() + COMPLETE))


def host_note_rule():
    import presentation
    return presentation.HOST_NOTE_RULE


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
            return instant(event, root, 'activate', '--agent', agent)
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


def relay_context(event, root, adapter, requests, lead, worker=None, cursor=0, passing=False, label=None):
    """The facts Claude needs to relay agent output, and how the user expects to see it.

    Claude Code's desktop app folds a turn's text between tool calls out of view (the user saw "Passing to"
    only inside a collapsed group), so the opening line comes before any command and the agent's output is
    the turn's last message, with nothing posted in between.

    With background tasks, an unfinished request is not waited on in the turn: a background `follow`
    shows the agent's work as a row in Claude Code's background tasks, the turn ends, and the follow's
    end wakes Claude for one relay, which returns at once. Without them, the relay command waits itself.
    """
    from state import passing_line
    label = label or adapter.LABEL
    opening = ''
    if passing:
        import presentation
        # The line is its text block's last text while the relay runs, and the desktop app keeps a "$" at the
        # very end of a still-streaming block as plain text (it may become "$$"): a zero-width space after it
        # lets the green line render at once rather than when the turn ends.
        line = presentation.strong(passing_line(label), COLOR) + ('​' if COLOR else '')
        opening = (' The turn opens with this line, exactly as written on the next line, posted before any '
                   'command:\n' + line + '\n')
    relay = '`' + command(event, root, *relay_words(requests, cursor)) + '`' + (
        ', which covers these ' + str(len(requests)) + ' requests in order: its final part carries each one\'s '
        'output, oldest first' if len(requests) > 1 else '')
    plan = follow_plan(event, root, requests[-1])
    if plan == 'start' and passing:
        # Claude Code asks a turn that ends on a tool call for visible output (live run 4: "Your previous response
        # had no visible output"), which pushed Claude to post or run more after the follow. So the Passing line
        # comes last, where the desktop app also keeps it open: only text between tool calls is folded away.
        opening = ''
    if plan is None:
        waiting = (' The agent\'s output reaches the user only through the relay command ' + relay + '. It runs in '
                   'the Bash or PowerShell tool with a timeout of ' + str(RELAY_TIMEOUT_MS) + ' ms. Each call waits '
                   'up to 25 seconds and prints plain text. While the agent is still working, that is one line with '
                   'the `--cursor` for the next call, and the user expects the same command run again at once, with '
                   'no sleep and no text between calls (the app folds text between tool calls out of view). Once the '
                   'agent has finished, a first line says so, and everything after it is the agent\'s output, posted '
                   'exactly as printed as the last message of the turn.')
    else:
        if plan == 'running':
            now = (' A follow of this request is already running in the background, so no other is started; this '
                   'turn only says, in one line, that CLI-MODE is still following ' + label + '.')
        else:
            now = (' ' + label + ' works in the background, and the user watches it as a row in Claude '
                   'Code\'s background tasks: this turn first runs its follow command `' +
                   command(event, root, 'follow', '--request', requests[-1]) + '` once (CLI-MODE makes it a '
                   'background task), then ends with ' + (
                       'exactly this line, as written on the next line, as its only message:\n' + line + '\n'
                       if passing else 'one line saying CLI-MODE is following ' + label + ' as its only message. ') +
                   'The follow\'s end wakes this conversation by itself, so nothing else is needed to wait for it: '
                   'this turn uses no other tool of any kind (no echo, sleep, check, wake-up, reminder, schedule or '
                   'monitor) and posts nothing else, because the row already shows the agent working.')
        waiting = now + (
            ' The agent\'s output reaches the user only through the relay command ' + relay + ', run when the '
            'follow ends: its task notification starts a new turn, which runs the relay in the Bash or PowerShell '
            'tool with a timeout of ' + str(RELAY_TIMEOUT_MS) + ' ms, whatever the follow\'s exit code. The relay '
            'prints plain text: a first line saying the agent has finished, and everything after it is the agent\'s '
            'output, posted exactly as printed as the last message of that turn. (If it says the agent is still '
            'working, the same command runs again at once with the `--cursor` it names.)')
    text = lead + opening + waiting + (
             ' (A very long answer comes in parts: a first line saying '
             'so means that part is posted exactly before the next call.) Relayed text carries nothing added: no '
             'summary, commentary, rewording or insight blocks, whatever the output style. The task belongs to ' +
             label + ', which plans and runs it with its own tools and subagents, so it is not answered, '
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


def several_context(event, root, state, decision, worker=None):
    """One prompt sent to several agents: a background follow each, then one Passing line naming them all.

    Each follow's end wakes Claude for that agent's relay alone (notification_reply), so the answers arrive one
    by one, each under its own name. Without background tasks, one relay command waits for them all.
    """
    import presentation
    from queue_worker import request_label
    from state import passing_line
    requests = decision['requestIds']
    fresh = state.get('requests') or {}
    labels = [request_label(state, request) for request in requests]
    if background():
        for request, label in zip(requests, labels):
            remember_label(event, root, request, label, decision.get('named', 0))
    together = ', '.join(labels[:-1]) + ' and ' + labels[-1]
    lead = ('CLI-MODE forwarded this message (without its /d trigger and the agent names after it) unchanged to ' +
            together + ', each as its own request in its own agent\'s queue; they work at the same time. Only its '
            'text was forwarded: images or files attached to it stay with Claude Code, so if it had any, one short '
            'line saying the agents did not receive them goes before the line below, in the same message.')
    plans = [follow_plan(event, root, request) for request in requests]
    if any(plan != 'start' for plan in plans) or not all(request in fresh for request in requests):
        adapter = label_of(state, dict(session=(fresh.get(requests[-1]) or {}).get('session')))
        return relay_context(event, root, adapter, requests, lead, worker, passing=True, label=together)
    line = presentation.strong(passing_line(together), COLOR) + ('\u200b' if COLOR else '')
    follows = ', '.join('`' + command(event, root, 'follow', '--request', request) + '`' for request in requests)
    text = (lead + ' Each agent works in the background and the user watches each as its own row in Claude Code\'s '
            'background tasks: this turn first runs these follow commands, once each and in this order: ' + follows +
            ' (CLI-MODE makes each a background task), then ends with exactly this line, as written on the next '
            'line, as its only message:\n' + line + '\nEach follow\'s end wakes this conversation by itself, with '
            'that agent\'s relay command, so nothing else is needed to wait for them: this turn uses no other tool of '
            'any kind (no echo, sleep, check, wake-up, reminder, schedule or monitor) and posts nothing else. Each '
            'relay prints plain text: a first line saying the agent has finished, and everything after it is that '
            'agent\'s output, posted exactly as printed as the last message of its turn. Relayed text carries '
            'nothing added: no summary, commentary, comparison, rewording or insight blocks, whatever the output '
            'style. The task belongs to the agents, which plan and run it with their own tools and subagents, so it '
            'is not answered, planned or split here and the Agent tool stays unused.' + COMPLETE)
    if worker and worker.get('worker') in ('blocked', 'start-failed'):
        text += (' A queue worker needs attention (' + json.dumps(worker) + '); /cli queue shows the blocked '
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
    lead = ('CLI-MODE reattached to the agents\' captured work; these requests are not sent to the agent again, only '
            'monitored.')
    from state import agent_label
    return context(event, relay_context(event, root, adapter, requests, lead, worker, label=agent_label(state)))


def session_start(event, root, state, decision):
    """After compaction, restore what the last turn was doing; otherwise nothing is needed."""
    if event.get('source') != 'compact':
        return {}
    kind = decision.get('route')
    adapter = label_of(state, decision)
    request = decision.get('requestId')
    if kind in ('direct', 'direct-result') and request and state['active']:
        if ((state.get('relayProgress') or {}).get(request) or {}).get('done'):
            return {}
        from queue_worker import request_label
        label = request_label(state, request)
        lead = ('The conversation was compacted while CLI-MODE was relaying request ' + request + ' from ' +
                label + '; the request is not sent again, and relaying continues from its saved cursor.')
        requests, cursor = relay_position(state, request)
        return context(event, relay_context(event, root, adapter, requests, lead, cursor=cursor, label=label))
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
