"""The formatting corpus: every public event type and every content case, as one agent turn.

The validation plan's P1-P5. `events()` builds the turn an agent could send; the
fixture file `checks/fixtures/formatting-corpus.jsonl` holds it with message IDs
(Claude, Codex) and `formatting-corpus-noid.jsonl` without them (Grok, Copilot,
Antigravity). `render_all()` sends a turn through the four renderers:

- R1 Codex mid-turn Markdown (`relay` -> `markdown`);
- R2 Codex final HTML view (`relay` -> `messageView`);
- R3 Claude Code final text (`relay_text`);
- R4 the agent viewer window (`viewer.ps1`, output captured).

R1-R3 go through a real Controller and Store, so a request's log is read the way
a live relay reads it. `test_formatting_corpus.py` checks each case; the live
formatting prompt (`formatting_prompt.py`) reuses `render_all()` on a real turn.

    python checks/formatting_corpus.py [--write-fixtures] [--render <events.jsonl> <folder>]
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

CHECKS = Path(__file__).resolve().parent
PLUGIN = CHECKS.parent / 'plugins' / 'cli-mode'
sys.path.insert(0, str(PLUGIN / 'scripts'))
FIXTURES = CHECKS / 'fixtures'
WORKSPACE = 'C:/work/garden-app'

# Every P2 content case, in the order the answer sends them. The unclosed fence ends the answer,
# since everything after it is code.
CASES = {
    'headings': '# H1 title\n\n## H2 title\n\n### H3 title\n\n#### H4 title\n\n##### H5 title\n\n###### H6 title',
    'lists': ('- Changes\n  - Tests\n    - Three passed\n- Docs\n\n'
              '1. First\n2. Second\n   1. Second, part a\n   - Second, note'),
    'tasks': '- [x] Done task\n- [ ] Open task',
    'table-narrow': '| Case | Result |\n|---|---|\n| Empty | **Pass** |\n| Long | `fail` |',
    'table-wide': ('| Name | Kind | Status | Owner | Location | Detail | Seconds | Note |\n'
                   '|---|---|---|---|---|---|---|---|\n'
                   '| multiply | function | added | agent | calc.py:4 | returns the product of both arguments | 0.01 '
                   '| a deliberately long cell that makes the table wider than a phone screen |'),
    'table-aligned': '| Left | Centre | Right |\n|:---|:---:|---:|\n| a | b | c |\n| long left | mid | 1,234 |',
    'code': ('```python\ndef add(a, b):\n    return a + b\n```\n\n'
             '```powershell\nGet-ChildItem -Recurse | Select-Object Name\n```\n\n'
             '```json\n{"ok": true, "items": [1, 2]}\n```'),
    'inline': 'Run `pytest -q` with **bold**, *italic*, _underscored_ and ~~struck~~ words.',
    'quote': '> Quoted advice from the agent.',
    'links': 'See [the docs](https://example.com/docs) and [calc.py](file:///C:/work/garden-app/calc.py).',
    'html': '<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n[click](javascript:alert(3))',
    'view-reference': ('visualize{"path": "C:/evil.html"}\n   ::codex-inline-vis{file="x"}\n'
                       'inline \ue200visualize\ue202{"path": "C:/evil.html"}\ue201 reference'),
    'long-line': 'Long line: ' + ' '.join('word%03d' % index for index in range(120)) + ' end.',
    'long-token': 'Token: ' + 'x' * 180 + ' end.',
    'unicode': 'Emoji \U0001f389\u2705\U0001f680, accents caf\u00e9 na\u00efve, CJK \u6f22\u5b57, '
               'RTL \u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd and \u0645\u0631\u062d\u0628\u0627.',
    'dollars': 'It costs $5 and $10; set $HOME and $PATH first.',
    'crlf': 'First CRLF line\r\nSecond CRLF line',
    'escapes': 'Before \x1b]0;evil title\x07after, \x1b[31mred\x1b[0m text\x1b[2A moved.',
    'unclosed-fence': '```bash\necho unclosed',
}
PREAMBLE = "I'll look at calc.py first, then answer."
LABEL = 'Antigravity'  # The adapter the offline Controller binds to; its label heads every relay.


def answer():
    return '\n\n'.join(CASES.values()) + '\n'


def chunks(text, size=97):
    """Agents stream their answer in pieces; split mid-line, mid-row and mid-CRLF on purpose."""
    return [text[index:index + size] for index in range(0, len(text), size)]


def events(message_ids=True):
    """One turn with every public event type."""
    ident = (lambda value: {'messageId': value}) if message_ids else (lambda value: {})
    where = [dict(path=WORKSPACE + '/calc.py', line=1)]
    turn = [
        dict(type='plan', entries=[dict(content='Read calc.py', status='completed'),
                                   dict(content='Write the answer', status='in_progress'),
                                   dict(content='Check the tests', status='pending')]),
        dict(type='message', text=PREAMBLE + '\n', **ident('m1')),
        dict(type='activity', toolCallId='t1', kind='read', status='in_progress', title='Read calc.py', locations=where),
        dict(type='activity', toolCallId='t1', kind='read', status='completed', title='Read calc.py', locations=where),
        dict(type='activity', toolCallId='t2', kind='execute', status='completed', title='Run Python tests (unittest)'),
        dict(type='activity', toolCallId='t3', kind='edit', status='failed', title='Edit README.md',
             locations=[dict(path=WORKSPACE + '/README.md')]),
        dict(type='activity', toolCallId='t4', kind='search', status='in_progress', title='Search for multiply'),
        dict(type='artifact', content=dict(type='resource_link', name='report.txt', uri='file:///C:/work/garden-app/report.txt')),
        dict(type='artifact', content=dict(type='image', mimeType='image/png', uri='file:///C:/work/garden-app/chart.png')),
    ]
    turn += [dict(type='message', text=piece, **ident('m2')) for piece in chunks(answer())]
    turn += [
        dict(type='usage', used=41234, size=200000),
        dict(type='context_warning', message='Context is 80% full.'),
        dict(type='error', message='Search for multiply did not finish.'),
        dict(type='done', stopReason='end_turn'),
    ]
    return turn


def write_events(path, turn):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in turn), encoding='utf-8',
                    newline='\n')
    return path


def read_events(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def fixture(message_ids=True):
    return FIXTURES / ('formatting-corpus.jsonl' if message_ids else 'formatting-corpus-noid.jsonl')


def settled_controller(root, turn, progress='activity'):
    """A Controller whose one request has already settled with `turn` as its public log."""
    from controller import Controller
    from state import Store
    from test_controller import FakeBackend
    (Path(root) / 'workspace').mkdir(parents=True, exist_ok=True)
    control = Controller(Store('corpus', Path(root) / 'workspace', Path(root) / 'state'), FakeBackend())
    control.frontend()
    control.activate('gemini-3.8-flash-high', 'allow')
    control.progress(progress)
    request = uuid.uuid4().hex
    store = control.store
    folder = store.request_path(request).parent / 'operations'
    # The corpus's paths sit in its own workspace; move them into this store's, so rows show relative paths.
    moved = json.loads(json.dumps(turn))
    for event in moved:
        for location in event.get('locations') or []:
            location['path'] = location['path'].replace(WORKSPACE, Path(store.workspace).as_posix())
    log = write_events(folder / (request + '.jsonl'), [event for event in moved if event.get('type') != 'done'])
    with store.edit() as state:
        store.capture(state, request, 'formatting corpus')
        state['requests'][request].update(status='completed', events=str(log), submittedAt=time.time())
    return control, request


def render_r1_r3(turn, folder, progress='activity', color=True):
    """R1 and R2 through the Codex relay, R3 through the Claude Code relay."""
    import host
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    saved = os.environ.get('CLI_MODE_HOST')
    try:
        host.select(host.CODEX)
        control, request = settled_controller(folder / 'codex', turn, progress)
        codex = control.relay(request, 0, wait=1, view_dir=folder / 'views')
        host.select(host.CLAUDE)
        control, request = settled_controller(folder / 'claude', turn, progress)
        (control.store.root / 'display.json').write_text(json.dumps({'color': 'on' if color else 'off'}), encoding='utf-8')
        pieces, cursor = [], 0
        for _ in range(20):  # A long answer comes in parts.
            claude = control.relay_chain([request], cursor, wait=1)
            pieces.append(claude['text'])
            cursor = claude['cursor']
            if claude['done']:
                break
    finally:
        if saved is None:
            os.environ.pop('CLI_MODE_HOST', None)
        else:
            os.environ['CLI_MODE_HOST'] = saved
    html = Path(codex['messageView']['path']).read_text(encoding='utf-8')
    out = dict(r1=codex['markdown'], r2=html, r2Text=codex['text'], r2Path=codex['messageView']['path'],
               r3='\n\n'.join(pieces), r3Parts=len(pieces))
    for key, suffix in (('r1', '-r1.md'), ('r2', '-r2.html'), ('r3', '-r3.md')):
        (folder / (progress + suffix)).write_text(out[key], encoding='utf-8', newline='')
    return out


def render_r4(turn, folder, shell=None, agent='Antigravity', seconds=4):
    """R4: viewer.ps1 on the turn, output captured. Returns (stdout without colour codes, raw stdout, stderr)."""
    import viewer
    shell = shell or viewer.shell()
    folder = Path(folder)
    operations = folder / 'operations'
    operations.mkdir(parents=True, exist_ok=True)
    write_events(operations / 'op.jsonl', turn)
    (operations / 'op.txt').write_text('formatting corpus prompt', encoding='utf-8')
    settings, beat = folder / 'viewer.json', folder / 'viewer.alive'
    settings.write_text(json.dumps({'view': 'on'}), encoding='utf-8')
    process = subprocess.Popen(
        [shell, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(PLUGIN / 'scripts/viewer.ps1'),
         '-Folder', str(operations), '-Heartbeat', str(beat), '-Settings', str(settings), '-Agent', agent,
         '-Workspace', WORKSPACE],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace')
    try:
        until = time.monotonic() + 40
        while not beat.exists() and time.monotonic() < until and process.poll() is None:
            time.sleep(.1)
        time.sleep(seconds)
        settings.write_text(json.dumps({'view': 'off'}), encoding='utf-8')
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    plain = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', out)
    (folder / 'r4.txt').write_text(plain, encoding='utf-8')
    return plain, out, err, process.returncode, beat.exists()


def latex_risks(text):
    """`$...$` pairs in agent text that Claude Code's desktop app may draw as maths.

    Its guard (CLAUDE.md): at most 60 characters inside, no '#', '@', '"' or '`'. Whether it also
    needs no space after the opening '$' and none before the closing one is for Phase 5's eyes.
    """
    risks = []
    for paragraph in re.split(r'\n\s*\n', text):
        for match in re.finditer(r'\$([^$\n]{1,60})\$', paragraph):
            inner = match.group(1)
            if not re.search(r'[#@"`]', inner):
                risks.append(match.group(0))
    return risks


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write-fixtures', action='store_true')
    parser.add_argument('--render', nargs=2, metavar=('EVENTS', 'FOLDER'))
    parser.add_argument('--shell', help='PowerShell for R4 (default: the one the viewer would choose)')
    args = parser.parse_args()
    if args.write_fixtures:
        for ids in (True, False):
            print(write_events(fixture(ids), events(ids)))
    if args.render:
        turn = read_events(args.render[0])
        folder = Path(args.render[1])
        for mode in ('activity', 'quiet'):
            render_r1_r3(turn, folder / 'relay', mode)
        plain, _, err, code, _ = render_r4(turn, folder / 'viewer', args.shell)
        print(json.dumps(dict(folder=str(folder), viewerExit=code, viewerErrors=err[-400:],
                              latexRisks=latex_risks(''.join(e.get('text', '') for e in turn if e.get('type') == 'message')))))


if __name__ == '__main__':
    main()
