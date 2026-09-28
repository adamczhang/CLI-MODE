"""Relay output: Markdown for mid-turn updates, one inline view when the turn ends.

Codex renders inline views in the final response, and each view is a sandboxed
frame, so a turn gets exactly one: the agent's final words stay visible and its
plan and tool work nest inside browser-native <details> (no script). Mid-turn
updates are ordinary chat Markdown the host posts as given.

Every provider string is escaped in HTML (or passed through menu_view's safe
inline formatter). In Markdown, a line that Codex would treat as a view reference
is defused, so agent output can never open an inline view.
"""
from html import escape
from pathlib import Path, PureWindowsPath, PurePosixPath
import re
import uuid

from menu_view import GREEN, GREEN_LIGHT, block_content, block_styles
from presentation import strong
from progress import ActivityRelay, STATUSES, TERMINAL, usage_text

# Work groups in a fixed reading order.
GROUPS = (('read', 'Reading'), ('search', 'Searching'), ('edit', 'Editing'), ('delete', 'Deleting'),
          ('move', 'Moving'), ('execute', 'Running commands'), ('fetch', 'Fetching'),
          ('switch_mode', 'Changing mode'), ('other', 'Other work'))
PLAN_MARKS = dict(completed='\u2713', in_progress='\u2192', pending='\u25cb')
# Codex's inline surface replaces these with its own icons; elsewhere they are empty.
ICONS = dict(completed='check', failed='x', in_progress='loader', pending='circle')
MAX_ROWS = 20  # Per group; the rest are counted, not listed.
VIEW_REFERENCE = re.compile(r'^( {0,3})(visualize\{|::codex-inline-vis)', re.MULTILINE)


# Work between two pieces of text means they are separate messages, even from
# agents that send no message IDs (Grok, Copilot and Antigravity do not).
# Usage reports can arrive mid-sentence, so they never split text.
MESSAGE_BREAKS = ('activity', 'plan', 'artifact', 'error')


def messages(events, last_only=False):
    """Join message chunks; a new messageId or intervening work starts a new paragraph."""
    groups, current, interrupted = [], object(), False
    for event in events:
        kind = event.get('type')
        if kind in MESSAGE_BREAKS:
            interrupted = True
        if kind != 'message':
            continue
        identity = event.get('messageId')
        if not groups or identity != current or interrupted:
            groups.append([])
        groups[-1].append(event['text'])
        current, interrupted = identity, False
    if last_only:
        groups = groups[-1:]
    return '\n\n'.join(''.join(group) for group in groups)


def snapshot(history):
    """Latest tool states, plan and usage for the whole turn so far."""
    relay = ActivityRelay()
    plan = None
    for event in history:
        if event.get('type') in ('activity', 'usage'):
            relay.feed(event)
        elif event.get('type') == 'plan':
            plan = event['entries']
    relay.flush()
    return list(relay.tools.values()), plan, relay.usage


def artifact_label(content):
    if not isinstance(content, dict):
        return 'Artifact'
    for key in ('name', 'title', 'uri'):
        if isinstance(content.get(key), str) and content[key]:
            return content[key]
    resource = content.get('resource')
    if isinstance(resource, dict) and isinstance(resource.get('uri'), str):
        return resource['uri']
    return (content.get('type') or 'artifact').replace('_', ' ').capitalize()


def display_path(path, workspace=None):
    cls = PureWindowsPath if re.match(r'^[A-Za-z]:|^\\\\', path) else PurePosixPath
    value = cls(path)
    if workspace:
        try:
            return str(value.relative_to(cls(workspace)))
        except ValueError:
            pass
    return path


def tool_row(event, workspace=None):
    title = event.get('title') or dict(GROUPS).get(event['kind'], 'Tool activity')
    where = ', '.join(display_path(item['path'], workspace) + (':' + str(item['line']) if 'line' in item else '')
                      for item in event.get('locations', []))
    return title + (' \u2014 ' + where if where else '')


def counts(tools):
    running = sum(tool['status'] not in TERMINAL for tool in tools)
    done = sum(tool['status'] == 'completed' for tool in tools)
    failed = sum(tool['status'] == 'failed' for tool in tools)
    return ' \u00b7 '.join(part for part in (
        str(running) + ' running' if running else '', str(done) + ' done' if done else '',
        str(failed) + ' failed' if failed else '') if part)


ESCAPES = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def clean(text):
    """Agent text without terminal escape sequences or control characters (tabs and line breaks stay).

    Chat and views would show them as boxes or pass them to a terminal; the viewer window strips the same.
    """
    return ESCAPES.sub('', text)


def close_fence(text):
    """Agent text that ends inside a code fence gets its closing fence, so what follows is not code."""
    opened = None
    for line in text.splitlines():
        match = FENCE.match(line)
        if not match:
            continue
        mark = match.group(1)
        if opened is None:
            opened = mark
        elif mark[0] == opened[0] and len(mark) >= len(opened) and not line.strip()[len(mark):].strip():
            opened = None
    return text if opened is None else text.rstrip('\n') + '\n' + opened


def defuse(text):
    """Stop a line of agent output from being read as an inline-view reference, and drop control characters."""
    # Strip the renderer's opening delimiter even inside prose/code: provider
    # output is content, never authority to load a host-side HTML file.
    text = clean(text).replace('\ue200visualize\ue202', 'visualize')
    return VIEW_REFERENCE.sub('\\1\u200b\\2', text)


def markdown(label, batch, history, passing=None, footer=None, show_work=True, color=False, workspace=None):
    """One mid-turn update, ready to post as chat Markdown."""
    parts = []
    if passing:
        parts.append(strong(passing, color))
    text = messages(batch)
    if text:
        parts.append(strong(label + ' says...', color) + '\n\n' + close_fence(defuse(text)))
    parts += ['Artifact: ' + defuse(artifact_label(event['content']))
              for event in batch if event.get('type') == 'artifact']
    parts += ['**' + defuse(event['message']) + '**'
              for event in batch if event.get('type') == 'error' and event.get('message')]
    if show_work and any(event.get('type') in ('activity', 'plan') for event in batch):
        tools, plan, _ = snapshot(history)
        facts = [counts(tools)] if tools else []
        if plan:
            current = next((entry['content'] for entry in plan if entry['status'] == 'in_progress'), None)
            finished = sum(entry['status'] == 'completed' for entry in plan)
            facts.append('plan ' + str(finished) + '/' + str(len(plan)) + (': ' + current if current else ''))
        active = [tool for tool in tools if tool['status'] not in TERMINAL]
        if active:
            facts.append(tool_row(active[-1], workspace))
        if facts:
            parts.append('_' + defuse(label + ' work: ' + ' \u00b7 '.join(facts)).replace('_', '\\_') + '_')
    if footer:
        parts.append('_' + footer + '_')
    return '\n\n'.join(parts)


RECEIPT_PATHS = 6


def receipt_markdown(label, receipt, color=False):
    """What the turn changed in the folder: files, and lines added (green) and removed (red)."""
    from presentation import added_removed
    if not receipt:
        return None
    count = receipt.get('files') or 0
    if not count:
        return '_' + label + ' changed no files._'
    head = (label + ' changed ' + str(count) + (' file ' if count == 1 else ' files ') +
            added_removed(receipt.get('added', 0), receipt.get('removed', 0), color))
    rows = []
    for item in (receipt.get('paths') or [])[:RECEIPT_PATHS]:
        path = defuse(item['path'])
        shown = '`' + path + '`' if '`' not in path else path
        rows.append(shown + ' ' + ('binary' if item.get('added') is None else
                                   added_removed(item['added'], item['removed'], color)))
    more = count - len(rows)
    return head + '\n\n' + ' \u00b7 '.join(rows) + (' \u00b7 and ' + str(more) + ' more' if more > 0 else '')


def receipt_html(label, receipt):
    """The same receipt in a view: visible, not folded into the work section."""
    if not receipt:
        return '', None
    count = receipt.get('files') or 0
    if not count:
        text = label + ' changed no files.'
        return '<div class="changes state">' + escape(text) + '</div>', text

    def numbers(added, removed):
        return ('<span class="add">+' + str(added) + '</span> <span class="del">-' + str(removed) + '</span>')
    rows = ''.join('<li><code>' + escape(item['path']) + '</code> ' +
                   ('binary' if item.get('added') is None else numbers(item['added'], item['removed'])) + '</li>'
                   for item in (receipt.get('paths') or [])[:RECEIPT_PATHS])
    more = count - min(len(receipt.get('paths') or []), RECEIPT_PATHS)
    if more > 0:
        rows += '<li>and ' + str(more) + ' more</li>'
    head = label + ' changed ' + str(count) + (' file' if count == 1 else ' files')
    text = head + ' (+' + str(receipt.get('added', 0)) + ' -' + str(receipt.get('removed', 0)) + ')'
    return ('<div class="changes"><strong>' + escape(head) + '</strong> ' +
            numbers(receipt.get('added', 0), receipt.get('removed', 0)) + '<ul>' + rows + '</ul></div>'), text


def saved_markdown(label, saved):
    """What the turn saved in the agent's working folder: `ART saved 3 files in `Agent_Working_Folder/ART/`: ...`."""
    from agent_folder import counts
    if not saved:
        return None
    head = label + ' ' + counts(saved) + ' in `' + saved['folder'] + '/`'
    if saved.get('partial'):
        return head + ' (too many files there to list them).'
    rows = []
    for item in (saved.get('paths') or [])[:RECEIPT_PATHS]:
        path = defuse(item['path'])
        rows.append(('`' + path + '`' if '`' not in path else path) + ' ' + item['status'])
    more = saved['files'] - len(rows)
    return head + ': ' + ' · '.join(rows) + (' · and ' + str(more) + ' more' if more > 0 else '')


def saved_html(label, saved):
    """The same line in a view, with its plain-text fallback."""
    from agent_folder import counts
    if not saved:
        return '', None
    head = label + ' ' + counts(saved) + ' in ' + saved['folder'] + '/'
    if saved.get('partial'):
        text = head + ' (too many files there to list them).'
        return '<div class="changes saved">' + escape(text) + '</div>', text
    rows = ''.join('<li><code>' + escape(item['path']) + '</code> ' + escape(item['status']) + '</li>'
                   for item in (saved.get('paths') or [])[:RECEIPT_PATHS])
    more = saved['files'] - min(len(saved.get('paths') or []), RECEIPT_PATHS)
    if more > 0:
        rows += '<li>and ' + str(more) + ' more</li>'
    return ('<div class="changes saved"><strong>' + escape(head) + '</strong><ul>' + rows + '</ul></div>'), head + '.'


def refs_markdown(label, refs):
    """The reference box at the bottom of an answer: a code block, so the host gives it a copy button.

    It holds the saved answer's path and the files the turn made or mentioned, ready to paste into another
    agent's /d; the answer itself stays above it, as usual.
    """
    from agent_folder import box
    lines = box(label, (refs or {}).get('answer'), (refs or {}).get('files'))
    if not lines:
        return None
    body = '\n'.join(lines)
    fence = '`' * max(3, max((len(run) for run in re.findall('`+', body)), default=0) + 1)
    return fence + 'text\n' + body + '\n' + fence


def refs_html(label, refs):
    """The same references in a view (selectable text; views have no copy button), with a plain fallback."""
    from agent_folder import box
    lines = box(label, (refs or {}).get('answer'), (refs or {}).get('files'))
    if not lines:
        return '', None
    return '<pre class="refs">' + escape('\n'.join(lines)) + '</pre>', '\n'.join(lines)


def final_markdown(label, batch, history, footer=None, show_work=True, color=False, receipt=None, saved=None,
                   refs=None, tests=None, overlaps=None):
    """The end of a turn for hosts without inline views (Claude Code), as chat Markdown.

    It carries the agent's words, artifacts and errors (all of the turn's, unless
    a long answer's first part went out already), then a single line summarizing
    the whole turn's work. The terminal shows HTML literally, so there is no
    <details> nesting.
    """
    parts = []
    text = messages(batch)
    if text:
        parts.append(strong(label + ' says...', color) + '\n\n' + close_fence(defuse(text)))
    parts += ['Artifact: ' + defuse(artifact_label(event['content']))
              for event in batch if event.get('type') == 'artifact']
    parts += ['**' + defuse(event['message']) + '**'
              for event in batch if event.get('type') == 'error' and event.get('message')]
    if show_work:
        tools, plan, usage = snapshot(history)
        facts = [counts(tools)] if tools else []
        if plan:
            finished = sum(entry['status'] == 'completed' for entry in plan)
            facts.append('plan ' + str(finished) + '/' + str(len(plan)))
        if usage:
            facts.append(usage_text(usage))
        if facts:
            parts.append('_' + defuse(label + ' work: ' + ' · '.join(facts)).replace('_', '\\_') + '_')
    changed = receipt_markdown(label, receipt, color)
    if changed:
        parts.append(changed)
    kept = saved_markdown(label, saved)
    if kept:
        parts.append(kept)
    from test_gate import line, overlap_line
    parts += ['_' + defuse(text) + '_' for text in [line(tests)] + overlap_line(overlaps) if text]
    if footer:
        parts.append('_' + footer + '_')
    if not parts and not messages(history):
        parts.append('_' + label + ' finished without public output._')
    box = refs_markdown(label, refs)
    if box:
        parts.append(box)  # Last: the answer stays above, and this is what gets copied into another agent.
    return '\n\n'.join(parts)


HOST_ANSWER_MAX = 6000  # Characters of the agent's answer Claude reads in AUTO; the whole answer stays in its file.
HOST_ENDS = {'completed': 'finished', 'canceled': 'was canceled', 'superseded': 'was canceled',
             'rejected': 'was not sent', 'uncertain': 'could not be confirmed (check /cli queue)'}


def file_stat(item):
    """One changed file of a receipt: `app.py +3 -1`, `logo.png (binary)`, or the bare path when its lines were
    not counted."""
    if 'added' not in item:
        return item['path']
    if item['added'] is None:
        return item['path'] + ' (binary)'
    return item['path'] + ' +' + str(item['added']) + ' -' + str(item.get('removed') or 0)


def check_line(status, receipt, batch, read_only=False, stopped=None, others=(), outside=()):
    """The result's verdict, worked out by CLI-MODE so Claude doesn't have to (lever L4): `CHECK: ok`, or
    `CHECK: look: <what>` naming only what needs a look. Every input is already in the settled request."""
    reasons = []
    if status != 'completed':
        reasons.append('it ' + HOST_ENDS.get(status, status))
    tests = receipt.get('tests')
    if isinstance(tests, dict) and tests.get('passed') is False and not tests.get('timedOut'):
        reasons.append('tests failed')  # A run that timed out is no result; its TESTS line says so.
    if stopped:
        reasons.append('it stopped to ask permission')
    if any(event.get('type') == 'error' for event in batch):
        reasons.append('its turn reported errors')
    if others:
        reasons.append('files it did not edit changed while it worked')
    if outside:
        reasons.append('it edited files outside its Files claim')
    if receipt.get('overlaps'):
        reasons.append('another agent edited the same files')
    if read_only and ((receipt.get('changes') or {}).get('files') or 0):
        reasons.append('a read-only task changed files')
    if status == 'completed' and not messages(batch).strip():
        reasons.append('it gave no answer')
    return 'CHECK: ok' if not reasons else 'CHECK: look: ' + '; '.join(reasons) + '.'


# An answer without its REMAINING line can still say work is left: these phrases, found in one of its sentences.
DOUBT = re.compile(r'\b(probably still|still (?:there|remain|left|hidden)|may (?:still )?remain|might (?:still )?remain|'
                   r'i (?:suspect|missed)|likely missed|not (?:sure|confident)|only moderately|needs? (?:a |another )?'
                   r'(?:second look|closer look|another pass|more review)|worth (?:a |another )(?:look|pass)|'
                   r'unfinished|left undone)\b', re.I)
REMAINING_MAX = 300  # Characters of a REMAINING line carried into the ESCALATE line.
REMAINING_LINE = re.compile(r'^[\s>*_`#-]*REMAINING[*_`]*\s*:[*_`]*\s*(.*?)[\s*_`.]*$', re.I | re.M)


def remaining(answer):
    """What an AUTO agent says is left, from its answer: the text of its last `REMAINING:` line ('' for `none`), or
    without that line, the first sentence that says work may remain; None when it says nothing either way."""
    lines = REMAINING_LINE.findall(answer or '')
    if lines:
        text = ' '.join(lines[-1].split())
        if text.casefold().rstrip('.') in ('none', 'nothing', 'n/a', '-'):
            return ''
        # It goes into the ESCALATE line and the next task's Context: a line, not a paragraph (its answer has the rest).
        return text if len(text) <= REMAINING_MAX else text[:REMAINING_MAX - 1].rstrip() + '…'
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', answer or ''):
        if DOUBT.search(sentence):
            return ' '.join(sentence.split())[:240]
    return None


def host_text(request, label, status, batch, receipt, read_only=False, task=None, stopped=None, access=None,
              touched=None, answer_max=HOST_ANSWER_MAX, alongside=(), outside=(), escalate=None):
    """An AUTO handoff's result as Claude reads it (relay --for-host, or the wake-up itself): plain lines, then the
    agent's answer.

    Claude checks it and tells the user in its own words, so there is no Markdown styling and nothing to post as is.
    The receipt covers the whole folder, so while several agents write it holds their files too: with `touched` (the
    files the agent's own edit tools changed), the rest are named apart: `alongside` are those that work running at
    the same time may change (auto_mode.alongside), expected; the others need a look. `outside` are its own edits
    beyond its Files claim (auto_mode.outside_claim). The CHECK line says what, if anything, needs a look; `answer_max`
    cuts the answer when several results share one wake-up.
    """
    from agent_folder import counts
    from test_gate import line, overlap_line
    receipt = receipt or {}
    changes = receipt.get('changes')
    own = {path.casefold() for path in touched} if touched is not None else None
    near = {path.casefold() for path in alongside}
    others = [item['path'] for item in (changes or {}).get('paths') or [] if own is not None
              and item['path'].casefold() not in own and item['path'].casefold() not in near]
    lines = ['HANDOFF ' + request + ': ' + label + ' ' + HOST_ENDS.get(status, status) +
             (' (read-only)' if read_only else '') + '.',
             check_line(status, receipt, batch, read_only, stopped, others, outside)]
    if escalate:  # auto_mode.escalation: ESCALATE (work left, a higher effort to try) or FOLLOW-UP (its result).
        lines.append(escalate)
    if task:
        lines.append('TASK: ' + task)
    if changes:
        count = changes.get('files') or 0
        # Each file with its own lines, as `git diff --stat` gives them, so Claude has nothing to ask git.
        paths = [file_stat(item) for item in (changes.get('paths') or [])[:RECEIPT_PATHS]]
        lines.append('CHANGES: ' + ('none' if not count else str(count) + (' file' if count == 1 else ' files') +
                                    ', +' + str(changes.get('added', 0)) + ' -' + str(changes.get('removed', 0)) +
                                    ': ' + ', '.join(paths) + (' and ' + str(count - len(paths)) + ' more'
                                                               if count > len(paths) else '')))
        if others:
            lines.append('NOT ITS OWN EDITS: ' + ', '.join(others[:RECEIPT_PATHS]) + (
                ' and ' + str(len(others) - RECEIPT_PATHS) + ' more' if len(others) > RECEIPT_PATHS else '') +
                ' changed while it worked, but not by its own edit tools (another task, or a command it ran).')
        if alongside:
            lines.append('ALONGSIDE: ' + ', '.join(list(alongside)[:RECEIPT_PATHS]) + (
                ' and ' + str(len(alongside) - RECEIPT_PATHS) + ' more' if len(alongside) > RECEIPT_PATHS else '') +
                ' changed by other work running at the same time (its claim or its own edits): expected, not this '
                'agent\'s.')
    else:
        lines.append('CHANGES: not measured (not a git repository).')
    if outside:
        lines.append('OUTSIDE ITS CLAIM: ' + ', '.join(list(outside)[:RECEIPT_PATHS]) + (
            ' and ' + str(len(outside) - RECEIPT_PATHS) + ' more' if len(outside) > RECEIPT_PATHS else '') +
            ': its own edits, though its Files line did not name them (another task may have been given them).')
    saved = receipt.get('saved')
    if saved:
        lines.append('SAVED: ' + counts(saved) + ' in ' + saved['folder'] + '/' + (
            '' if saved.get('partial') else ': ' + ', '.join(item['path'] for item in (saved.get('paths') or [])
                                                            [:RECEIPT_PATHS])))
    tests = line(receipt.get('tests'))
    lines.append('TESTS: ' + (tests or 'no test command ran (set one with the controller\'s `test --command=<command>`; '
                                       'in AUTO it is yours to set).'))
    lines += ['OVERLAP: ' + text for text in overlap_line(receipt.get('overlaps'))]
    if stopped:
        from presentation import access_display, permission_stop
        name = access_display((access or {}).get('access', 'prompt'), (access or {}).get('accessName'))
        lines.append('STOPPED: ' + ' '.join(permission_stop(label, name, stopped).split()) + ' Tell the user in one '
                     'line what it asks; only they answer it (/cli approve or /cli deny).')
    lines += ['ERROR: ' + ' '.join(event['message'].split()) for event in batch
              if event.get('type') == 'error' and event.get('message')]
    answer = messages(batch).strip()
    whole = (receipt.get('refs') or {}).get('answer')
    if not answer:
        lines.append('ANSWER: none.')
    elif len(answer) > answer_max:
        lines += ['ANSWER (its first ' + str(answer_max) + ' characters' + ('; the whole answer is in ' + whole
                                                                          if whole else '') + '):',
                  answer[:answer_max]]
    else:
        lines += ['ANSWER' + (' (also saved in ' + whole + ')' if whole else '') + ':', answer]
    return '\n'.join(lines)


def render(label, history, destination, footer=None, show_work=True, workspace=None, receipt=None, saved=None,
           refs=None, tests=None, overlaps=None):
    """The turn's one inline view: final words, artifacts, errors and nested work.

    Returns (path, plain-text fallback, artifacts).
    """
    text = messages(history, last_only=True)
    errors = [event['message'] for event in history if event.get('type') == 'error' and event.get('message')]
    artifacts = [event['content'] for event in history if event.get('type') == 'artifact']
    tools, plan, usage = snapshot(history) if show_work else ([], None, None)
    ident = 'cli-relay-' + uuid.uuid4().hex
    html, plain = [], []
    if text:
        html.append('<div class="attrib">' + escape(label) + ' says...</div>'
                    '<div class="body">' + block_content(clean(text)) + '</div>')
        plain += [label + ' says...', defuse(text)]  # The fallback is chat text: it must not open a view either.
    if artifacts:
        items = ''.join('<li>' + escape(artifact_label(item)) + '</li>' for item in artifacts)
        html.append('<ul class="artifacts">' + items + '</ul>')
        plain += ['Artifact: ' + artifact_label(item) for item in artifacts]
    for message in errors:
        html.append('<div class="error">' + escape(clean(message)) + '</div>')
        plain.append(defuse(message))
    if tools or plan or usage:
        inner = []
        if plan:
            finished = sum(entry['status'] == 'completed' for entry in plan)
            percent = round(100 * finished / len(plan)) if plan else 0
            rows = ''.join('<li><span class="mark">' + PLAN_MARKS.get(entry['status'], '\u25cb') + '</span> ' +
                           escape(entry['content']) + '</li>' for entry in plan)
            inner.append('<details open><summary>Plan \u00b7 ' + str(finished) + ' of ' + str(len(plan)) +
                         ' done</summary><div class="progress" role="progressbar" aria-label="Plan progress" '
                         'aria-valuenow="' + str(percent) + '" aria-valuemin="0" aria-valuemax="100">'
                         '<div class="progress-bar" style="width:' + str(percent) + '%"></div></div>'
                         '<ul>' + rows + '</ul></details>')
            plain.append('Plan: ' + str(finished) + ' of ' + str(len(plan)) + ' done')
        for kind, name in GROUPS:
            group = [tool for tool in tools if tool['kind'] == kind]
            if not group:
                continue
            # Unfinished work first, then the newest.
            group = sorted(reversed(group), key=lambda tool: tool['status'] in TERMINAL)
            rows = ''.join('<li><i data-lucide="' + ICONS[tool['status']] + '" aria-hidden="true"></i> <strong>' +
                           escape(STATUSES[tool['status']]) + '</strong> <span title="' +
                           escape(tool_row(tool), quote=True) + '">' + escape(tool_row(tool, workspace)) + '</span></li>'
                           for tool in group[:MAX_ROWS])
            more = len(group) - MAX_ROWS
            if more > 0:
                rows += '<li>' + str(more) + ' more</li>'
            inner.append('<details><summary>' + escape(name) + ' \u00b7 ' + escape(counts(group)) +
                         '</summary><ul>' + rows + '</ul></details>')
        if usage:
            inner.append('<p class="usage">' + escape(usage_text(usage)) + '</p>')
        summary = counts(tools)
        heading = label + ' work' + (' \u00b7 ' + summary if summary else '')
        html.append('<details class="work"><summary>' + escape(heading) + '</summary>' + ''.join(inner) + '</details>')
        plain.append(heading)
    changed, changed_text = receipt_html(label, receipt)
    if changed:
        html.append(changed)
        plain.append(changed_text)
    kept, kept_text = saved_html(label, saved)
    if kept:
        html.append(kept)
        plain.append(kept_text)
    from test_gate import line, overlap_line
    for text in [line(tests)] + overlap_line(overlaps):
        if text:
            warn = text.startswith('⚠') or text.startswith('✗')
            html.append('<div class="' + ('error' if warn else 'state') + '">' + escape(text) + '</div>')
            plain.append(text)
    if footer:
        html.append('<div class="state">' + escape(footer) + '</div>')
        plain.append(footer)
    if not html:
        html.append('<div class="state">' + escape(label) + ' finished without public output.</div>')
        plain.append(label + ' finished without public output.')
    box, box_text = refs_html(label, refs)
    if box:
        html.append(box)
        plain.append(box_text)
    muted = 'var(--muted-foreground,inherit)'
    fragment = ('<section id="' + ident + '" aria-label="' + escape(label + ' update', quote=True) + '">'
        '<style>#' + ident + '{color:var(--foreground,inherit);background:transparent;font:inherit;line-height:1.5;'
        'overflow-wrap:anywhere;max-width:100%;}'
        '#' + ident + ' .attrib,#' + ident + ' summary{color:' + GREEN + ';}'
        '#' + ident + ' .attrib,#' + ident + ' .work>summary{font-weight:700;}'
        + block_styles(ident) +
        '#' + ident + ' .body code{font-family:ui-monospace,Consolas,monospace;background:transparent;color:inherit;}'
        '#' + ident + ' .error{color:var(--destructive,inherit);}'
        '#' + ident + ' .refs{white-space:pre-wrap;overflow-wrap:anywhere;font-family:ui-monospace,Consolas,monospace;'
        'border:1px solid var(--border,#92979b);border-radius:6px;padding:6px 8px;margin:8px 0 0;}'
        '#' + ident + ' details{margin:4px 0;}'
        '#' + ident + ' details details{margin-left:1.1em;}'
        '#' + ident + ' summary{cursor:pointer;}'
        '#' + ident + ' ul{padding-left:1.3em;margin:4px 0;}'
        '#' + ident + ' li i{display:inline-block;width:14px;height:14px;vertical-align:-2px;}'
        '#' + ident + ' .mark{display:inline-block;width:1.1em;}'
        '#' + ident + ' .state,#' + ident + ' .usage{color:' + muted + ';margin:4px 0;}'
        '#' + ident + ' .changes{margin:6px 0;}'
        '#' + ident + ' .changes .add{color:' + GREEN + ';font-weight:700;}'
        '#' + ident + ' .changes .del{color:#cf222e;font-weight:700;}'
        '@media (prefers-color-scheme:light){#' + ident + ' .attrib,#' + ident + ' summary{color:' + GREEN_LIGHT + ';}}'
        '</style>' + ''.join(html) + '</section>\n')
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fragment, encoding='utf-8', newline='')
    return str(path), '\n'.join(plain), artifacts
