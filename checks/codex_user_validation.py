"""User validation of the INSTALLED CLI-MODE plugin, driven through real Codex sessions (plan Phase 3).

It mirrors claude_user_validation.py and runs the same shared scenario
(validation_scenarios.py). Codex is driven through `codex app-server` (JSON-RPC over
stdio), the server Codex Desktop runs on: `codex exec` does not run plugin hooks. Each
session is one app-server and one thread in a throwaway project folder, with the user's
real Codex configuration, approval policy `never` and the `:danger-full-access`
permission profile (Desktop's Full Access). Stopping a turn early is `turn/interrupt`,
as when the user presses Esc. After each turn it asserts:
- CLI-MODE's hooks ran and succeeded (hook/completed), and the hook that answered was the
  installed copy (state's hookSeen.plugin);
- every command the model ran is the installed plugin's controller.py;
- no errors, and no approval requests;
- a menu or result came back as a view `reference` line, and a relayed turn posted its
  mid-turn Markdown before the final view (F9). Views are read back as text for the checks,
  and each view file is kept as evidence.

--extras adds the Codex-only checks: gates (the Full Access gate: a thread without Full
Access must be refused, B6), format (the formatting prompt through the host, R1 and R2 as
posted), viewer (L8 under the Codex host). Codex's rate limits are read before and after.

It spends real quota: Codex turns on your plan, and the agent's own account.
Evidence goes to %TEMP%\\cmv\\codex-<agent>-<depth>-<time>.

    python checks/codex_user_validation.py [--agent claude] [--depth full|smoke] [--extras ...] [--keep]
"""
import argparse
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import validation_scenarios as scenarios

PROJECT = Path(__file__).resolve().parents[1]
CODEX_HOME = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
DATA = CODEX_HOME / 'plugin-data' / 'cli-mode'
VERSION = json.loads((PROJECT / 'plugins/cli-mode/.codex-plugin/plugin.json').read_text(encoding='utf-8'))[
    'version'].split('+')[0]
INSTALLED = CODEX_HOME / 'plugins' / 'cache' / 'cli-mode' / 'cli-mode' / VERSION
AGENTS = ('claude', 'copilot', 'agy', 'grok-build', 'codex', 'cursor')
EXTRAS = ('gates', 'format', 'viewer')
# CLI-MODE writes U+E200 visualize U+E202 {...} U+E201; a model has been seen to write U+E000/E002/E001.
REFERENCE = re.compile('[]visualize[](\\{.*?\\})[]')


def norm(path):
    """A comparable path; Codex quotes commands with doubled backslashes (C:\\\\Users\\\\...)."""
    return re.sub(r'/+', '/', str(path).replace('\\', '/')).rstrip('/').casefold()


def view_text(path):
    """A view file read back as the text a user sees: menu rows as lines, blocks separated."""
    try:
        html = Path(path).read_text(encoding='utf-8')
    except OSError:
        return ''
    html = re.sub(r'<style>.*?</style>', '', html, flags=re.S)
    html = re.sub(r'</(div|p|li|h[1-6]|tr|summary|pre|blockquote|dt|dd|header)>', '\n', html)
    html = re.sub(r'<(br|hr)\s*/?>', '\n', html)
    html = re.sub(r'</t[dh]>', ' | ', html)
    text = unescape(re.sub(r'<[^>]+>', '', html))
    return '\n'.join(line.rstrip() for line in text.splitlines() if line.strip())


class AppServer:
    """JSON-RPC over the stdio of one `codex app-server`."""

    def __init__(self, cwd, log):
        self.process = subprocess.Popen([shutil.which('codex'), 'app-server'], cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                        errors='replace', bufsize=1,
                                        env={k: v for k, v in os.environ.items() if not k.startswith('CLI_MODE_')})
        self.next_id, self.pending, self.notes, self.log = 0, {}, queue.Queue(), log
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [log.write('STDERR ' + line) for line in self.process.stderr],
                         daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.log.write(line)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'id' in message and 'method' not in message and message['id'] in self.pending:
                self.pending[message['id']].put(message)
            else:
                self.notes.put(message)

    def send(self, message):
        with self.lock:
            self.process.stdin.write(json.dumps(message) + '\n')
            self.process.stdin.flush()

    def request(self, method, params=None, timeout=120):
        self.next_id += 1
        box = self.pending[self.next_id] = queue.Queue()
        self.send(dict(jsonrpc='2.0', id=self.next_id, method=method, params=params or {}))
        reply = box.get(timeout=timeout)
        if 'error' in reply:
            raise RuntimeError(method + ': ' + json.dumps(reply['error'])[:600])
        return reply['result']

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True)


def rate_limits():
    """Codex's plan windows, read through a short-lived app-server."""
    log = open(os.devnull, 'w', encoding='utf-8')
    server = AppServer(Path.home(), log)
    try:
        server.request('initialize', dict(clientInfo=dict(name='cli-mode-validation', version=VERSION)))
        server.send(dict(jsonrpc='2.0', method='initialized', params={}))
        return server.request('account/rateLimits/read', {})
    except Exception as exc:
        return dict(error=str(exc)[:300])
    finally:
        server.close()


class Session:
    host = 'codex'

    def __init__(self, project, evidence, full_access=True, name='main'):
        self.project, self.evidence, self.name = project, evidence, name
        self.turns = []
        self.tokens = {}
        self.server = AppServer(project, open(evidence / ('rpc-%s.log' % name), 'w', encoding='utf-8'))
        self.server.request('initialize', dict(clientInfo=dict(name='cli-mode-validation', version=VERSION),
                                               capabilities=dict(experimentalApi=True)))
        self.server.send(dict(jsonrpc='2.0', method='initialized', params={}))
        # A permission profile, not the legacy `sandbox` field: only a thread with an active profile tells its
        # commands (CODEX_PERMISSION_PROFILE), which is how CLI-MODE sees Full Access, as in Desktop.
        settings = dict(approvalPolicy='never' if full_access else 'on-request',
                        config={'default_permissions': ':danger-full-access' if full_access else ':workspace'})
        started = self.server.request('thread/start', dict(cwd=str(project), **settings))
        self.thread = started['thread']['id']
        self.settings = {key: started.get(key) for key in ('model', 'approvalPolicy', 'sandbox', 'activePermissionProfile')}

    def close(self):
        self.server.close()

    def state(self):
        path = DATA / 'sessions' / (hashlib.sha256(self.thread.encode()).hexdigest() + '.json')
        try:
            return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        except ValueError:
            return {}

    def send(self, prompt, scenario, kill_after=None):
        began = time.monotonic()
        started = self.server.request('turn/start', dict(threadId=self.thread, input=[dict(type='text', text=prompt)]))
        turn_id = (started.get('turn') or {}).get('id')
        texts, commands, errors, hooks, requests, others = [], [], [], [], [], []
        interrupted, completed = False, None
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            try:
                note = self.server.notes.get(timeout=1)
            except queue.Empty:
                note = None
            if kill_after and not interrupted and time.monotonic() - began > kill_after:
                # The user presses Esc: the turn stops, the agent keeps working.
                self.server.request('turn/interrupt', dict(threadId=self.thread, turnId=turn_id))
                interrupted = True
            if note is None:
                continue
            method, params = note.get('method', ''), note.get('params') or {}
            if 'id' in note and method:  # An approval or input request: there should be none.
                requests.append(method)
                self.server.send(dict(jsonrpc='2.0', id=note['id'], result={'decision': 'decline'}))
                continue
            if method == 'hook/completed':
                run = params.get('run') or {}
                hooks.append(dict(event=run.get('eventName'), status=run.get('status'),
                                  entries=[entry.get('text') for entry in run.get('entries') or []]))
            elif method == 'item/completed':
                item = params.get('item') or {}
                kind = item.get('type')
                if kind == 'agentMessage':
                    texts.append(item.get('text') or '')
                elif kind == 'commandExecution':
                    commands.append(dict(command=item.get('command') or '', exit=item.get('exitCode'),
                                         output=(item.get('aggregatedOutput') or '')[-2000:]))
                elif kind not in ('reasoning', 'userMessage', 'contextCompaction', 'plan'):
                    others.append(kind)
            elif method == 'error':
                errors.append(json.dumps(params)[:300])
            elif method == 'thread/tokenUsage/updated':
                self.tokens = params.get('tokenUsage') or params
            elif method == 'turn/completed':
                completed = params.get('turn') or {}
                break
        status = (completed or {}).get('status')
        if (completed or {}).get('error'):
            errors.append(json.dumps(completed['error'])[:300])
        turn = dict(scenario=scenario, prompt=prompt, seconds=round(time.monotonic() - began, 1), killed=interrupted,
                    modelTurns=1, texts=texts, commands=commands, errors=errors, hooks=hooks, status=status,
                    result=texts[-1] if texts else '', problems=[], notes=[], controllerCalls=len(commands))
        state = self.state()
        turn['route'] = (state.get('turnRoute') or {}).get('route')
        failed = [hook for hook in hooks if hook['status'] != 'completed']
        if failed:
            turn['problems'].append('CLI-MODE hook failed: ' + json.dumps(failed)[:300])
        if not any(hook['event'] == 'userPromptSubmit' for hook in hooks):
            turn['problems'].append('the prompt hook did not run')
        seen = (state.get('hookSeen') or {}).get('plugin')
        if seen and norm(seen) != norm(INSTALLED):
            turn['problems'].append('hook ran from ' + seen)
        for command in commands:
            paths = re.findall(r"([A-Za-z]:[/\\][^'\" ]*controller\.py)", command['command'])
            if not paths:
                turn['problems'].append('ran a non-CLI-MODE command: ' + command['command'][:160])
            for path in paths:
                if not norm(path).startswith(norm(INSTALLED)):
                    turn['problems'].append('controller outside the installed copy: ' + path)
        for kind in others:
            turn['problems'].append('used ' + str(kind))
        if requests:
            turn['problems'].append('approval requests: ' + ', '.join(requests))
        if errors:
            turn['problems'].append('errors: ' + ' | '.join(errors)[:300])
        if completed is None:
            turn['problems'].append('the turn did not complete')
        views = []
        for text in texts:
            for match in REFERENCE.finditer(text):
                if match.group(0)[0] != '':
                    turn['notes'].append('view reference written with U+%04X delimiters' % ord(match.group(0)[0]))
                try:
                    path = json.loads(match.group(1))['path']
                except (ValueError, KeyError):
                    turn['problems'].append('unreadable view reference')
                    continue
                views.append(path)
                if Path(path).is_file():
                    shutil.copyfile(path, self.evidence / ('%s-%02d-%s' % (self.name, len(self.turns) + 1, Path(path).name)))
                else:
                    turn['problems'].append('view file missing: ' + path)
        turn['views'] = views
        relayed = {request for command in commands
                   for request in re.findall(r'--request ([0-9a-f]{32})', command['command'])}
        if relayed and not interrupted:
            final = texts[-1] if texts else ''
            if not REFERENCE.search(final):
                turn['problems'].append('a relayed turn did not end with a view reference')
            # A new request is announced once; /cli resume continues ones already announced.
            if turn['route'] == 'direct' and not any('Passing to' in text for text in texts):
                turn['problems'].append('no mid-turn "Passing to" update before the view')
        (self.evidence / ('%s-%02d-turn.json' % (self.name, len(self.turns) + 1))).write_text(
            json.dumps(turn, indent=1, ensure_ascii=False), encoding='utf-8')
        self.turns.append(turn)
        return turn

    def shown(self, turn):
        """The model's messages, with each view reference replaced by the view's text."""
        return REFERENCE.sub(lambda match: '\n' + view_text(json.loads(match.group(1)).get('path', '')) + '\n',
                             '\n\n'.join(turn['texts']))

    def words(self, request):
        sys.path.insert(0, str(INSTALLED / 'scripts'))
        import relay_view
        record = (self.state().get('requests') or {}).get(request) or {}
        path = Path(record.get('events') or '')
        if not path.is_file():
            return ''
        events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        return relay_view.messages(events, last_only=True).strip()


def row(step, features, turns, extra=()):
    problems = [p for turn in turns for p in turn['problems']] + list(extra)
    return dict(step=step, features=features, status='fail' if problems else 'pass', problems=problems,
                notes=[n for turn in turns for n in turn['notes']])


def fresh_project(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    (folder / 'README.md').write_text('# Garden app\n\nTracks planting dates. `calc.py` holds helpers.\n', encoding='utf-8')
    return folder


def extra_gates(evidence, agent):
    """B6: a thread without Full Access must be refused with the Full Access instruction."""
    session = Session(fresh_project(evidence / 'gate-sandbox'), evidence, full_access=False, name='gate')
    try:
        a = session.send('/cli bind ' + agent, 'gates')
        shown = session.shown(a)
        a['problems'] = [p for p in a['problems'] if not p.startswith(('approval requests', 'errors'))]
        scenarios.expect(a, re.search(r'(?i)full access', shown), 'no Full Access readiness message without Full Access')
        scenarios.expect(a, not session.state().get('active'), 'an agent activated without Full Access')
        a['notes'].append('reply without Full Access: ' + shown[:200].replace('\n', ' / '))
        return [row('gate-full-access', ['B6'], [a])], session
    except Exception:
        session.close()
        raise


def extra_format(session, agent, evidence):
    import formatting_prompt
    if not session.state().get('active'):
        session.send('/cli bind ' + agent, 'formatting')
    t = session.send('/d ' + formatting_prompt.PROMPT, 'formatting')
    request = (session.state().get('turnRoute') or {}).get('requestId')
    words = session.words(request)
    mid = '\n\n'.join(t['texts'][:-1])
    scenarios.expect(t, t['views'], 'no final view')
    view = view_text(t['views'][-1]) if t['views'] else ''
    html = Path(t['views'][-1]).read_text(encoding='utf-8') if t['views'] else ''
    checks = formatting_prompt.answer_checks(words)
    scenarios.expect(t, '<table>' in html or not checks['table'], 'the table is not a table in the view')
    scenarios.expect(t, '<pre><code>' in html or not checks['codeBlock'], 'the code block is not a code block in the view')
    scenarios.expect(t, 'It costs $5 and $10.' in view or not checks['dollars'], '"$5 and $10" changed in the view')
    scenarios.expect(t, 'href=' not in html, 'a link became clickable in the view')
    scenarios.expect(t, 'Passing to' in mid, 'no mid-turn Markdown before the view')
    t['notes'].append('mid-turn Markdown characters posted: %d' % len(mid))
    capture = formatting_prompt.capture(((session.state().get('requests') or {}).get(request) or {}).get('events'),
                                        evidence / 'format-capture', agent)
    t['notes'].append('answer checks: ' + json.dumps(capture['answer']))
    (evidence / 'format-posted.md').write_text('\n\n---\n\n'.join(t['texts']), encoding='utf-8')
    return [row('format', ['P2', 'F9', 'P1'], [t])]


def extra_viewer(session, agent):
    from claude_user_validation import viewers
    if not session.state().get('active'):
        session.send('/cli bind ' + agent, 'viewer')
    probe = type('S', (), {'id': session.thread})()
    a = session.send('/cli view on', 'viewer')
    b = session.send('/d Reply with only the word view.', 'viewer')
    time.sleep(2)
    opened = viewers(probe)
    scenarios.expect(b, len(opened) == 1, 'a relayed turn opened %d viewer windows under Codex' % len(opened))
    c = session.send('/cli view off', 'viewer')
    time.sleep(4)
    left = viewers(probe)
    scenarios.expect(c, not left, 'view off left %d viewer windows' % len(left))
    for pid in left:
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
    return [row('viewer', ['L8', 'L1', 'L2', 'L9'], [a, b, c])]


def run(agent, depth, extras, keep):
    if not (INSTALLED / 'scripts' / 'controller.py').is_file():
        raise SystemExit('CLI-MODE %s is not installed in %s' % (VERSION, CODEX_HOME))
    stamp = time.strftime('%Y%m%d-%H%M%S')
    evidence = Path(tempfile.gettempdir()) / 'cmv' / ('codex-%s-%s-%s' % (agent, depth, stamp))
    evidence.mkdir(parents=True)
    project = fresh_project(evidence / 'garden-app')
    before = rate_limits()
    session = Session(project, evidence)
    sessions = [session]
    try:
        results = scenarios.run_steps(session, agent, depth) if depth != 'none' else []
        for name in extras:
            try:
                if name == 'gates':
                    rows, other = extra_gates(evidence, agent)
                    sessions.append(other)
                elif name == 'format':
                    rows = extra_format(session, agent, evidence)
                else:
                    rows = extra_viewer(session, agent)
            except Exception as exc:
                rows = [dict(step=name, features=[], status='fail', problems=['error: %s: %s' % (type(exc).__name__, exc)])]
            results += rows
        for each in sessions:
            if each.state().get('active'):
                each.send('/cli stop', 'cleanup')
    finally:
        for each in sessions:
            each.close()
    after = rate_limits()
    turns = [turn for each in sessions for turn in each.turns]
    report = dict(host='codex', agent=agent, depth=depth, extras=list(extras), installed=str(INSTALLED),
                  threads=[each.thread for each in sessions], settings=session.settings, evidence=str(evidence),
                  rateLimitsBefore=before, rateLimitsAfter=after, turns=len(turns))
    (evidence / 'summary.json').write_text(json.dumps(dict(report, results=results, turns_detail=[
        {key: turn.get(key) for key in ('step', 'scenario', 'prompt', 'route', 'seconds', 'killed', 'hooks', 'problems',
                                        'notes', 'views')} for turn in turns]), indent=1, ensure_ascii=False),
        encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if not key.startswith('rateLimits')}))
    for index, turn in enumerate(turns, 1):
        status = 'FAIL ' + '; '.join(turn['problems']) if turn['problems'] else 'ok'
        print('%02d %-16s %-44s %5ss %s' % (index, turn['scenario'], turn['prompt'][:44].replace('\n', ' '),
                                            turn['seconds'], status))
        for note in turn['notes']:
            print('     note: ' + note[:170])
    for item in results:
        if item['status'] == 'skip':
            print('skip %-14s %s' % (item['step'], item.get('reason')))
    if not keep:
        for each in sessions:
            key = hashlib.sha256(each.thread.encode()).hexdigest()
            for path in (DATA / 'sessions').glob(key + '*'):
                path.unlink(missing_ok=True)
        shutil.rmtree(project, ignore_errors=True)
    return 1 if any(item['status'] == 'fail' for item in results) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', default='claude', choices=AGENTS)
    parser.add_argument('--depth', default='full', choices=('full', 'smoke', 'none'))
    parser.add_argument('--extras', nargs='*', default=[], choices=EXTRAS)
    parser.add_argument('--keep', action='store_true', help='Keep the test project and session state.')
    args = parser.parse_args()
    raise SystemExit(run(args.agent, args.depth, args.extras, args.keep))
