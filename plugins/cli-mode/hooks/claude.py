"""Claude Code hook: route prompts, approve CLI-MODE's own commands, keep relays going.

Claude Code runs this file for SessionStart, UserPromptSubmit, PreToolUse (the
Agent and scheduling tools, shell commands that start with `python`, and file
edits) and Stop. The shared route.decide() records what each prompt is, exactly as for
Codex. This file decides how Claude Code shows it:

- Local controls (menus, help, queue, stop) and activation answer in the hook,
  without a command for Claude: the hook runs the controller in process.
- A /d turn posts "Passing to ...", starts a background `follow` and ends; the
  follow's notification wakes Claude for one relay. Installing and widening
  access stay commands Claude runs, so Claude Code asks first.
- CLI-MODE's own controller commands for this session are approved; nothing
  else is.
- A relay that Claude ends early is resumed from its saved cursor.
- AUTO: each of Claude's own turns carries the rule and ledger; its task files
  are approved, and its larger edits and coding subagents refused by strength;
  a handoff's follow wakes it to read the result (relay --for-host).

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
    edit = '"PreToolUse"' in raw and any('"' + tool + '"' in raw for tool in EDIT_TOOLS)  # Confirmed below.
    if '"PreToolUse"' in raw and not edit and 'controller.py' not in raw and not any(
            '"' + tool + '"' in raw for tool in AGENT_TURN_TOOLS):
        return True  # Some other `python` command: only CLI-MODE's own controller is approved here.
    if '"SessionStart"' in raw and '"compact"' not in raw:
        return True  # Only a compaction has a relay or menu to restore.
    import json
    try:
        event = json.loads(raw)
        name, session = event['hook_event_name'], event['session_id']
        edit = edit and event.get('tool_name') in EDIT_TOOLS
        root = os.environ.get('CLI_MODE_DATA') or os.environ.get('CLAUDE_PLUGIN_DATA')
        if name not in ('UserPromptSubmit', 'Stop', 'PreToolUse') or not root:
            return False
        if name == 'PreToolUse' and not edit and event.get('tool_name') not in AGENT_TURN_TOOLS:
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
        auto = saved.get('active') and saved.get('routingMode') == 'auto'
        if edit:
            # A file edit: the brief's host note, or in AUTO a task file or Claude's own project edit.
            return 'BRIEF.md' not in raw and not auto
        if saved.get('active'):
            # A prompt may be a /d task or a notification, a tool may need denying, a relay may be running; in AUTO
            # a turn's end may leave a handoff unfollowed or its result unread.
            return (name == 'Stop' and (saved.get('turnRoute') or {}).get('route') not in RELAY_ROUTES
                    and not auto)
        # Nothing is active: only an open menu, setup or help card can make a plain reply (such as X) CLI-MODE's.
        return not (any(saved.get(key) for key in ('pending', 'helpMenu')) or
                    (saved.get('turnRoute') or {}).get('route') == 'help')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False  # handle() reports what is wrong, as it always has.


RELAY_ROUTES = ('direct', 'direct-result')
# File-editing tools: the brief's host note, and in AUTO task files and the delegation strengths (claude/hooks.json).
EDIT_TOOLS = ('Edit', 'Write', 'NotebookEdit', 'MultiEdit')
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
    'catalog', 'handoff'))
# In AUTO (Claude Code), CLI-MODE runs the agents and the user changes them with /cli mode: Claude may run these
# only in a turn the user started with a CLI-MODE control (the hook's own instructions), never in its own turns.
AUTO_OWNED_COMMANDS = frozenset((
    'bind', 'activate', 'choose', 'navigate', 'settings', 'tune', 'close', 'use', 'timeout', 'attach', 'off',
    'progress', 'view', 'frontend', 'options', 'refresh', 'setup-start', 'setup-status', 'setup-manual',
    'first-time-check', 'activation-message'))
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
        lead = ('CLI-MODE answered this control itself; no command needs to run.' +
                (COMPLETE + ' Its reply is below, to be ' if not step else
                 ' ' + step + ' Then post its reply below as this turn\'s last message, '))
        return context(event, lead + ALONE + ':\n\n' + presentation.chat_menu(fenced, COLOR))
    if style == 'stop':
        return {'continue': False, 'stopReason': '\n' + plain}
    return {'decision': 'block', 'reason': plain, 'suppressOriginalPrompt': True}


def show_result(event, result):
    import presentation
    if isinstance(result.get('setup'), dict):
        result = result['setup']  # A finished install: the menu setup continues with, not the setup window's words.
    note = result.get('hostNote')
    if note and STYLE != 'model':
        # An instant reply has no model turn to write the host's note in: its waiting entry comes out again.
        import agent_folder
        try:
            agent_folder.drop_host_note(workspace(event), note)
        except KeyError:
            pass  # No project folder in the event: nothing to take out.
        note = None
    return show(event, presentation.result_text(result), presentation.result_text(result, fenced=False),
                step=presentation.host_note_step(note))


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
    if decision['route'] in ('help-invalid', 'mode-invalid'):
        # On Claude Code help and the Mode page are cards, not modes: any other message closes them and is handled
        # as usual, so a question typed after the card still reaches Claude (or the agent).
        with store.edit() as saved:
            if decision['route'] == 'help-invalid':
                saved['helpMenu'] = None
            else:
                saved['pending'] = None
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


def deny(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                   'permissionDecisionReason': reason}}


def auto_state(event, root):
    """This session's saved state when it is in AUTO, else None (never raises: a tool call is never blocked by it)."""
    try:
        import route
        from state import auto_on
        state = route.Store(event['session_id'], workspace(event), root).read()
        return state if auto_on(state) and state.get('active') else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def task_file_approval(event, root):
    """AUTO: allow Claude's Write of a task file, exactly in this project's task folder; a task file written anywhere
    else is refused with the right path (live P0: one went to `.cli-mode/tasks/` in the project root)."""
    request = event.get('tool_input') or {}
    path = str(request.get('file_path') or '')
    if '/.cli-mode/tasks/' not in path.replace('\\', '/'):
        return None
    state = auto_state(event, root)
    if state is None:
        return None  # Not AUTO: Claude Code's own permissions decide.
    import auto_mode
    folder = auto_mode.tasks_dir(workspace(event))
    try:
        target = Path(os.path.realpath(path))
        right = os.path.normcase(str(target.parent)) == os.path.normcase(os.path.realpath(folder))
    except (OSError, ValueError):
        right = False
    if not right or target.suffix != '.md' or not auto_mode.TASK_ID.fullmatch(target.stem):
        return deny('CLI-MODE AUTO: a task file goes in ' + folder.as_posix() + '/<id>.md, where <id> is 1-40 '
                    'letters, digits, _ or - (such as t1). Write it there, then run the handoff.')
    if len(request.get('content') or '') > auto_mode.TASK_MAX:
        return deny('CLI-MODE AUTO: that task is over ' + str(auto_mode.TASK_MAX // 1024) + ' KB. Keep it to what '
                    'the agent needs; it can read the project itself.')
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                   'permissionDecisionReason': 'CLI-MODE AUTO: a task file for the AUTO agent.'}}


def auto_owned(event, root):
    """In AUTO, a command that changes which agent does what is refused in Claude's own turns: CLI-MODE runs the
    agents, and the user changes them. A turn the user started with a CLI-MODE control runs its commands as usual."""
    state = auto_state(event, root)
    if state is None or ((state.get('turnRoute') or {}).get('route') or 'host') not in ('host', 'restore'):
        return None
    return deny('CLI-MODE AUTO is on: CLI-MODE runs the agents, and the user changes them (/cli mode, or /cli mode '
                'direct to drive them). Hand work to the AUTO agent with the handoff command instead.')


def pre_tool_use(event, root):
    tool = event.get('tool_name', '')
    if tool in EDIT_TOOLS:
        return ((brief_note_approval(event) if tool == 'Edit' else None) or
                (task_file_approval(event, root) if tool == 'Write' else None) or strength_refusal(event, root) or {})
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
            return auto_tool_refusal(event, root, tool, state) or {}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass  # Unreadable state never blocks a tool here.
    return {}


def auto_tool_refusal(event, root, tool, state):
    """AUTO: at Strong and Max, Claude's own subagents that would write code go to the AUTO agent instead (lever C;
    Explore and Plan stay Claude's); and while a handoff runs, nothing waits or polls for it: its follow wakes Claude."""
    from state import auto_on
    if not auto_on(state) or not state.get('active'):
        return None
    import auto_mode
    if tool == 'Agent':
        if (state.get('turnRoute') or {}).get('route') == 'auto-host':
            return None  # The user asked Claude itself (/d): its own subagents are its business.
        kind = (event.get('tool_input') or {}).get('subagent_type') or 'general-purpose'
        if auto_mode.load(data_root(root))['strength'] in ('strong', 'max') and kind not in auto_mode.KEPT_SUBAGENTS:
            return deny('CLI-MODE AUTO: coding work goes to the AUTO agent, not a Claude subagent. Put it in a task file '
                        'and hand it over with the handoff command; Explore and Plan stay yours for quick reads.')
        return None
    if any(record.get('status') in auto_mode.WORKING for _, record in auto_mode.auto_requests(state)):
        return deny('CLI-MODE AUTO: a handoff is with the agent, and its background follow wakes you when it finishes, '
                    'so nothing needs to wait, schedule or watch for it. Carry on, or end the turn.')
    return None


def edit_size(request):
    """The lines one edit changes: the larger side of each replacement, or a written file's length."""
    def lines(text):
        return len(str(text or '').splitlines()) or (1 if text else 0)
    if isinstance(request.get('edits'), list):  # MultiEdit.
        return sum(max(lines(item.get('old_string')), lines(item.get('new_string')))
                   for item in request['edits'] if isinstance(item, dict))
    return max(lines(request.get('old_string')), lines(request.get('new_string')), lines(request.get('content')),
               lines(request.get('new_source')))


def strength_refusal(event, root):
    """AUTO: Claude's own edits of project files (lever B). None of a file a running task may change (one writer per
    file); at Strong, small fixes only (SMALL_EDIT lines an edit, TURN_FILES files a turn); at Max, none. Files outside
    the project, and the agents' working folder (task files, the brief), are not the project's code and stay Claude's."""
    state = auto_state(event, root)
    if state is None:
        return None
    import agent_folder
    import auto_mode
    from state import agent_label
    request = event.get('tool_input') or {}
    path = str(request.get('file_path') or request.get('notebook_path') or '')
    if not path:
        return None
    try:
        relative = Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(workspace(event))))
    except (OSError, ValueError):
        return None  # Outside the project: Claude's own notes and memory.
    if relative.parts[:1] == (agent_folder.ROOT,):
        return None
    busy = auto_mode.conflict(state, [auto_mode.file_key(relative.as_posix())])
    if busy:
        what = 'files anywhere in this project' if busy[2] == auto_mode.WHOLE else busy[2]
        return deny('CLI-MODE AUTO: ' + agent_label(state, busy[0]) + ' may be changing ' + what + ' right now (one '
                    'writer per file). Make this edit after its result, or put it in a task for that agent.')
    if (state.get('turnRoute') or {}).get('route') == 'auto-host':
        return None  # The user asked Claude itself (/d): no delegation strength this turn.
    strength = auto_mode.load(data_root(root))['strength']
    if strength == 'max':
        return deny('CLI-MODE AUTO (Max): project changes go to the AUTO agent. Put this in a task file and hand it '
                    'over with the handoff command.')
    if strength != 'strong':
        return None
    changed = edit_size(request)
    if changed > auto_mode.SMALL_EDIT:
        return deny('CLI-MODE AUTO (Strong): this edit changes about ' + str(changed) + ' lines, more than a small '
                    'fix. Put it in a task file and hand it to the AUTO agent.')
    import route
    key = os.path.normcase(relative.as_posix())
    try:
        with route.Store(event['session_id'], workspace(event), root).edit() as saved:
            turn = (saved.get('turnRoute') or {}).get('id')
            record = saved.get('autoTurnFiles') or {}
            files = list(record.get('files') or []) if record.get('turn') == turn else []
            if key not in files:
                if len(files) >= auto_mode.TURN_FILES:
                    return deny('CLI-MODE AUTO (Strong): another project file in this turn is more than a small fix. '
                                'Hand the rest to the AUTO agent in a task file.')
                saved['autoTurnFiles'] = dict(turn=turn, files=files + [key])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None
    return None


AUTO_RULE_REFRESH = 30  # AUTO turns after which the whole rule is sent again: lever L1's safety net for long talks.


def auto_context(event, root, state):
    """What Claude is told about AUTO this turn (lever A), made light (L1): the whole rule on the conversation's
    first AUTO turn, again when it changes, after a compaction and every AUTO_RULE_REFRESH turns; otherwise only a
    changed status (agents, work still running or unread), or nothing. None for nothing: the rule sent before
    stays in the conversation, and Claude Code keeps it there."""
    import auto_mode
    import route
    rule = auto_mode.rule(data_root(root), state, workspace(event), handoff_command(event, root), attribution)
    if rule is None:
        return auto_mode.NOT_RUNNING
    status = auto_mode.status(state, lambda request: command(event, root, 'relay', '--request', request,
                                                            '--for-host'))
    digests = {key: hashlib.sha1(text.encode('utf-8')).hexdigest()[:16] for key, text in (('rule', rule),
                                                                                         ('status', status))}
    try:
        with route.Store(event['session_id'], workspace(event), root).edit() as saved:
            sent = saved.get('autoRule') or {}
            turns = int(sent.get('turns') or 0) + 1
            whole = sent.get('rule') != digests['rule'] or turns >= AUTO_RULE_REFRESH
            changed = sent.get('status') != digests['status']
            saved['autoRule'] = dict(digests, turns=0 if whole else turns)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        whole = changed = True  # Unsure what was sent: send it all.
    if whole:
        return rule + '\n' + status
    if changed:
        return 'CLI-MODE AUTO: the rule given earlier in this conversation still applies. ' + status
    return None


def saved_prompt(event):
    """AUTO: a long prompt is saved where the agents can read it, and Claude is told where (pass by reference);
    None for a short one, or when it can't be saved (the turn goes on without it)."""
    import auto_mode
    text = event.get('prompt') or ''
    if len(text) < auto_mode.PROMPT_SAVE_MIN:
        return None
    try:
        return auto_mode.prompt_note(auto_mode.save_prompt(workspace(event), text), text)
    except OSError:
        return None


def attribution(text):
    """An AUTO attribution line, marked as DIRECT's "Passing to" line is: green bold (plain bold with /cli color off),
    with a zero-width space after it so a line that ends a still-streaming block renders at once."""
    import presentation
    return presentation.strong(text, COLOR) + ('​' if COLOR else '')


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
    short = tokens[:2] == expected[:2] and tokens[2:3] == ['handoff']  # AUTO's `python <controller> handoff …`.
    if short:
        rest = tokens[2:]
    if ((tokens[:len(expected)] != expected and not short) or not rest or rest[0] not in ALLOWED
            or not all(ARGUMENT.fullmatch(token) for token in rest)
            or any("'" in token for token in tokens)
            or ' '.join(quote(token) for token in tokens) != text):
        return None
    if rest[0] == 'handoff' and ((auto_state(event, root) or {}).get('turnRoute') or {}).get('route') == 'auto-host':
        return deny('CLI-MODE AUTO: the user asked you directly (/d), so nothing from this turn goes to an agent. '
                    'Answer or do it yourself.')
    if rest[0] in AUTO_OWNED_COMMANDS:
        refused = auto_owned(event, root)
        if refused:
            return refused
    if rest[0] == 'handoff':
        handed = handoff_approval(event, root, rest)
        if handed:
            return handed
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


def handoff_command(event, root):
    """The handoff command Claude runs in AUTO, short: this hook knows the session, so the command it turns it into
    carries the rest (handoff_approval)."""
    return ' '.join(quote(token) for token in ['python', CONTROLLER, 'handoff', '--task']) + ' <id>'


def handoff_approval(event, root, rest):
    """AUTO's one-call handoff (lever L2): this hook hands the task over itself, and the same tool call becomes the
    agent's background follow, one row. A refusal comes back at once, as this call's denial. A new agent
    (`--agent new`, 15-40 s to start: too long for a hook) is checked here and started by that background task,
    under a request id chosen here so its end still wakes Claude. None for those background forms themselves."""
    import uuid
    import controller as control
    try:
        args = control.build_parser().parse_args(base(event, root)[2:] + list(rest))
    except SystemExit:
        return deny('CLI-MODE AUTO: that handoff command is incomplete. Run `' + handoff_command(event, root) +
                    '` (with --read-only or --agent <name> if needed).')
    if args.check or args.request or args.follow:
        return None
    extra = ['--read-only'] if args.read_only else []
    try:
        if (args.agent or '').casefold() == 'new':
            label = controller(event, root, 'handoff', '--task', args.task, '--agent', 'new', '--check', *extra)['label']
            request = uuid.uuid4().hex
            words = ['handoff', '--task', args.task, '--agent', 'new', '--request', request, '--follow'] + extra
        else:
            result = controller(event, root, *rest)
            request, label, words = result['requestId'], result['label'], ['follow', '--request', result['requestId']]
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return deny('CLI-MODE AUTO: ' + str(exc))
    remember_follow(event, root, request)
    return {'hookSpecificOutput': {
        'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
        'permissionDecisionReason': 'CLI-MODE AUTO handed the task over and follows the agent in the background.',
        'updatedInput': dict(event.get('tool_input') or {}, command=command(event, root, *words),
                             run_in_background=True, description=label)}}


# The only controller commands that can run past the desktop app's row threshold (about 2-3 s) as a command
# Claude runs: an activation that widens access (13-42 s), and the relay loop without background tasks (25 s).
ROW_LABELS = {'bind': 'starting', 'activate': 'starting', 'choose': 'starting', 'tune': 'starting', 'relay': 'answer'}


def labelled(event, root, rest, output):
    """Name the command's row, should it get one, after the agent rather than Claude's own words."""
    if rest[0] == 'handoff' and '--agent' in rest and rest[rest.index('--agent') + 1:][:1] == ['new']:
        try:  # AUTO: another agent like the AUTO agent starts first (15-40 s).
            import adapters
            import route
            from state import agent_entry
            state = route.Store(event['session_id'], workspace(event), root).read()
            lead = agent_entry(state, (state.get('auto') or {}).get('agent')) or {}
            output['updatedInput'] = dict(event.get('tool_input') or {}, description=adapters.module(
                lead.get('backend') or 'agy').LABEL + ' · starting')
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
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
    records = state.get('requests') or {}
    found = list(dict.fromkeys(follows[item] for item in TOOL_USE_ID.findall(event.get('prompt', ''))
                               if item in follows))
    failed = [item for item in found if item not in records and item in (state.get('handoffErrors') or {})]
    if failed and state.get('active'):
        return context(event, 'CLI-MODE AUTO: the handoff to a new agent did not go through, so nothing was handed '
                              'off: ' + state['handoffErrors'][failed[0]] + ' Tell the user in one line, then hand the '
                              'task to a running agent (`--agent <name>`) or try again.')
    request = next(iter(found), None)
    if not request or not state.get('active') or request not in records:
        return {}  # Claude's own background work, or an agent no longer active: not CLI-MODE's to answer.
    from queue_worker import request_label
    label = request_label(state, request)
    if records[request].get('routingMode') == 'auto':
        return auto_wake(event, root, [key for key in found if (records.get(key) or {}).get('routingMode') == 'auto'],
                         state)
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


WAKE_ROOM = 8400  # Characters of results one wake-up carries, under Claude Code's 10,000 with its instructions.
WAKE_LINES = 900  # About what a result's lines take besides the agent's answer.
HOOK_TEXT_MAX = 9800


def auto_wake(event, root, requests, state):
    """AUTO: handoffs' follows ended (lever L3). Their results come with the wake-up itself, read here and marked read,
    so Claude checks and reports without a relay call; results finishing together share its room. Should that fail,
    the wake-up names the relay to run instead, and the Stop guard holds the turn until it has run (autoWake)."""
    import route
    from controller import Controller
    from queue_worker import request_label
    import relay_view
    records = state.get('requests') or {}
    fresh = [key for key in requests if not (records.get(key) or {}).get('hostRead')]
    if not fresh:
        ids = TOOL_USE_ID.findall(event.get('prompt', ''))
        if ids and all(item in (state.get('followTasks') or {}) for item in ids):
            # Only CLI-MODE's follows, their results already reported with the rest of their batch: no turn at all,
            # which would only re-read the whole conversation to say nothing.
            return {'decision': 'block', 'suppressOriginalPrompt': True,
                    'reason': 'CLI-MODE: ' + ' and '.join(request_label(state, key) for key in requests) +
                              ' finished; reported with the rest of its handoffs.'}
        return context(event, 'CLI-MODE: this notification ends the follow of handoff ' + requests[0] + ', whose '
                       'result you have already read. Nothing more is needed for it.' + COMPLETE)
    # The rest of their batch that has also finished comes along, so one wake-up reports them all.
    from queue_worker import QueueMixin
    captured = [(records.get(key) or {}).get('capturedAt') or 0 for key in fresh]
    fresh += [key for key, other in records.items() if key not in fresh and other.get('routingMode') == 'auto'
              and other.get('handoff') and not other.get('hostRead')
              and other.get('status') not in ('captured', 'submitting')
              and any(abs((other.get('capturedAt') or 0) - at) <= QueueMixin.BATCH_WINDOW for at in captured)]
    room = min(relay_view.HOST_ANSWER_MAX, max(500, WAKE_ROOM // len(fresh) - WAKE_LINES))
    control = Controller(route.Store(event['session_id'], workspace(event), root))
    done = []
    for key in fresh:
        try:
            result = control.relay_for_host(key, wait=5.0, answer_max=room)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            continue
        if result.get('done'):
            done.append(result)
    waiting = [key for key in fresh if key not in {item['requestId'] for item in done}]
    text = ''
    if done:
        labels = [request_label(state, item['requestId']) for item in done]
        one = len(done) == 1
        text = ('CLI-MODE: this notification ends the follow of handoff ' +
                ', '.join(item['requestId'] for item in done) + ', so ' + ' and '.join(labels) +
                (' has' if one else ' have') + ' finished. ' + ('Its result is' if one else 'Their results are') +
                ' below, already read for you (there is no relay to run); it is for you, not the user. Open your '
                'report with ' + ('this attribution' if one else 'these attributions') + ', exactly as written:\n' +
                '\n'.join(attribution(label + ' finished.') for label in labels) + '\nThen tell the user in a few '
                'lines, in your own words, what was done, whether it held up, and anything unresolved or waiting on '
                'them; do not post the result as is. CHECK: ok means CLI-MODE found nothing to look into: report from '
                'the result alone, with no files to open and no commands to run unless the user asked. The CHANGES '
                'line is CLI-MODE\'s own git status and diff across the agent\'s turn (new files included) and a TESTS '
                'line its own run of the project\'s tests after it: never run git or the tests again to confirm them. '
                'CHECK: look '
                'names what to check: check only that. If it needs more work, hand a follow-up task to the agent the '
                'same way. The CLI-MODE skill and its guides are not needed.\n\n' +
                '\n\n'.join(item['text'] for item in done))
        if len(text) > HOOK_TEXT_MAX:
            text = (text[:HOOK_TEXT_MAX - 120].rstrip() + '\n[Cut to fit the wake-up: each whole answer is in the '
                    'answer file its ANSWER line names.]')
    if not waiting:
        return context(event, text)
    # Fallback: a result that could not be read here is read with its relay; the Stop guard holds the turn for it.
    request = waiting[0]
    label = request_label(state, request)
    try:
        with route.Store(event['session_id'], workspace(event), root).edit() as saved:
            saved['autoWake'] = request
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass  # The context still says what to run.
    return context(event, (text + '\n\n' if text else '') + (
        'CLI-MODE: this notification ends the follow of handoff ' + request + ', so ' + label + ' has finished. Its '
        'result is for you, not the user: run `' + command(event, root, 'relay', '--request', request, '--for-host') +
        '` once (Bash or PowerShell, timeout ' + str(RELAY_TIMEOUT_MS) + ' ms) and read what it prints. Then check the '
        'work against the task: the change receipt, the test result and the agent\'s report (open a changed file or '
        'run a check only where something needs confirming). Open your report with this attribution, exactly as '
        'written:\n' + attribution(label + ' finished.') + '\nThen tell the user in your own words what was done, whether '
        'it held up, and anything unresolved or waiting on them; do not post the result as is. If it needs more work, '
        'hand a follow-up task to the agent the same way.' + COMPLETE))


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
        # A follow, or AUTO's handoff to a new agent, which follows once the agent has started (handoff_approval).
        return any(isinstance(task, dict) and task.get('status') == 'running'
                   and (' follow --request ' + request in (task.get('command') or '') or
                        ' --request ' + request + ' --follow' in (task.get('command') or '')) for task in tasks)
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
        if saved.get('active') and saved.get('routingMode') == 'auto':
            nudge = auto_stop(event, root)
            if nudge:
                return nudge
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


def auto_stop(event, root):
    """AUTO: a turn ends with a handoff nothing follows (so nothing would wake Claude), or with a finished handoff's
    result unread (autoWake). Say which, up to MAX_NUDGES times each; otherwise the turn ends."""
    import auto_mode
    import route
    store = route.Store(event['session_id'], workspace(event), root)
    try:
        with store.edit() as state:
            nudges = dict(state.get('relayNudges') or {})
            wake = state.get('autoWake')
            record = (state.get('requests') or {}).get(wake) or {}
            if wake and (record.get('hostRead') or record.get('status') in auto_mode.WORKING):
                state.pop('autoWake', None)
                wake = None
            if wake and nudges.get(wake, 0) >= MAX_NUDGES:
                state.pop('autoWake', None)  # The ledger still lists it as unread on the next AUTO turn.
                wake = None
            if wake:
                state['relayNudges'] = dict(nudges, **{wake: nudges.get(wake, 0) + 1})
                unfollowed = None
            else:
                unfollowed = next((key for key, item in auto_mode.auto_requests(state)
                                   if item.get('status') in auto_mode.WORKING and nudges.get(key, 0) < MAX_NUDGES
                                   and not followed(event, store, key)), None)
                if unfollowed:
                    state['relayNudges'] = dict(nudges, **{unfollowed: nudges.get(unfollowed, 0) + 1})
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {}
    if wake:
        return context(event, 'CLI-MODE AUTO: handoff ' + wake + ' has finished, and you have not read its result. '
                              'Run `' + command(event, root, 'relay', '--request', wake, '--for-host') + '` and '
                              'check it before telling the user how it went.')
    if unfollowed:
        return context(event, 'CLI-MODE AUTO: handoff ' + unfollowed + ' is with the agent, but nothing follows it, '
                              'so nothing would wake you when it finishes. Run its follow `' +
                       command(event, root, 'follow', '--request', unfollowed) + '` now (CLI-MODE makes it a '
                       'background task), then end the turn.')
    return {}


def relay_position(state, request):
    """The requests and stream cursor that continue `request`'s relay, with any earlier requests it carries."""
    progress = (state.get('relayProgress') or {}).get(request) or {}
    chain = progress.get('chain')
    if chain:
        return chain['requests'], chain['cursor']
    earlier = unrelayed(state, before=request, session=((state.get('requests') or {}).get(request) or {}).get('session'))
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


# The Mode page's routes, as `auto` controller words. Each runs in this hook, starting an AUTO agent included (as
# bind does): the hook's 300 s timeout covers an agent's start, and as a command it would get a pane row of its own.
MODE_ACTIONS = {
    'mode-page': lambda decision: ['page'],
    'mode-set': lambda decision: ['set', '--to', decision['mode']],
    'mode-agent': lambda decision: ['agent', '--role', decision['role'],
                                    *(['--agent', decision['agent']] if decision.get('agent') else [])],
    'mode-backup-clear': lambda decision: ['clear-backup'],
    'mode-strength': lambda decision: ['strength', '--to', decision['strength']],
    'mode-effort': lambda decision: ['effort', '--to', decision['effort']],
    'mode-fast': lambda decision: ['fast', '--to', decision['fast']],
    'mode-choose': lambda decision: ['choose', '--number', str(decision['number'])],
    'mode-back': lambda decision: ['back'],
    'mode-dismiss': lambda decision: ['close'],
}


def instant(event, root, *words, render=None):
    """Run a control in process and show its reply at once."""
    try:
        result = controller(event, root, *words)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        return show_text(event, 'CLI-MODE: ' + str(exc))
    return show_text(event, render(result)) if render else show_result(event, result)


def cancel_all(event, root, state):
    """AUTO's `/cli cancel`: one brake for the whole team, every agent's running turn (each wakes Claude as canceled).
    Queued work still runs, as with /cli cancel <name> in DIRECT; /cli off closes every agent."""
    import route
    from controller import Controller
    from state import agent_label, live_agents
    control = Controller(route.Store(event['session_id'], workspace(event), root))
    stopped = []
    for session in live_agents(state, every=True).values():
        try:
            if control.cancel(session).get('canceled'):
                stopped.append(agent_label(state, session))
        except RuntimeError:
            continue  # Closed meanwhile: nothing of it to cancel.
    if not stopped:
        return 'No agent turn was running, so nothing was canceled. Queued work is unchanged.'
    return ('Cancel requested for ' + ', '.join(stopped) + ': ' + ('its turn stops' if len(stopped) == 1 else
                                                                   'their turns stop') +
            ', and Claude is told. Queued work still runs; /cli off closes every agent.')


def prompt_reply(event, root, state, decision, worker, cancellation):
    import presentation
    kind = decision['route']
    adapter = label_of(state, decision)
    pending = state.get('pending') or {}
    if kind == 'auto-host':  # AUTO: /d is the user asking Claude itself; nothing of this turn goes to an agent.
        return context(event, 'CLI-MODE AUTO is on, but this message starts with /d: the user is asking you, not the '
                       'agent. Answer it or do it yourself, and do not hand it off (the handoff command is refused this '
                       'turn, and your own edits are not limited by the delegation strength; while an agent is changing '
                       'the project, still leave the project files alone). The /d is not part of the request.')
    if kind == 'host' and state.get('active') and state.get('routingMode') == 'auto':
        text = auto_context(event, root, state)  # Claude's own turn in AUTO: the rule once, then what changed.
        text = '\n'.join(part for part in (text, saved_prompt(event)) if part)
        return context(event, text) if text else {}
    if kind in ('host', 'restore'):
        return {}
    if kind == 'hint':
        return show_text(event, decision['text'])
    if kind == 'help':
        return instant(event, root, 'commands')
    if kind in ('help-invalid', 'mode-invalid'):
        return {}  # handle() closes the card and routes the message again; never hold it here.
    if kind in MODE_ACTIONS:
        return instant(event, root, 'auto', *MODE_ACTIONS[kind](decision))
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
    if kind == 'cancel-all':
        return show_text(event, cancel_all(event, root, state))
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
        if (state['requests'].get(request) or {}).get('routingMode') == 'auto':
            return context(event, auto_continuation(event, root, state, request, label))
        if background():
            remember_label(event, root, request, label, decision.get('named', False))
        lead = ('CLI-MODE forwarded this message (without its /d trigger' +
                (' and the agent name after it' if decision.get('named') else '') + ') unchanged to ' + label +
                '. Only its text was forwarded: images or files '
                'attached to it stay with Claude Code, so if it had any, one short line saying the agent did not '
                'receive them follows the opening line below, in the same message.')
        earlier = unrelayed(state, before=request, session=(state['requests'].get(request) or {}).get('session'))
        if earlier:
            lead += (' The relay also carries earlier requests whose output the user has not seen yet (a relay was '
                     'interrupted), first and in order; they are not sent again.')
        return context(event, relay_context(event, root, adapter, earlier + [request], lead, worker, passing=True,
                                            label=label))
    if kind == 'resume':
        return resume_reply(event, root, adapter, worker, state)
    return {}


def auto_continuation(event, root, state, request, label):
    """AUTO: the user's /cli approve or /cli deny goes on with Claude's handoff, so its result comes to Claude."""
    import route
    handoff = (state['requests'][request].get('handoff') or {})
    before = (state.get('followLabels') or {}).get(handoff.get('continues'))
    try:
        with route.Store(event['session_id'], workspace(event), root).edit() as saved:
            labels = saved.setdefault('followLabels', {})
            labels[request] = (before or label) + ' (answered)'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass  # Only the pane row's name.
    return ('CLI-MODE sent the user\'s answer to ' + label + ', which goes on with your handoff' +
            (' (task ' + handoff['task'] + ')' if handoff.get('task') else '') + '. Its result comes to you, not the '
            'user. Run its follow `' + command(event, root, 'follow', '--request', request) + '` now, as is (CLI-MODE '
            'makes it a background task), post one short line that ' + label + ' goes on, and end the turn: the '
            'follow\'s end wakes you, and you then read the result with the relay command CLI-MODE gives you.' +
            COMPLETE)


def unrelayed(state, before=None, limit=3, session=None):
    """This activation's requests whose relay never finished: their output has not reached the user.

    With `session`, only that agent's (a turn's relay carries its own agent's earlier requests); otherwise
    every running agent's. With `before`, only those captured before that request: one queued after it has
    not even started, and chained first it held a canceled turn's relay back until it finished (live, 6 min).
    """
    progress = state.get('relayProgress') or {}
    records = state.get('requests') or {}
    sessions = {session} if session else {item['name'] for item in state.get('owned') or []}
    limit_at = (records.get(before) or {}).get('capturedAt') if before else None
    rows = [(record.get('capturedAt') or 0, key) for key, record in records.items()
            if key != before and (limit_at is None or (record.get('capturedAt') or 0) < limit_at)
            and record.get('generation') == state.get('generation')
            and record.get('session') in sessions and record.get('routingMode') != 'auto'  # AUTO: Claude reads those.
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
    if state.get('autoRule'):
        import route
        try:  # The AUTO rule sent earlier may be summarized away: the next AUTO turn sends it whole again.
            with route.Store(event['session_id'], workspace(event), root).edit() as saved:
                saved.pop('autoRule', None)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
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
