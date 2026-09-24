"""Deterministic inline menu view. No scripts, network, or agent operations."""
from html import escape
from pathlib import Path
import re
import uuid
import json
from progress import ActivityRelay, STATUSES, TERMINAL, activity_text, usage_text


def render(block):
    rows = [line[2:-2].rstrip() for line in block.splitlines()
            if line.startswith('| ') and line.endswith(' |')]
    if len(rows) < 2 or rows[0] != 'CLI-MODE':
        raise ValueError('Expected a formatted CLI-MODE menu.')
    ident = 'cli-menu-' + uuid.uuid4().hex
    body = []
    warning = False
    for row in rows[2:]:
        marked = row.startswith('*')
        is_warning = warning or marked
        if row.count('**') % 2:
            warning = not warning
        content = escape(row)
        if is_warning:
            content = '<span class="warning">' + content + '</span>'
        elif row.startswith('X. '):
            content = 'X. <span class="exit">' + escape(row[3:]) + '</span>'
        elif re.fullmatch(r'(Hooks|Full Access): (On|!Attention!)', row):
            label, value = row.split(': ', 1)
            style = 'ok' if value == 'On' else 'warning'
            content = escape(label) + ': <span class="' + style + '">' + escape(value) + '</span>'
        elif row.endswith(': Installed'):
            content = escape(row[:-9]) + '<span class="ok">Installed</span>'
        elif row.endswith(': Needs installation'):
            content = escape(row[:-18]) + '<span class="warning">Needs installation</span>'
        elif row.startswith('/'):
            content = '<span class="command">' + content + '</span>'  # Help's command rows.
        body.append('<div class="row">' + (content or '&#160;') + '</div>')
    # The Codex inline surface supplies theme tokens; the original dark card
    # palette is each token's fallback, so other renderers look unchanged.
    return ('<section id="' + ident + '" aria-label="CLI-MODE ' + escape(rows[1], quote=True) + '">\n'
            '<style>\n'
            '#' + ident + '{color:' + CARD_TEXT + ';background:' + CARD + ';border:1px solid ' + BORDER + ';'
            'box-sizing:border-box;max-width:40ch;width:100%;font-family:ui-monospace,Consolas,monospace;'
            'font-size:14px;line-height:1.5;overflow-wrap:anywhere;}\n'
            '#' + ident + ' .heading{padding:10px 12px;border-bottom:1px solid ' + BORDER + ';}\n'
            '#' + ident + ' .title{color:' + GREEN + ';font-weight:700;}\n'
            '#' + ident + ' .subtitle{color:' + CARD_TEXT + ';font-weight:700;}\n'
            '#' + ident + ' .body{padding:10px 12px;}\n'
            '#' + ident + ' .row{min-width:0;white-space:pre-wrap;}\n'
            '#' + ident + ' .ok{color:' + GREEN + ';}\n'
            '#' + ident + ' .command{font-weight:700;}\n'
            '#' + ident + ' .warning,#' + ident + ' .exit{color:' + RED + ';}\n'
            '</style>\n<header class="heading"><div class="title">CLI-MODE</div>'
            '<div class="subtitle">' + escape(rows[1]) + '</div></header>\n'
            '<div class="body">\n' + '\n'.join(body) + '\n</div>\n</section>\n')


def write(block, destination):
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(block), encoding='utf-8')
    return str(path)


def message_content(text):
    """Allow a small inline vocabulary without executing HTML or creating links."""
    token = re.compile(r'```[\s\S]*?```|`[^`\n]+`|\*\*[^*\n]+\*\*|'
                       r'\[[^\]\n]+\]\\?\((?:file:///|[A-Za-z]:[/\\]|/)[^\n]*?\)')
    result, offset = [], 0
    for match in token.finditer(text):
        result.append(escape(text[offset:match.start()]))
        value = match.group()
        if value.startswith('```'):
            result.append(escape(value))
        elif value.startswith('`'):
            result.append('<code>' + escape(value[1:-1]) + '</code>')
        elif value.startswith('**'):
            result.append('<strong>' + escape(value[2:-2]) + '</strong>')
        else:
            # Keep the provider's visible label, removing unsupported file-link syntax.
            result.append('<code>' + escape(value[1:value.index(']')]) + '</code>')
        offset = match.end()
    result.append(escape(text[offset:]))
    return ''.join(result)


def block_content(text):
    """Render a bounded Markdown vocabulary; raw HTML and links stay inert."""
    lines = text.splitlines()
    item = re.compile(r'^( *)([-+*]|\d+[.)])\s+(.*)$')
    def cells(line):
        return [cell.strip() for cell in line.strip().strip('|').split('|')]
    def listing(index, indent, depth=0):
        ordered = item.match(lines[index])[2][0].isdigit()
        tag = 'ol' if ordered else 'ul'
        rows = []
        while index < len(lines):
            match = item.match(lines[index])
            if not match or len(match[1]) != indent or match[2][0].isdigit() != ordered:
                break
            content = message_content(match[3])
            index += 1
            while index < len(lines):
                child = item.match(lines[index])
                if child and len(child[1]) > indent and depth < 20:
                    nested, index = listing(index, len(child[1]), depth + 1)
                    content += nested
                elif lines[index].strip() and not child and len(lines[index]) - len(lines[index].lstrip()) > indent:
                    content += ' ' + message_content(lines[index].strip())
                    index += 1
                else:
                    break
            rows.append('<li>' + content + '</li>')
        return '<' + tag + '>' + ''.join(rows) + '</' + tag + '>', index
    out, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = re.match(r'^\s*(`{3,}|~{3,})([^`~]*)$', line)
        if fence:
            code = []
            index += 1
            while index < len(lines) and not re.fullmatch(r'\s*' + re.escape(fence[1][0]) + '{' + str(len(fence[1])) + r',}\s*', lines[index]):
                code.append(lines[index]); index += 1
            out.append('<pre><code>' + escape('\n'.join(code)) + '</code></pre>')
            index += 1
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading:
            level = str(len(heading[1]))
            out.append('<h' + level + '>' + message_content(heading[2]) + '</h' + level + '>')
            index += 1
            continue
        if item.match(line):
            rendered, index = listing(index, len(item.match(line)[1]))
            out.append(rendered)
            continue
        if index + 1 < len(lines) and '|' in line and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells(lines[index + 1])):
            headers = cells(line)
            out.append('<div class="table-scroll" role="region" aria-label="Results table" tabindex="0"><table><thead><tr>' +
                       ''.join('<th scope="col">' + message_content(c) + '</th>' for c in headers) + '</tr></thead><tbody>')
            index += 2
            while index < len(lines) and '|' in lines[index] and lines[index].strip():
                row = cells(lines[index])
                out.append('<tr>' + ''.join('<td>' + message_content(c) + '</td>' for c in row) + '</tr>')
                index += 1
            out.append('</tbody></table></div>')
            continue
        if line.startswith('> '):
            out.append('<blockquote>' + message_content(line[2:]) + '</blockquote>')
        else:
            out.append('<p>' + message_content(line) + '</p>')
        index += 1
    return ''.join(out)


def block_styles(ident):
    """Scoped styles shared by message and final relay views."""
    prefix = '#' + ident + ' '
    rules = (
        '.body{white-space:normal;min-width:0;}',
        '.body p{margin:.5em 0;}',
        '.body h1,.body h2,.body h3,.body h4,.body h5,.body h6{font-size:1.1em;line-height:1.35;margin:1em 0 .4em;}',
        '.body ul,.body ol{padding-left:1.4em;margin:.35em 0;}',
        '.body li{margin:.2em 0;}',
        '.body pre{white-space:pre;overflow-x:auto;padding:.7em;background:var(--card,#202124);color:var(--card-foreground,#fff);border-radius:6px;}',
        '.body .table-scroll{max-width:100%;overflow-x:auto;margin:.7em 0;}',
        '.body table{border-collapse:collapse;font-size:.9em;width:max-content;min-width:100%;}',
        '.body th,.body td{padding:.35em .6em;border:1px solid var(--border,#92979b);text-align:left;white-space:nowrap;overflow-wrap:normal;}',
        '.body blockquote{margin:.5em 0;padding-left:1em;border-left:2px solid var(--border,#92979b);}',
    )
    return ''.join(','.join(prefix + selector for selector in rule.split('{', 1)[0].split(',')) +
                   '{' + rule.split('{', 1)[1] for rule in rules)


def activation_content(text):
    """Turn the stable activation text contract into responsive label/value rows."""
    title, settings, usage = text.split('\n\n', 2)
    rows = []
    for row in settings.split(' | '):
        match = re.match(r'\*\*([^*]+):\*\* (.*)', row)
        if match:
            rows.append('<div><dt>' + escape(match[1]) + '</dt><dd>' + message_content(match[2]) + '</dd></div>')
    usage = usage.removeprefix('**Utilization:** ')
    return ('<div class="activation-title">' + message_content(title) + '</div><dl>' + ''.join(rows) +
            '</dl><strong>Utilization</strong><ul class="usage-rows">' + ''.join(
                '<li>' + message_content(row.strip()) + '</li>' for row in usage.splitlines()) + '</ul>')


# CLI-MODE's own voice is tinted; the agent's own words are not, so a long
# answer stays as readable as the rest of the conversation.
#
# Two accents, because no single green clears WCAG AA on both a dark and a
# light chat background: #42d392 measures 8.7:1 on dark but 1.9:1 on white.
# The message view is transparent, so it adapts to the host theme. The menu
# view ships its own fixed dark card and keeps the dark accent in both themes;
# swapping it there would drop 8.4:1 to 3.0:1.
ACCENT = '#42d392'
ACCENT_LIGHT = '#0b7a55'


# Codex's inline visualization surface defines theme tokens (--green follows the
# host theme through light-dark()). Each keeps the tuned palette as fallback.
GREEN = 'var(--green,' + ACCENT + ')'
GREEN_LIGHT = 'var(--green,' + ACCENT_LIGHT + ')'
RED = 'var(--red,#ff4d4f)'
CARD = 'var(--card,#202124)'
CARD_TEXT = 'var(--card-foreground,#fff)'
BORDER = 'var(--border,#92979b)'


def reference(path):
    """The exact line Codex renders as an inline view, on a line of its own."""
    return '\ue200visualize\ue202' + json.dumps({'path': str(Path(path).resolve())}) + '\ue201'


def write_message(text, destination, label=None, kind='agent'):
    """Render safe inline formatting; provider markup never executes.

    `kind` picks the voice. CLI-MODE's own lines (`activation`, `passing`) are
    accent-tinted. A relayed agent message keeps the host's default text colour
    and is introduced by one tinted attribution line, so you can always tell
    who is speaking without losing contrast on the content itself.
    """
    chrome = kind in ('activation', 'passing')
    typography = ('font-family:ui-monospace,Consolas,monospace;font-size:14px;font-variant-ligatures:none;'
                  if kind == 'activation' else 'font-family:inherit;font-size:inherit;')
    ident = 'cli-message-' + uuid.uuid4().hex
    attribution = ''
    if not chrome and label:
        attribution = ('<div class="attrib">' + escape(label) + ' says...</div>')
    # Only CLI-MODE's own lines are tinted, so only they follow the theme.
    accent_rule = ('#' + ident + '{color:' + GREEN + ';}') if chrome else (
        '#' + ident + ' .attrib{color:' + GREEN + ';font-weight:700;}')
    light_rule = ('#' + ident + '{color:' + GREEN_LIGHT + ';}') if chrome else (
        '#' + ident + ' .attrib{color:' + GREEN_LIGHT + ';}')
    fragment = ('<section id="' + ident + '" aria-label="' +
        escape((label + ' message') if label and not chrome else 'CLI-MODE message', quote=True) + '">\n'
        '<style>#' + ident + '{' + ('' if chrome else 'color:inherit;') + 'background:transparent;'
        + typography + 'line-height:1.5;'
        'white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;}'
        + accent_rule +
        '#' + ident + ' code{color:inherit;background:transparent;font-family:ui-monospace,Consolas,monospace;}'
        '#' + ident + ' strong{color:inherit;font-weight:700;}'
        '@media (prefers-color-scheme: light){' + light_rule + '}'
        + block_styles(ident) + '#' + ident + ' dl{margin:.8em 0;}'
        '#' + ident + ' dl>div{display:grid;grid-template-columns:6em minmax(0,1fr);gap:.5em;margin:.35em 0;}'
        '#' + ident + ' dt{font-weight:700;}#' + ident + ' dd{margin:0;}'
        '#' + ident + ' .usage-rows{padding-left:1.3em;margin:.3em 0;}'
        '</style>' + attribution + '<div class="body">' +
        (activation_content(text) if kind == 'activation' and text.startswith('**CLI-MODE Activated**')
         else block_content(text)) + '</div></section>\n')
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fragment, encoding='utf-8', newline='')
    return str(path)


def write_progress(source, destination, label):
    """Render a bounded snapshot of a public JSONL log, including while it grows.

    Stable tool IDs replace rows within a snapshot. Ignore the unfinished last
    line, unknown fields and all non-public event types. Never read ACPX history.
    """
    relay = ActivityRelay()
    ended = False
    with Path(source).open(encoding='utf-8-sig') as stream:
        for line in stream:
            if not line.endswith('\n'):
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            relay.feed(event)
            ended |= event.get('type') == 'done'
    tools = list(relay.tools.values())
    counts = {status: sum(event['status'] == status for event in tools) for status in STATUSES}
    summary = ', '.join(str(value) + ' ' + STATUSES[key].lower() for key, value in counts.items() if value)
    # Keep unresolved tools visible; otherwise prefer the newest reported work.
    rows = sorted(reversed(tools), key=lambda event: event['status'] in TERMINAL)[:20]
    texts = [activity_text(event) for event in rows]
    usage = usage_text(relay.usage) if relay.usage else ''
    note = 'Turn ended; tool states are last reported.' if ended and any(e['status'] not in TERMINAL for e in tools) else ''
    omitted = str(len(tools) - len(rows)) + ' other tools in the public log.' if len(tools) > len(rows) else ''
    fallback = '\n'.join(filter(None, [label + ' activity', summary, *texts, omitted, usage, note]))
    if not rows and not usage:
        fallback += '\nNo public tool activity or usage reported.'
    ident = 'cli-progress-' + uuid.uuid4().hex
    body = ''.join('<li><strong>' + escape(STATUSES[event['status']]) + '</strong> ' +
                   escape(text.split(': ', 1)[1]) + '</li>' for event, text in zip(rows, texts))
    fragment = ('<section id="' + ident + '" aria-label="' + escape(label + ' activity', quote=True) + '">'
        '<style>#' + ident + '{color:inherit;background:transparent;font:inherit;line-height:1.5;overflow-wrap:anywhere;}'
        '#' + ident + ' h3{font:inherit;font-weight:700;color:' + GREEN + ';margin:0 0 4px;}'
        '#' + ident + ' ul{padding-left:1.3em;margin:4px 0;}'
        '#' + ident + ' p{margin:4px 0;}'
        '@media (prefers-color-scheme:light){#' + ident + ' h3{color:' + GREEN_LIGHT + ';}}'
        '</style><h3>' + escape(label + ' activity') + '</h3>' +
        ('<p>' + escape(summary) + '</p>' if summary else '') + '<ul>' + body + '</ul>' +
        ''.join('<p>' + escape(text) + '</p>' for text in (omitted, usage, note) if text) +
        ('<p>No public tool activity or usage reported.</p>' if not rows and not usage else '') + '</section>\n')
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fragment, encoding='utf-8', newline='')
    return dict(text=fallback, toolCount=len(tools), messageView=dict(
        path=str(path), format='inline-html', label=label, kind='activity'))
