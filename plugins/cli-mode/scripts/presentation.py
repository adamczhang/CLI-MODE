"""Plain menu layout. Chat color and font selection belong to the host renderer."""
import re
import textwrap

# Every host shows the same frame on a phone too (Claude Code's mobile app and remote
# control), where a code block fits about 40 monospace characters: wider frames overflow.
MENU_WIDTH = 40
# A menu never offers more than this many selectable rows, counting navigation.
# A provider catalog can be long and plan-dependent, so long lists paginate
# instead of growing an unreadable page.
MENU_MAX_OPTIONS = 10

# ACPX shared sessions have no channel for live approvals. Every access level
# except allow ends the turn at the first request the agent would ask about;
# the answer asks the user, and /cli approve sends the turn on with that kind
# of request allowed (dispatch.approval_policy).
PERMISSION_CODES = ('PERMISSION_PROMPT_UNAVAILABLE', 'PERMISSION_DENIED')
# What each kind of ACP tool request lets an agent do, in the approval question.
KIND_WORDS = {'edit': 'edit files', 'delete': 'delete files', 'move': 'move files', 'execute': 'run commands',
              'fetch': 'fetch from the web'}


def access_note(access):
    return '' if access == 'allow' else ' \u2014 you approve in chat'


# One vocabulary for every agent. The shared alias leads; the provider's own
# name for that mode follows in brackets when it says something different.
ACCESS_ALIASES = {'allow': 'Allow', 'auto-edit': 'Auto-edit', 'prompt': 'Prompt'}


def access_display(access, native=None):
    alias = ACCESS_ALIASES.get(access, access)
    return alias + (' (' + native + ')' if native and native.casefold() != alias.casefold() else '')


# Effort levels in ascending order under one spelling, whatever order and
# casing a provider advertises ("Xhigh", "Extra High", highest-first lists).
EFFORT_ORDER = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
EFFORT_NAMES = dict(minimal='Minimal', low='Low', medium='Medium', high='High', xhigh='Extra High',
                    max='Max', ultra='Ultra')
EFFORT_SPELLINGS = {'extra high': 'xhigh', 'extra-high': 'xhigh', 'x-high': 'xhigh'}


def effort_key(name, value=None):
    for candidate in (value, name):
        if isinstance(candidate, str):
            key = EFFORT_SPELLINGS.get(candidate.casefold(), candidate.casefold())
            if key in EFFORT_NAMES:
                return key
    return None


def effort_display(name, value=None):
    key = effort_key(name, value)
    return EFFORT_NAMES[key] if key else name


def effort_rank(name, value=None):
    key = effort_key(name, value)
    return EFFORT_ORDER.index(key) if key else len(EFFORT_ORDER)


HOST_NOTE_WORDS = ('2 to 6 short lines, in plain words, on what this conversation has been working on so far: the '
                   'task, what is done, what is still open, and the key files. If nothing yet, write: Nothing yet, '
                   'this conversation has just started.')


# For a host that runs an activation as a command and reads its JSON (Codex, or Claude widening access). Short:
# Codex carries it in every menu turn's instructions.
HOST_NOTE_RULE = ('A result with hostNote started a new agent: first replace its placeholder line in hostNote.file '
                  'with 2-6 plain lines on what this conversation has worked on (or: Nothing yet), editing only that '
                  'line. ')


def host_note_step(note):
    """What the host does when an activation starts a new agent: write its note in the project brief, or None."""
    if not isinstance(note, dict) or not note.get('placeholder'):
        return None
    return ('First, for the agents to read: in the project file `' + note['file'] + '`, replace the line `' +
            note['placeholder'] + '` (under `' + note['heading'] + '`) with ' + HOST_NOTE_WORDS + ' Use the Edit '
            'tool on that one line (no headings in the note), and change nothing else in the file.')


def approval_rule(asked):
    """The ACPX rule `/cli approve` adds for a request: its kind (edit, execute...), else its exact title.

    A kind, because an agent retrying a refused step often words it differently (in a live probe, a write titled
    with a full path came back titled with a relative one), so an exact title would stop it again.
    """
    return asked.get('kind') if asked.get('kind') in KIND_WORDS else asked.get('title')


def approval_words(asked):
    return KIND_WORDS.get(asked.get('kind'), 'use this tool')


def permission_stop(label, access_name, asked=None):
    if not asked or not approval_rule(asked):
        return (label + ' asked for a permission that CLI-MODE could not read, so the turn stopped there under ' +
                access_name + ' access. Use /cli access and choose allow to let it continue, or ask for work that '
                'needs no approval.')
    words = approval_words(asked)
    return (label + ' asks to ' + words + ': ' + ' '.join((asked.get('detail') or asked.get('title')).split()) +
            '\nIts turn stopped for your answer (' + access_name + ' access). /cli approve lets it ' + words +
            ' and carry on, /cli approve always lets it ' + words + ' from now on, /cli deny tells it no.')


def paginate(items, reserved=0, page=1, limit=MENU_MAX_OPTIONS):
    """Slice `items` so the page never exceeds `limit` selectable rows.

    `reserved` counts the navigation rows the caller always shows (Back,
    Refresh, Exit and so on). Paging rows are reserved automatically when more
    than one page is needed. Item numbers stay continuous across pages, so a
    number always identifies the same entry in the displayed snapshot.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 3:
        raise ValueError('A menu must allow at least three selectable rows.')
    if reserved < 0 or reserved >= limit:
        raise ValueError('Reserved navigation rows leave no room for choices.')
    items = list(items)
    if len(items) <= limit - reserved:
        return dict(items=items, page=1, pages=1, start=1, rows=[])
    # One capacity for every page, sized for the worst case where both a Next
    # and a Previous row are shown. Simpler than per-page sizing, and no page
    # can overflow the cap.
    capacity = limit - reserved - 2
    if capacity < 1:
        raise ValueError('Reserved navigation rows leave no room for paging.')
    pages = -(-len(items) // capacity)
    page = max(1, min(int(page), pages))
    start = (page - 1) * capacity
    rows = []
    if page < pages:
        rows.append('> Next page (' + str(page + 1) + ' of ' + str(pages) + ')')
    if page > 1:
        rows.append('< Previous page (' + str(page - 1) + ' of ' + str(pages) + ')')
    return dict(items=items[start:start + capacity], page=page, pages=pages,
                start=start + 1, rows=rows)


def options_menu(subtitle, labels, tail=(), page=1, lead=(), limit=MENU_MAX_OPTIONS):
    """Build a numbered choice menu that respects the option cap.

    `tail` holds the caller's navigation rows, excluding the X. Exit row that
    every menu carries. Numbering is continuous across pages.
    """
    tail = list(tail)
    # X. Exit is always appended by menu_block and counts toward the cap.
    slice_ = paginate(labels, reserved=len(tail) + 1, page=page, limit=limit)
    body = list(lead)
    for offset, label in enumerate(slice_['items']):
        body.append(str(slice_['start'] + offset) + '. ' + label)
    if slice_['pages'] > 1:
        body.append('')
        body.append('Showing ' + str(slice_['start']) + '-' +
                    str(slice_['start'] + len(slice_['items']) - 1) +
                    ' of ' + str(len(list(labels))))
    body.extend(slice_['rows'])
    body.extend(tail)
    return dict(text='CLI-MODE\n' + subtitle + '\n\n' + '\n'.join(body),
                page=slice_['page'], pages=slice_['pages'], start=slice_['start'],
                shown=len(slice_['items']))


# Claude Code's chat renders LaTeX (KaTeX on the desktop, and the mobile app), the one way it shows colour:
# titles and attribution come out green, bold and sans-serif. Codex keeps its own green views.
# The desktop takes $...$ as maths only when it passes a guard: at most 60 characters inside, and no "#", "@",
# '"', "$" or "`" (so a hex colour is written without its "#", which KaTeX accepts). Forest green reads on
# both the light and the dark theme.
CHAT_GREEN = '228b22'
LATEX_MAX = 60
LATEX_UNSAFE = re.compile(r'[#@"$`]|&&|  ')
LATEX_ESCAPES = {'\\': r'\textbackslash{}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
                 **{char: '\\' + char for char in '&%_{}'},
                 ' ': '~'}  # A phone's math renderer drops ordinary spaces ("Codexsays..."); ~ is kept.
GREEN_OPEN, GREEN_CLOSE = '\\color{' + CHAT_GREEN + '}\\small\\textsf{\\textbf{', '}}'


def green(words):
    """One green span's LaTeX (without its $ signs). LaTeX is drawn at 1.21 times the chat's text size;
    \\small brings it to about 1.09, just larger than the text around it."""
    return GREEN_OPEN + ''.join(LATEX_ESCAPES.get(char, char) for char in words) + GREEN_CLOSE


# Lines removed, in the red diffs use; it reads on both the light and the dark theme.
CHAT_RED = 'cf222e'


def added_removed(added, removed, color=False):
    """`+42 -7`: lines added in green and removed in red (Claude Code's chat), or plain."""
    plus, minus = '+' + str(added), '-' + str(removed)
    if not color:
        return plus + ' ' + minus
    return ('$' + green(plus) + '$ $' + GREEN_OPEN.replace(CHAT_GREEN, CHAT_RED) +
            ''.join(LATEX_ESCAPES.get(char, char) for char in minus) + GREEN_CLOSE + '$')


def strong(text, color=False):
    """Bold text; with colour on (Claude Code, /cli color), green bold sans-serif.

    A line too long for one span is split at spaces into several, with a plain space between:
    "Passing to Antigravity..." is two. Text the guard would refuse stays plain bold.
    """
    if not color or LATEX_UNSAFE.search(text):
        return '**' + text + '**'
    spans, words = [], []
    for word in text.split(' '):
        if words and len(green(' '.join(words + [word]))) > LATEX_MAX:
            spans.append(green(' '.join(words)))
            words = []
        words.append(word)
    spans.append(green(' '.join(words)))
    return ' '.join('$' + span + '$' for span in spans)


def plain_strong(text):
    """Coloured titles and names turned back into **bold**, as the words a reader sees (for checks)."""
    unescape = {latex: char for char, latex in LATEX_ESCAPES.items()}
    # One pass, longest first, so a restored "~" is never read again as a space.
    escapes = re.compile('|'.join(re.escape(latex) for latex in sorted(unescape, key=len, reverse=True)))
    span = r'\$' + re.escape(GREEN_OPEN) + r'(.*?)' + re.escape(GREEN_CLOSE) + r'\$'

    def bold(match):
        words = re.findall(span, match.group(0))
        return '**' + ' '.join(escapes.sub(lambda found: unescape[found.group(0)], part) for part in words) + '**'
    return re.sub(span + '(?: ' + span + ')*', bold, text)


def chat_menu(text, color=False):
    """A menu for Claude Code's chat: the same ASCII box, its two title rows in green.

    Text in a code block cannot be styled, but a diff block colours each line that starts
    with "+" (the desktop's and the phone's code highlighting both do). The title rows take
    "+" edges, and the borders, which would start with "+" too, take ".", "|" and "'"
    corners so that only the title is green, in the same monospace letters, with every
    column where it was. Anything else (plain notices, other text) is unchanged.
    """
    lines = text.split('\n')
    if (not color or len(lines) < 7 or lines[0] != '```text' or not lines[2].startswith('| CLI-MODE ')
            or not lines[1] == lines[4] == lines[-2]):
        return text
    rule = lines[1][1:-1]
    band = ['+' + row[1:-1] + '+' for row in lines[2:4]]
    # Section headings (the help card's AGENTS, SEND WORK...) are green too.
    body = ['+' + row[1:-1] + '+' if SECTION.fullmatch(row[2:-2].strip()) else row for row in lines[5:-2]]
    return '\n'.join(['```diff', '.' + rule + '.', *band, '|' + rule + '|', *body, "'" + rule + "'", '```'])


SECTION = re.compile(r'[A-Z]{3,}(?: [A-Z]{2,})*')


def menu_frame(text, width=MENU_WIDTH):
    """Wrap every source row inside a simple ASCII frame, including long labels."""
    if not isinstance(width, int) or isinstance(width, bool) or not 12 <= width <= MENU_WIDTH:
        raise ValueError('Menu width must be between 12 and 40 characters.')
    inner = width - 4
    border = '+' + '-' * (width - 2) + '+'
    lines = text.splitlines()
    rows = [border]
    for index, line in enumerate(lines):
        # Continuation rows keep the source line's indent, so a wrapped
        # description stays visibly under its command.
        indent = line[:len(line) - len(line.lstrip(' '))]
        wrapped = textwrap.wrap(line, width=inner, break_long_words=True, subsequent_indent=indent,
                                break_on_hyphens=False) or ['']
        rows.extend('| ' + row.ljust(inner) + ' |' for row in wrapped)
        if index == 1 and lines[0] == 'CLI-MODE':
            rows.append(border)
    return '\n'.join([*rows, border])


def menu_block(text, width=MENU_WIDTH):
    """Preserve alignment and disable the host's automatic syntax highlighting."""
    if not text.startswith('CLI-MODE\n'):
        text = 'CLI-MODE\n' + text
    lines = text.splitlines()
    exits = [line for line in lines if line.strip().casefold().startswith('x. ')]
    lines = [line for line in lines if not line.strip().casefold().startswith('x. ')]
    text = '\n'.join(lines).rstrip() + '\n' + (exits[-1] if exits else 'X. Exit')
    return '```text\n' + menu_frame(text, width) + '\n```'


# Text for hosts without inline views (Claude Code). A menu block is fenced for
# chat Markdown; plain-text surfaces, such as a message shown instead of a
# prompt, take it without the fence.

def unfence(text):
    """A menu block without its Markdown code fence."""
    if text.startswith('```text\n') and text.endswith('\n```'):
        return text[len('```text\n'):-len('\n```')]
    return text


def result_text(result, fenced=True):
    """What a controller result shows: a confirmation, else its menu and message."""
    activation = result.get('activation')
    if isinstance(activation, dict) and activation.get('text'):
        parts = [activation['text']]
    else:
        parts = [result[key] for key in ('activationMenu', 'message') if isinstance(result.get(key), str) and result[key]]
        if not parts:
            parts = [result[key] for key in ('text', 'error') if isinstance(result.get(key), str) and result[key]][:1]
    parts = parts if fenced else [unfence(part) for part in parts]
    return '\n\n'.join(parts)


def relay_plain(result):
    """A relay result as Claude Code prints it: plain words, then exactly what to post.

    Claude reads this, and anyone who opens the tool call sees it, so it is not JSON:
    the first line says whether the agent is still working (with the next --cursor)
    or has finished, and everything after the blank line is posted exactly. Until
    the agent finishes there is nothing to post, except the first parts of a long answer.
    """
    agent = result.get('agent') or 'The agent'
    post = result['text'] if result['done'] else result.get('markdown') or ''
    again = ' Run the same command again with --cursor ' + str(result['cursor']) + '.'
    if result.get('posted'):
        return agent + '\'s answer is already posted above, so there is nothing more to post.'
    if result['done']:
        # Agents that finish together can mean several relays in one turn; the desktop app folds text between
        # tool calls out of view, so only a final message that carries them all shows every answer.
        lead = (agent + ' has finished. Post everything below this line exactly, as the last message of the turn. '
                'If another CLI-MODE relay ran in this turn too, that last message carries every relay\'s output, '
                'in the order they ran, each exactly as printed.')
    elif post:
        lead = (agent + ' has finished, and its answer is long, so it comes in parts. Post everything below this '
                'line exactly, then run the same command again with --cursor ' + str(result['cursor']) + '.')
    elif result.get('status') == 'captured':
        lead = agent + ' is finishing an earlier turn; this request is queued.' + again
    else:
        idle = result.get('idleSeconds')
        quiet = ' (quiet for ' + str(int(idle)) + ' s)' if isinstance(idle, (int, float)) and idle >= 1 else ''
        lead = agent + ' is still working' + quiet + '.' + again
    return lead + ('\n\n' + post if post else '')


def queue_text(result):
    """`/cli queue` as short plain lines: the worker, then each request's status."""
    lines = ['CLI-MODE queue', 'Worker: ' + (result.get('workerState') or 'idle')]
    if result.get('workerError'):
        lines.append('Worker error: ' + str(result['workerError']))
    requests = result.get('requests') or []
    if not requests:
        lines.append('No requests in this activation.')
    for item in requests:
        age = item.get('statusAgeSeconds')
        lines.append('  ' + item['requestId'][:8] + '  ' + item['status'] +
                     (' (' + str(int(age)) + ' s)' if isinstance(age, (int, float)) else ''))
    if result.get('inflight'):
        lines.append(str(len(result['inflight'])) + ' operation(s) in flight.')
    return '\n'.join(lines)


def close_text(result):
    """`/cli close`: the chooser when several agents run, one agent's close, or the full shutdown."""
    if isinstance(result.get('activationMenu'), str):
        return result_text(result)
    if 'closed' in result:
        return result.get('message') or ''
    return shutdown_text(result)


def shutdown_text(result):
    """`/cli stop` as plain lines, reporting exactly what shutdown verified."""
    if result.get('shutdownComplete'):
        lines = ['CLI-MODE is off. The agent session was closed.']
    else:
        lines = ['CLI-MODE is off, but shutdown is not complete.']
        lines += ['Could not close ' + item['session'] + ': ' + item['error'] for item in result.get('failures') or []]
        if result.get('inflight'):
            lines.append(str(len(result['inflight'])) + ' operation(s) are still unwinding. Check /cli queue.')
    if result.get('note'):
        lines.append(result['note'])
    cancellation = result.get('installerCancellation')
    if isinstance(cancellation, dict) and cancellation.get('message'):
        lines.append(cancellation['message'])
    return '\n'.join(lines)
