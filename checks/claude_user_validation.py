"""P7b: user validation of the INSTALLED CLI-MODE plugin, driven through real Claude Code sessions.

Only the installed copy runs. Before starting, the build, the installed marketplace
folder and Claude Code's cache must be identical. Every turn uses the desktop app's
own Claude Code with your real configuration (no --plugin-dir, no CLAUDE_CONFIG_DIR,
no CLI_MODE_* variables). After each turn it asserts:
- the hook that answered was the installed copy (state's hookSeen.plugin);
- every controller command Claude ran points into the installed copy;
- no permission denials, no subagents, no commands other than CLI-MODE's controller.

It spends real quota: Claude turns on your plan, and the agent's own account.
Evidence (every turn's events) goes to %TEMP%\\cli-mode-user-validation-<time>.

    python checks/claude_user_validation.py [--agent codex] [--keep]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
INSTALLED = Path(os.path.expandvars(r'%LOCALAPPDATA%\CLI-MODE\claude-marketplace\plugins\cli-mode'))
CACHE = Path.home() / '.claude' / 'plugins' / 'cache' / 'cli-mode' / 'cli-mode'
DATA = Path.home() / '.claude' / 'plugins' / 'data' / 'cli-mode-cli-mode'
OPTION = re.compile(r'^\|?\s*(\d+)\.\s+(.+?)\s*\|?\s*$')
VERSION = json.loads((PROJECT / 'plugins/cli-mode/.codex-plugin/plugin.json').read_text(encoding='utf-8'))[
    'version'].split('+')[0]
BUILD = PROJECT / 'dist' / ('cli-mode-claude-' + VERSION) / 'plugins' / 'cli-mode'


def fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and '.in_use' not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()[:16]


def desktop_claude():
    versions = sorted(Path(os.environ['APPDATA'], 'Claude', 'claude-code').glob('*/claude.exe'),
                      key=lambda path: [int(part) for part in path.parent.name.split('.') if part.isdigit()])
    return str(versions[-1]) if versions else shutil.which('claude')


def norm(path):
    return str(path).replace('\\', '/').rstrip('/').casefold()


class Session:
    def __init__(self, binary, project, evidence):
        self.binary, self.project, self.evidence = binary, project, evidence
        self.id = str(uuid.uuid4())
        self.started = False
        self.spent = 0.0
        self.turns = []
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('CLI_MODE_', 'CLAUDE_PLUGIN_')) and key != 'CLAUDE_CONFIG_DIR'}

    def state(self):
        path = DATA / 'sessions' / (hashlib.sha256(self.id.encode()).hexdigest() + '.json')
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}

    def send(self, prompt, scenario, kill_after=None, extra=()):
        args = [self.binary, '-p', prompt, '--output-format', 'stream-json', '--verbose',
                '--resume' if self.started else '--session-id', self.id, *extra]
        self.started = True
        began = time.monotonic()
        process = subprocess.Popen(args, cwd=self.project, env=self.env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   errors='replace')
        killed = False
        try:
            out, err = process.communicate(timeout=kill_after or 900)
        except subprocess.TimeoutExpired:
            # Simulate the user stopping or closing mid-relay: end Claude Code and its shell children.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            out, err = process.communicate()
            killed = True
        events = [json.loads(line) for line in out.splitlines() if line.startswith('{')]
        final = next((event for event in reversed(events) if event.get('type') == 'result'), {})
        texts, tools, errors, order, parts = [], [], [], [], False
        for event in events:
            if event.get('type') == 'user':  # A failed controller command prints {"error": ...}.
                for block in (event.get('message') or {}).get('content') or []:
                    body = block.get('content') if isinstance(block, dict) else None
                    body = body if isinstance(body, str) else ''.join(
                        part.get('text', '') for part in body or [] if isinstance(part, dict))
                    try:
                        found = json.loads(body[body.index('{'):]) if '{"error"' in body else {}
                    except ValueError:
                        found = {}
                    if isinstance(found, dict) and found.get('error'):
                        errors.append(found['error'])
                    parts = parts or 'comes in parts' in body  # A long answer's earlier parts are posted mid-turn.
            if event.get('type') != 'assistant':
                continue
            for block in (event.get('message') or {}).get('content') or []:
                if block.get('type') == 'text':
                    texts.append(block['text'])
                    order.append('text')
                elif block.get('type') == 'tool_use':
                    tools.append(dict(name=block.get('name'), command=(block.get('input') or {}).get('command') or ''))
                    order.append('tool')
        total = final.get('total_cost_usd') or self.spent
        turn = dict(scenario=scenario, prompt=prompt, seconds=round(time.monotonic() - began, 1), killed=killed,
                    modelTurns=final.get('num_turns'), costUsd=round(max(total - self.spent, 0), 4),
                    denials=final.get('permission_denials') or [], tools=tools, texts=texts, errors=errors,
                    result=final.get('result') or '', problems=[], notes=[])
        self.spent = max(self.spent, total)
        state = self.state()
        turn['route'] = (state.get('turnRoute') or {}).get('route')
        # Only the installed copy may run.
        seen = (state.get('hookSeen') or {}).get('plugin')
        if seen and norm(seen) != norm(INSTALLED):
            turn['problems'].append('hook ran from ' + seen)
        for tool in tools:
            command = tool['command']
            if tool['name'] in ('Agent', 'Task'):
                turn['problems'].append('used a subagent')
            elif tool['name'] in ('Bash', 'PowerShell'):
                paths = re.findall(r"'?([A-Za-z]:[/\\][^' ]*controller\.py)'?", command)
                if not paths:
                    turn['problems'].append('ran a non-CLI-MODE command: ' + command[:160])
                for path in paths:
                    if not norm(path).startswith(norm(INSTALLED)):
                        turn['problems'].append('controller outside the installed copy: ' + path)
            elif tool['name'] == 'Skill':
                turn['notes'].append('opened the CLI-MODE skill')
            else:
                turn['problems'].append('used ' + str(tool['name']))
        # A relayed request is announced once, however many relay calls it takes (run 2 showed it twice).
        relayed = {request for tool in tools for request in re.findall(r'--request ([0-9a-f]{32})', tool['command'])}
        passing = plain('\n\n'.join(texts)).count('**Passing to ')
        if passing > len(relayed):
            turn['problems'].append('"Passing to" posted %d times for %d relayed requests' % (passing, len(relayed)))
        # The desktop app folds text between tool calls out of view: a relayed turn opens with its "Passing to"
        # line before any call, posts nothing between calls, and ends with the agent's output.
        if relayed and 'tool' in order:
            first, last = order.index('tool'), len(order) - 1 - order[::-1].index('tool')
            if turn['route'] in ('direct', 'delegate') and not (first and '**Passing to ' in plain(texts[0])):
                turn['problems'].append('"Passing to" was not posted before the first relay call')
            if 'text' in order[first:last] and not parts:
                turn['problems'].append('text posted between relay calls, where the app folds it out of view')
        if turn['denials']:
            turn['problems'].append('permission denials: ' + json.dumps(turn['denials'])[:200])
        if not final and not killed:
            turn['problems'].append('no result: ' + err[-400:])
        (self.evidence / ('%02d-turn.json' % (len(self.turns) + 1))).write_text(
            json.dumps(dict(turn, events=events), indent=1), encoding='utf-8')
        self.turns.append(turn)
        return turn

    def shown(self, turn):
        """Everything the user saw for a turn: Claude's messages, or the hook's own reply. Coloured
        titles and names (LaTeX in Claude Code's chat) read as the bold words they display."""
        return plain('\n\n'.join(turn['texts']) or turn['result'])


def plain(text):
    """Posted text with the installed plugin's coloured titles turned back into **bold**."""
    sys.path.insert(0, str(INSTALLED / 'scripts'))
    from presentation import plain_strong
    return plain_strong(text)


def expect(turn, condition, message):
    if not condition:
        turn['problems'].append(message)


def options(text):
    rows = []
    for line in text.splitlines():
        match = OPTION.match(line.strip())
        if match:
            rows.append((match.group(1), match.group(2).strip()))
    return rows


def agent_words(session, request):
    """The agent's last message for a request, rebuilt from CLI-MODE's public event log."""
    sys.path.insert(0, str(INSTALLED / 'scripts'))
    import relay_view
    record = (session.state().get('requests') or {}).get(request) or {}
    path = Path(record.get('events') or '')
    if not path.is_file():
        return ''
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return relay_view.messages(events, last_only=True).strip()


def verbatim(session, turn, request):
    words = agent_words(session, request)
    turn['notes'].append('agent words: ' + words[:120].replace('\n', ' / '))
    expect(turn, words and words in session.shown(turn), 'agent\'s final words not posted verbatim')


def run(agent, keep):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    evidence = Path(tempfile.gettempdir()) / ('cli-mode-user-validation-' + stamp)
    evidence.mkdir()
    project = evidence / 'garden-app'
    project.mkdir()
    (project / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    (project / 'README.md').write_text('# Garden app\n\nTracks planting dates. `calc.py` holds helpers.\n', encoding='utf-8')
    versions = sorted(CACHE.iterdir())
    copies = {'build': fingerprint(BUILD),
              'installed': fingerprint(INSTALLED), 'cache': fingerprint(versions[-1])}
    if len(set(copies.values())) != 1:
        raise SystemExit('The three copies differ; reinstall before validating: ' + json.dumps(copies))
    binary = desktop_claude()
    session = Session(binary, project, evidence)
    say = session.send
    sys.path.insert(0, str(INSTALLED / 'scripts'))
    os.environ['CLI_MODE_HOST'] = 'claude-code'
    import help_view
    import presentation

    # 1. Menus, as a first-time user of this agent, in the default chat display.
    t = say('/cli help', 'menus')
    card = plain(presentation.chat_menu(help_view.render(), True))  # Its title band in green, in the box.
    expect(t, card in session.shown(t), 'help card not posted exactly (green title band, fenced frame)')
    t = say('x', 'menus')
    expect(t, 'Help closed' in session.shown(t), 'help did not close')
    t = say('/cli', 'menus')
    home = options(session.shown(t))
    number = next((n for n, label in home if agent_label(agent) in label), None)
    expect(t, number, 'agent not on the home menu: ' + json.dumps(home))
    t = say(number or '1', 'menus')
    expect(t, 'Agent Settings' in session.shown(t) or 'Setup CLI Agent' in session.shown(t), 'no activation or setup page')
    t = say('2', 'menus')
    models = [(n, label) for n, label in options(session.shown(t)) if 'Refresh' not in label]
    expect(t, models, 'no model list')
    pick = next(((n, label) for n, label in models if 'current' not in label), models[0] if models else ('1', ''))
    t = say(pick[0], 'change models')
    expect(t, options(session.shown(t)), 'no effort list after choosing a model')
    t = say('1', 'change models')
    expect(t, options(session.shown(t)), 'no access list after choosing effort')
    t = say('1', 'change models')
    expect(t, 'CLI-MODE Activated' in session.shown(t), 'final access choice did not activate')
    chosen = pick[1].replace('(current)', '').strip()
    expect(t, chosen.split()[0] in session.shown(t), 'activation card does not show the chosen model ' + chosen)

    # 2. Changing settings while active.
    t = say('/cli menu', 'change models')
    expect(t, 'Agent Settings' in session.shown(t) and 'Model:' in session.shown(t), 'settings page missing')
    t = say('x', 'change models')
    other = next((label.replace('(current)', '').strip() for n, label in models
                  if 'current' in label and label.replace('(current)', '').strip() != chosen), None)
    if other:
        t = say('/cli model ' + other, 'change models')
        expect(t, 'CLI-MODE Activated' in session.shown(t) or 'Select Model' in session.shown(t),
               'direct model change gave neither a confirmation nor the menu')
    t = say('/cli effort low', 'change models')
    expect(t, 'Activated' in session.shown(t) or 'Effort' in session.shown(t), 'effort change not confirmed')

    # 3. Emissions and attribution on a real coding task.
    label = agent_label(agent, short=True)
    t = say('/d In calc.py add multiply(a, b), create test_calc.py with unittest tests for add and multiply, '
            'run the tests, and report the result in two sentences.', 'emissions')
    shown = session.shown(t)
    expect(t, 'Passing to' in shown, 'no passing line')
    expect(t, 'says...**' in shown, 'no attribution line')
    expect(t, ' work:' in shown or ' work ' in shown, 'no work summary line')
    verbatim(session, t, session.state()['turnRoute'].get('requestId'))
    expect(t, (project / 'test_calc.py').is_file(), 'the agent did not create test_calc.py')
    t = say('/cli progress quiet', 'emissions')
    t = say('/d Run the tests again and reply with the result in one sentence.', 'emissions')
    expect(t, ' work:' not in session.shown(t), 'quiet mode still showed work lines')
    verbatim(session, t, session.state()['turnRoute'].get('requestId'))
    t = say('/cli progress activity', 'emissions')

    # 4. Formatting in both display styles.
    t = say('/cli display instant', 'formatting')
    expect(t, t['modelTurns'] == 0 and 'instant replies' in t['result'], 'instant display not confirmed at $0')
    t = say('/cli menu', 'formatting')
    expect(t, t['modelTurns'] == 0 and '| CLI-MODE' in t['result'] and '```' not in t['result'],
           'instant menu not an unfenced frame at $0')
    t = say('x', 'formatting')
    t = say('/cli display chat', 'formatting')
    expect(t, 'normal chat messages' in session.shown(t), 'chat display not confirmed')

    # 5. Losing control mid-relay, resuming it, and queueing behind a running turn.
    long_task = ('/d Create five files one.txt to five.txt, each holding its own number written as a word, one file '
                 'at a time; then list the folder and describe what you made in three sentences.')
    t = say(long_task, 'queue and resume', kill_after=12)
    first = session.state()['turnRoute'].get('requestId')
    t['notes'].append('turn stopped after 12 s, as if the user pressed Esc; request ' + str(first))
    t = say('/cli queue', 'queue and resume')
    expect(t, first and first[:8] in session.shown(t), 'queue does not list the interrupted request')
    t = say('/cli resume', 'queue and resume')
    verbatim(session, t, first)
    t = say(long_task.replace('five files one.txt to five.txt', 'three files a.txt to c.txt').replace(
        'its own number', 'its letter'), 'queue and resume', kill_after=10)
    running = session.state()['turnRoute'].get('requestId')
    t = say('/d Reply with only the word queued.', 'queue and resume')
    queued = session.state()['turnRoute'].get('requestId')
    shown = session.shown(t)
    expect(t, 'Queued behind' in shown or 'queued' in shown.casefold(), 'the queued request was not reported as queued')
    verbatim(session, t, queued)
    # The interrupted request's output comes first, so nothing the agent did goes unseen.
    verbatim(session, t, running)
    t = say('/cli queue', 'queue and resume')
    t = say('/cli resume', 'queue and resume')

    # 6. Direct mode keeps host questions with Claude.
    before = len(session.state().get('requests') or {})
    t = say('What is 7 times 6? Reply with just the number.', 'host turns')
    expect(t, '42' in t['result'] and not t['tools'], 'host question was not answered by Claude alone')
    expect(t, len(session.state().get('requests') or {}) == before, 'host question was forwarded to the agent')

    # 7 and 8. Closing, error paths and rebinding.
    t = say('/cli frobnicate', 'errors')
    expect(t, 'help' in session.shown(t), 'unknown verb gave no hint')
    t = say('/cli stop', 'closing')
    state = session.state()
    expect(t, 'CLI-MODE is off' in session.shown(t), 'no shutdown message')
    expect(t, not state.get('active') and not state.get('owned'), 'agent still active or owned after stop')
    t = say('/cli queue', 'closing')
    expect(t, '/cli to activate' in session.shown(t), 'no inactive hint after stop')
    t = say('/d hello', 'closing')
    expect(t, '/cli to activate' in session.shown(t), '/d after stop was not refused')
    t = say('/cli bind cursor', 'errors')
    expect(t, not session.state().get('active'), 'a failed bind left an agent active')
    t['notes'].append('cursor bind reply: ' + session.shown(t)[:200].replace('\n', ' / '))
    flat = lambda text: ' '.join(text.split())  # noqa: E731  (Markdown may drop a line's trailing space)
    expect(t, t['errors'] and all(flat('CLI-MODE: ' + error) in flat(session.shown(t)) for error in t['errors']),
           'bind error not posted exactly as CLI-MODE: <error>')
    t = say('/cli bind ' + agent, 'closing')
    expect(t, 'CLI-MODE Activated' in session.shown(t), 'rebind failed')
    t = say('/cli stop', 'closing')
    expect(t, not session.state().get('active'), 'second stop left the agent active')

    report = dict(claudeCode=binary, copies=copies, session=session.id, evidence=str(evidence),
                  totalCostUsd=round(session.spent, 4), turns=len(session.turns))
    (evidence / 'summary.json').write_text(json.dumps(dict(report, turns_detail=[
        {key: turn[key] for key in ('scenario', 'prompt', 'route', 'seconds', 'modelTurns', 'costUsd', 'killed',
                                    'problems', 'notes')} for turn in session.turns]), indent=1), encoding='utf-8')
    print(json.dumps(report))
    for index, turn in enumerate(session.turns, 1):
        status = 'FAIL ' + '; '.join(turn['problems']) if turn['problems'] else 'ok'
        print('%02d %-16s %-44s %s %5ss $%-6s %s' % (index, turn['scenario'], turn['prompt'][:44],
              'hook ' if turn['modelTurns'] == 0 else 'model', turn['seconds'], turn['costUsd'], status))
        for note in turn['notes']:
            print('     note: ' + note[:170])
    if not keep:
        key = hashlib.sha256(session.id.encode()).hexdigest()
        for path in (DATA / 'sessions').glob(key + '*'):
            path.unlink(missing_ok=True)
        shutil.rmtree(project, ignore_errors=True)
        mangled = re.sub(r'[^A-Za-z0-9]', '-', str(project))
        shutil.rmtree(Path.home() / '.claude' / 'projects' / mangled, ignore_errors=True)
    return 1 if any(turn['problems'] for turn in session.turns) else 0


def agent_label(agent, short=False):
    names = {'codex': ('Codex CLI', 'Codex'), 'copilot': ('GitHub Copilot CLI', 'Copilot'), 'agy': ('Antigravity CLI',
             'Antigravity'), 'grok-build': ('Grok Build CLI', 'Grok'), 'claude': ('Claude Code CLI', 'Claude')}
    return names[agent][1 if short else 0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', default='codex', choices=('codex', 'copilot', 'agy', 'grok-build'))
    parser.add_argument('--keep', action='store_true', help='Keep the test project and session state.')
    args = parser.parse_args()
    raise SystemExit(run(args.agent, args.keep))
