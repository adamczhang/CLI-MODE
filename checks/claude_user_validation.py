"""P7b: user validation of the INSTALLED CLI-MODE plugin, driven through real Claude Code sessions.

Only the installed copy runs. Before starting, the build, the installed marketplace
folder and Claude Code's cache must be identical. Every turn uses the desktop app's
own Claude Code with your real configuration (no --plugin-dir, no CLAUDE_CONFIG_DIR,
no CLI_MODE_* variables) and, like the desktop app, bypassPermissions. After each turn
it asserts:
- the hook that answered was the installed copy (state's hookSeen.plugin);
- every controller command Claude ran points into the installed copy;
- no permission denials, no subagents, no commands other than CLI-MODE's controller.

The steps are the shared scenario in validation_scenarios.py (the Codex harness runs the
same ones): --depth full for every step, smoke for bind, one relayed coding turn, a model
change and stop. --extras adds the Claude-only checks (plan 2d): reset, long, attach,
permission, viewer, format, compact.

It spends real quota: Claude turns on your plan, and the agent's own account.
Evidence (every turn's events, summary.json) goes to %TEMP%\\cmv\\claude-<agent>-<time>.

    python checks/claude_user_validation.py [--agent codex] [--depth full|smoke] [--extras ...] [--keep]
"""
import argparse
import base64
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

import validation_scenarios as scenarios

PROJECT = Path(__file__).resolve().parents[1]
INSTALLED = Path(os.path.expandvars(r'%LOCALAPPDATA%\CLI-MODE\claude-marketplace\plugins\cli-mode'))
CACHE = Path.home() / '.claude' / 'plugins' / 'cache' / 'cli-mode' / 'cli-mode'
DATA = Path.home() / '.claude' / 'plugins' / 'data' / 'cli-mode-cli-mode'
VERSION = json.loads((PROJECT / 'plugins/cli-mode/.codex-plugin/plugin.json').read_text(encoding='utf-8'))[
    'version'].split('+')[0]
BUILD = PROJECT / 'dist' / ('cli-mode-claude-' + VERSION) / 'plugins' / 'cli-mode'
AGENTS = ('codex', 'copilot', 'agy', 'grok-build', 'claude', 'cursor')
EXTRAS = ('reset', 'long', 'attach', 'permission', 'viewer', 'format', 'compact')


def fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and '.in_use' not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()[:16]


def desktop_claude():
    # The Store (MSIX) app's %APPDATA% is virtualized: outside the app it lives in the package's LocalCache.
    roots = [Path(os.environ['APPDATA'], 'Claude', 'claude-code')] + [
        package / 'LocalCache' / 'Roaming' / 'Claude' / 'claude-code'
        for package in Path(os.environ['LOCALAPPDATA'], 'Packages').glob('Claude_*')]
    versions = sorted((found for root in roots for found in root.glob('*/claude.exe')),
                      key=lambda path: [int(part) for part in path.parent.name.split('.') if part.isdigit()])
    return str(versions[-1]) if versions else shutil.which('claude')


def norm(path):
    return str(path).replace('\\', '/').rstrip('/').casefold()


class Session:
    host = 'claude-code'

    def __init__(self, binary, project, evidence, bypass=True):
        self.binary, self.project, self.evidence = binary, project, evidence
        self.id = str(uuid.uuid4())
        self.started = False
        self.bypass = bypass
        self.spent = 0.0
        self.turns = []
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('CLI_MODE_', 'CLAUDE_PLUGIN_')) and key != 'CLAUDE_CONFIG_DIR'}

    def state(self):
        path = DATA / 'sessions' / (hashlib.sha256(self.id.encode()).hexdigest() + '.json')
        try:
            return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        except ValueError:
            return {}

    def state_file(self):
        return DATA / 'sessions' / (hashlib.sha256(self.id.encode()).hexdigest() + '.json')

    def send(self, prompt, scenario, kill_after=None, extra=(), content=None):
        """One `claude -p` turn. `content` sends a full user message (text and images) as stream-json."""
        args = [self.binary, '-p', *([] if content else [prompt]), '--output-format', 'stream-json', '--verbose',
                '--resume' if self.started else '--session-id', self.id, *extra]
        if self.bypass:
            args += ['--permission-mode', 'bypassPermissions']
        if content:
            args += ['--input-format', 'stream-json']
        self.started = True
        began = time.monotonic()
        process = subprocess.Popen(args, cwd=self.project, env=self.env,
                                   stdin=subprocess.PIPE if content else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   errors='replace')
        killed = False
        feed = (json.dumps(dict(type='user', message=dict(role='user', content=content))) + '\n') if content else None
        try:
            out, err = process.communicate(feed, timeout=kill_after or 900)
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
                    result=final.get('result') or '', problems=[], notes=[], parts=parts,
                    controllerCalls=sum(tool['name'] in ('Bash', 'PowerShell') for tool in tools))
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
            if turn['route'] == 'direct' and not (first and '**Passing to ' in plain(texts[0])):
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

    def words(self, request):
        return agent_words(self, request)


def plain(text):
    """Posted text with the installed plugin's coloured titles turned back into **bold**."""
    sys.path.insert(0, str(INSTALLED / 'scripts'))
    from presentation import plain_strong
    return plain_strong(text)


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


expect = scenarios.expect


def verbatim(session, turn, request):
    words = agent_words(session, request)
    turn['notes'].append('agent words: ' + words[:120].replace('\n', ' / '))
    expect(turn, words and words in session.shown(turn), 'agent\'s final words not posted verbatim')


def agent_label(agent, short=False):
    return scenarios.LABELS[agent][1 if short else 0]


def events_path(session, request):
    return ((session.state().get('requests') or {}).get(request) or {}).get('events')


def viewers(session):
    """Viewer windows following this session's operations folder."""
    key = hashlib.sha256(session.id.encode()).hexdigest()
    script = ("Get-CimInstance Win32_Process -Filter \"Name='pwsh.exe' or Name='powershell.exe'\" | "
              "Where-Object { $_.CommandLine -like '*viewer.ps1*' } | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    out = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True).stdout
    return [int(line.split('\t')[0]) for line in out.splitlines() if '\t' in line and key in line]


# ---- Plan 2d: Claude-only checks. Each returns result rows for the report.

def row(step, features, turns, extra_problems=()):
    problems = [p for turn in turns for p in turn['problems']] + list(extra_problems)
    return dict(step=step, features=features, status='fail' if problems else 'pass', problems=problems,
                notes=[n for turn in turns for n in turn['notes']])


def ensure_active(session, agent):
    if not session.state().get('active'):
        session.send('/cli bind ' + agent, 'extras')


def extra_reset(session, agent):
    ensure_active(session, agent)
    session.state_file().write_text('{not json', encoding='utf-8')
    a = session.send('/cli menu', 'reset')
    shown = session.shown(a)
    scenarios.expect(a, 'could not be read' in shown and '/cli reset' in shown, 'unreadable state not reported with /cli reset')
    b = session.send('/cli reset', 'reset')
    scenarios.expect(b, 'set aside' in session.shown(b), 'reset did not set the state aside')
    scenarios.expect(b, not session.state_file().exists(), 'state file still in place after reset')
    c = session.send('/cli queue', 'reset')
    scenarios.expect(c, '/cli to activate' in session.shown(c), 'state after reset is not fresh')
    for path in session.state_file().parent.glob(session.state_file().name + '.bad-*'):
        path.unlink()
    return [row('reset', ['J3'], [a, b, c])]


def extra_long(session, agent):
    ensure_active(session, agent)
    t = session.send('/d Write a numbered list of 400 short, distinct facts about gardening, one per line, each about '
                     '15 words. Do not use any tools and do not create files.', 'long answer')
    request = (session.state().get('turnRoute') or {}).get('requestId')
    words = agent_words(session, request)
    shown = session.shown(t)
    t['notes'].append('answer %d characters; posted %d' % (len(words), len(shown)))
    scenarios.expect(t, len(words) > 24000, 'answer too short to need parts (%d characters)' % len(words))
    scenarios.expect(t, t['parts'], 'a long answer did not come in parts')
    scenarios.expect(t, words[:300] in shown and words[-300:] in shown, 'the long answer was not posted in full')
    return [row('long', ['F11'], [t])]


def extra_attach(session, agent):
    ensure_active(session, agent)
    pixel = base64.b64encode(bytes.fromhex(
        '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
        '1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082')).decode()
    t = session.send('/d Reply with only the word attached.', 'attachment', content=[
        dict(type='text', text='/d Reply with only the word attached.'),
        dict(type='image', source=dict(type='base64', media_type='image/png', data=pixel))])
    shown = session.shown(t)
    scenarios.expect(t, 'Passing to' in shown, 'the /d with an image was not relayed')
    scenarios.expect(t, re.search(r'(?i)(did not|didn.t|not) (receive|get|see)', shown), 'no note that the agent did not receive the image')
    return [row('attach', ['F12'], [t])]


def extra_permission(binary, project, evidence, agent):
    """C7: without bypassPermissions, wider access asks first; headless, the ask is denied."""
    session = Session(binary, project, evidence / 'permission', bypass=False)
    session.evidence.mkdir(exist_ok=True)
    a = session.send('/cli bind ' + agent, 'permission')
    a['problems'] = [p for p in a['problems'] if not p.startswith('permission denials')]
    b = session.send('/cli access prompt', 'permission')
    b['problems'] = [p for p in b['problems'] if not p.startswith('permission denials')]
    c = session.send('/cli access allow', 'permission')
    # `/cli access allow` reaches the controller as `tune --phase access --apply`, an access menu as `choose N`.
    denied = [d for d in c['denials'] if re.search(r'\b(activate|tune --phase access|choose)\b', json.dumps(d))]
    c['problems'] = [p for p in c['problems'] if not p.startswith('permission denials')]
    scenarios.expect(c, denied, 'wider access did not ask (no denial headless): ' + json.dumps(c['denials'])[:200])
    c['notes'].append('bind denials: %d; prompt-access denials: %d' % (len(a['denials']), len(b['denials'])))
    session.send('/cli stop', 'permission')
    return [row('permission', ['C7'], [a, b, c])], session


def extra_viewer(session, agent):
    ensure_active(session, agent)
    a = session.send('/cli view on', 'viewer')
    scenarios.expect(a, a['modelTurns'] == 0 or 'view' in session.shown(a).casefold(), 'view on not confirmed')
    b = session.send('/d Reply with only the word view.', 'viewer')
    time.sleep(2)
    first = viewers(session)
    scenarios.expect(b, len(first) == 1, 'a relayed turn opened %d viewer windows' % len(first))
    c = session.send('/d Reply with only the word again.', 'viewer')
    second = viewers(session)
    scenarios.expect(c, len(second) == 1, 'a second turn left %d viewer windows' % len(second))
    d = session.send('/cli view off', 'viewer')
    time.sleep(4)
    left = viewers(session)
    scenarios.expect(d, not left, 'view off left %d viewer windows' % len(left))
    for pid in left:
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
    return [row('viewer', ['L1', 'L2', 'L3', 'L8', 'L9'], [a, b, c, d])]


def extra_format(session, agent, evidence):
    import formatting_prompt
    ensure_active(session, agent)
    t = session.send('/d ' + formatting_prompt.PROMPT, 'formatting')
    request = (session.state().get('turnRoute') or {}).get('requestId')
    raw = '\n\n'.join(t['texts']) or t['result']
    words = agent_words(session, request)
    shown = session.shown(t)
    scenarios.expect(t, words and words in shown, "agent's final words not posted verbatim")
    scenarios.expect(t, 'It costs $5 and $10.' in raw, 'the agent\'s "$5 and $10" was changed in the posted text')
    greens = re.findall(r'\$\\color\{228b22\}[^$]*\$', raw)
    scenarios.expect(t, greens and all(len(span) - 2 <= 60 for span in greens), 'green spans missing or too long')
    scenarios.expect(t, '**Passing to' in shown and 'says...**' in shown, 'green title lines do not read back as bold')
    table = next((line for line in words.splitlines() if line.startswith('|')), None)
    scenarios.expect(t, not table or table in raw, 'the table was not posted verbatim')
    capture = formatting_prompt.capture(events_path(session, request), evidence / 'format-capture', agent)
    t['notes'].append('answer checks: ' + json.dumps(capture['answer']))
    t['notes'].append('LaTeX-risk pairs in agent text (check in Phase 5): ' + json.dumps(capture['latexRisks']))
    (evidence / 'format-posted.md').write_text(raw, encoding='utf-8')
    return [row('format', ['P3', 'P2', 'F10'], [t])]


def extra_compact(session, agent):
    ensure_active(session, agent)
    a = session.send(scenarios.SHORT_TASK, 'compaction', kill_after=10)
    request = (session.state().get('turnRoute') or {}).get('requestId')
    b = session.send('/compact', 'compaction')
    b['notes'].append('compact result: ' + (b['result'] or '')[:160])
    scenarios.expect(b, session.state().get('active'), 'compaction turned the agent off')
    scenarios.expect(b, not re.search(r'controller\.py[^\n]* (off|cancel)\b', json.dumps(b['tools'])),
                     'compaction repeated a cancel or off')
    c = session.send('/cli resume', 'compaction')
    words = agent_words(session, request)
    scenarios.expect(c, words and words in session.shown(c), 'after compaction the interrupted output was not relayed')
    return [row('compact', ['J2'], [a, b, c])]


def run(agent, depth, extras, keep):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    evidence = Path(tempfile.gettempdir()) / 'cmv' / ('claude-%s-%s-%s' % (agent, depth, stamp))
    evidence.mkdir(parents=True)
    project = evidence / 'garden-app'
    project.mkdir()
    (project / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    (project / 'README.md').write_text('# Garden app\n\nTracks planting dates. `calc.py` holds helpers.\n', encoding='utf-8')
    versions = sorted(CACHE.iterdir())
    copies = {'build': fingerprint(BUILD), 'installed': fingerprint(INSTALLED), 'cache': fingerprint(versions[-1])}
    if len(set(copies.values())) != 1:
        raise SystemExit('The three copies differ; reinstall before validating: ' + json.dumps(copies))
    binary = desktop_claude()
    session = Session(binary, project, evidence)
    sessions = [session]
    results = scenarios.run_steps(session, agent, depth) if depth != 'none' else []
    for name in extras:
        try:
            if name == 'permission':
                rows, other = extra_permission(binary, project, evidence, agent)
                sessions.append(other)
            elif name == 'format':
                rows = extra_format(session, agent, evidence)
            else:
                rows = globals()['extra_' + name](session, agent)
        except Exception as exc:
            rows = [dict(step=name, features=[], status='fail', problems=['error: %s: %s' % (type(exc).__name__, exc)])]
        results += rows
    if session.state().get('active'):
        session.send('/cli stop', 'cleanup')
    turns = [turn for each in sessions for turn in each.turns]
    report = dict(host='claude-code', agent=agent, depth=depth, extras=list(extras), claudeCode=binary, copies=copies,
                  session=session.id, evidence=str(evidence), totalCostUsd=round(sum(s.spent for s in sessions), 4),
                  turns=len(turns))
    (evidence / 'summary.json').write_text(json.dumps(dict(report, results=results, turns_detail=[
        {key: turn.get(key) for key in ('step', 'scenario', 'prompt', 'route', 'seconds', 'modelTurns', 'costUsd',
                                        'killed', 'problems', 'notes')} for turn in turns]), indent=1), encoding='utf-8')
    print(json.dumps(report))
    for index, turn in enumerate(turns, 1):
        status = 'FAIL ' + '; '.join(turn['problems']) if turn['problems'] else 'ok'
        print('%02d %-16s %-44s %s %5ss %s' % (index, turn['scenario'], turn['prompt'][:44].replace('\n', ' '),
              'hook ' if turn['modelTurns'] == 0 else 'model', turn['seconds'], status))
        for note in turn['notes']:
            print('     note: ' + note[:170])
    for item in results:
        if item['status'] == 'skip':
            print('skip %-14s %s' % (item['step'], item.get('reason')))
    if not keep:
        for each in sessions:
            key = hashlib.sha256(each.id.encode()).hexdigest()
            for path in (DATA / 'sessions').glob(key + '*'):
                path.unlink(missing_ok=True)
        shutil.rmtree(project, ignore_errors=True)
        mangled = re.sub(r'[^A-Za-z0-9]', '-', str(project))
        shutil.rmtree(Path.home() / '.claude' / 'projects' / mangled, ignore_errors=True)
    return 1 if any(item['status'] == 'fail' for item in results) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', default='codex', choices=AGENTS)
    parser.add_argument('--depth', default='full', choices=('full', 'smoke', 'none'))
    parser.add_argument('--extras', nargs='*', default=[], choices=EXTRAS)
    parser.add_argument('--keep', action='store_true', help='Keep the test project and session state.')
    args = parser.parse_args()
    raise SystemExit(run(args.agent, args.depth, args.extras, args.keep))
