"""Live check: a Claude Code agent turn that runs as a background task, end to end.

This spends quota: Claude Code's model for the host turns, and the agent's own
usage. It uses your Claude Code sign-in, but loads the unpacked build
(dist/claude-dev/cli-mode) for this session only; it does not install or
enable anything in your Claude Code settings.

`claude -p` ends background commands about 5 s after a result once stdin
closes, so this keeps ONE headless session open (stream-json input) for all
of its turns, as the desktop app does:

1. /cli bind <agent>: activation runs in the hook (no command, no row); its card comes back.
2. /d <task>: the turn posts "Passing to ...", starts `controller.py follow` and
   ends. The hook must have made the follow a background task, labelled
   "<Agent> · <prompt>". Nothing of the agent's answer is in this turn.
3. (no prompt) the follow ends when the agent finishes; its notification wakes
   Claude, which runs the relay and posts the agent's final message verbatim.
4. /cli stop.

With --agents, two named agents at once instead: spawn the first and give it a task,
spawn the second while the first works and give it one too (each by name), wait for
both answers, close the first by name (the second keeps running and becomes current),
then /cli close with one agent left, which turns CLI-MODE off.

    python scripts/package_plugin.py
    python checks/claude_background_live.py --agent grok-build [--model <id>] [--keep]
    python checks/claude_background_live.py --agents grok-build,codex [--model <id>] [--keep]
    python checks/claude_background_live.py --agents grok-build,codex --tools [--keep]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from claude_live_relay import DEV, MARKER, claude_binary, claude_data

PROMPT = 'Read README.md in this folder. Reply in two short paragraphs: what the project tracks, and its codename.'
SECOND_MARKER = 'CLI_MODE_SECOND_' + MARKER[-8:]
SECOND_PROMPT = 'Read NOTES.md in this folder. Reply in one short paragraph: what it lists, and its tag word.'
LABELS = {'grok-build': 'Grok', 'agy': 'Antigravity', 'claude': 'Claude Code', 'codex': 'Codex',
          'copilot': 'Copilot', 'cursor': 'Cursor'}


class Session:
    """One headless Claude Code session kept open across turns, including wake-ups."""
    def __init__(self, workspace, model):
        args = [claude_binary(), '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--verbose', '--plugin-dir', str(DEV)] + (['--model', model] if model else [])
        env = {key: value for key, value in os.environ.items() if not key.startswith('CLI_MODE_')}
        env['MSYS_NO_PATHCONV'] = '1'
        self.process = subprocess.Popen(args, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        self.events, self.lock = [], threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            if line.startswith('{'):
                with self.lock:
                    self.events.append(dict(json.loads(line), receivedAt=time.monotonic()))

    def results(self):
        with self.lock:
            return [index for index, event in enumerate(self.events) if event.get('type') == 'result']

    def send(self, prompt):
        message = {'type': 'user', 'message': {'role': 'user', 'content': prompt}}
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def wait(self, count, timeout, done=None):
        """Wait until the session has posted `count` results in all (None: ignore), or `done()` is true.

        False on timeout or if Claude Code exits.
        """
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if (count is not None and len(self.results()) >= count) or (done and done()):
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(.25)
        return False

    def mark(self):
        with self.lock:
            return len(self.events)

    def relay_turn_ended(self):
        """True once text is posted after the last relay command and a turn result follows that text.

        Other events, such as a late notification, may arrive after the result (live run 4), and a result can
        arrive between the relay call and the posted answer (live run 5), so neither 'last event is a result'
        nor 'any result after the relay' is enough.
        """
        with self.lock:
            events = list(self.events)
        relays = [index for index, event in enumerate(events) if event.get('type') == 'assistant'
                  and any(' relay --request ' in ((block.get('input') or {}).get('command') or '')
                          for block in (event.get('message') or {}).get('content') or [])]
        if not relays:
            return False
        texts = [index for index, event in enumerate(events) if index > relays[-1] and event.get('type') == 'assistant'
                 and any(block.get('type') == 'text' and block.get('text', '').strip()
                         for block in (event.get('message') or {}).get('content') or [])]
        return bool(texts) and any(event.get('type') == 'result' for event in events[texts[-1]:])

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()


def turns(events, marks):
    """The events of bind, the /d turn, its wake-up and stop, split by what started each.

    `marks` are the event counts when /d and /cli stop were sent. The /d turn ends at its first result;
    everything after it, until /cli stop, is the wake-up (empty when no separate turn came).
    """
    task_start, stop_start = marks
    task = events[task_start:stop_start]
    end = next((index + 1 for index, event in enumerate(task) if event.get('type') == 'result'), len(task))
    return dict(bind=events[:task_start], task=task[:end], wake=task[end:], stop=events[stop_start:])


def relayed(session, workspace, every=False):
    """True once CLI-MODE has shown the latest request's whole answer (with `every`, each request's), whichever
    turn relayed it."""
    sys.path.insert(0, str(DEV / 'scripts'))
    from state import Store
    try:
        state = Store(session, workspace, claude_data()).read()
        requests = state.get('requests') or {}
        wanted = list(requests) if every else [max(requests, key=lambda key: requests[key].get('capturedAt') or 0)]
        progress = state.get('relayProgress') or {}
        return bool(wanted) and all((progress.get(key) or {}).get('done') or requests[key].get('relayed')
                                    for key in wanted)
    except (OSError, ValueError, KeyError):
        return False


def saved_state(session, workspace):
    sys.path.insert(0, str(DEV / 'scripts'))
    from state import Store
    return Store(session, workspace, claude_data()).read()


def newest_name(session, workspace):
    """The name of the agent started last."""
    owned = saved_state(session, workspace)['owned']
    return max(owned, key=lambda item: item.get('lastUsedAt') or 0)['alias']


def brief_edit(tool):
    """The host writing its note: a file edit of the project brief (and the read Claude Code requires first)."""
    return tool['name'] in ('Read', 'Edit', 'MultiEdit', 'Write') and (tool.get('path') or '').replace(
        '\\', '/').endswith('Agent_Working_Folder/BRIEF.md')


def host_note(workspace):
    """The host's newest note in the project brief (its lines under the last dated heading), or None."""
    sys.path.insert(0, str(DEV / 'scripts'))
    import agent_folder
    notes = agent_folder.read_brief(workspace)[1]
    last = max((index for index, line in enumerate(notes) if line.startswith('### ')), default=None)
    return None if last is None else '\n'.join(line for line in notes[last + 1:] if line.strip())


def summary(events):
    blocks = [block for event in events if event.get('type') == 'assistant'
              for block in (event.get('message') or {}).get('content') or []]
    # The turn's result, which newer Claude Code versions follow with a `task_summary` event.
    result = next((event for event in reversed(events) if event.get('type') == 'result'), {})
    return dict(
        tools=[dict(name=block.get('name'), command=(block.get('input') or {}).get('command'),
                    path=(block.get('input') or {}).get('file_path'))
               for block in blocks if block.get('type') == 'tool_use'],
        tasks=[dict(subtype=event.get('subtype'), description=event.get('description'), status=event.get('status'),
                    backgrounded=event.get('is_backgrounded'))
               for event in events if event.get('type') == 'system'
               and event.get('subtype') in ('task_started', 'task_notification')],
        seconds=round((result.get('duration_ms') or 0) / 1000, 1), turns=result.get('num_turns'),
        costUsd=result.get('total_cost_usd') or 0, denials=result.get('permission_denials') or [],
        result=result.get('result') or '', error=bool(result.get('is_error')))


def agent_message(session, workspace, request):
    """The agent's last message, rebuilt from CLI-MODE's public event log."""
    sys.path.insert(0, str(DEV / 'scripts'))
    from state import Store
    import relay_view
    state = Store(session, workspace, claude_data()).read()
    path = Path(state['requests'][request]['events'])
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return relay_view.messages(events, last_only=True).strip()


def run(agent, model, keep):
    workspace = Path(tempfile.mkdtemp(prefix='cli-mode-background-' + agent + '-')).resolve()
    (workspace / 'README.md').write_text('# Notes\n\nThe project codename is ' + MARKER + '. It tracks garden '
                                         'planting dates and watering reminders.\n', encoding='utf-8')
    report, problems = dict(agent=agent, workspace=str(workspace)), []
    host = Session(workspace, model)
    session, marks, ended, woken, task_mark = None, None, None, None, None
    try:
        host.send('/cli bind ' + agent)
        if not host.wait(1, 300):
            raise RuntimeError('bind: no result')
        with host.lock:
            session = next(event.get('session_id') for event in host.events if event.get('session_id'))
        report['name'] = newest_name(session, workspace)
        started, task_mark = time.monotonic(), host.mark()
        host.send('/d ' + PROMPT)
        if not host.wait(2, 300):
            raise RuntimeError('/d: no result')
        ended = time.monotonic()
        # Done when CLI-MODE has shown the answer, however it got there (not after a fixed number of turns),
        # and the turn that showed it has ended.
        if not host.wait(None, 900, done=lambda: relayed(session, workspace)):
            raise RuntimeError('the answer was never relayed')
        woken = time.monotonic()
        if not host.wait(None, 120, done=host.relay_turn_ended):  # The turn that posted it has ended.
            raise RuntimeError('the turn that relayed the answer never ended')
        marks = (task_mark, host.mark())
        host.send('/cli stop')
        host.wait(len(host.results()) + 1, 120)
    except RuntimeError as exc:
        problems.append(str(exc))
        if task_mark is not None and marks is None:
            marks = (task_mark, host.mark())  # Keep the turns apart in the report.
    finally:
        host.close()
    with host.lock:
        events = list(host.events)
    # The raw stream, for diagnosing a failed run (kept with --keep).
    (workspace / 'host-events.jsonl').write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
    split = turns(events, marks or (len(events), len(events)))
    steps = {name: summary(part) for name, part in split.items() if part or name != 'wake'}
    report['turns'] = steps
    for name, step in steps.items():
        if step['error']:
            problems.append(name + ': ended with an error: ' + step['result'][:300])
        if step['denials']:
            problems.append(name + ': permission denials ' + json.dumps(step['denials']))
        for tool in step['tools']:
            if name == 'bind' and brief_edit(tool):
                continue  # The host's note in the project brief, written as the new agent's card is shown.
            if tool['name'] not in ('Bash', 'PowerShell') or 'controller.py' not in (tool['command'] or ''):
                problems.append(name + ': used ' + str(tool['name']) + ' ' + str(tool['command'])[:120])
    sys.path.insert(0, str(DEV / 'scripts'))
    from presentation import plain_strong
    label = LABELS.get(agent, agent) + (' ' + report['name'] if report.get('name') else '')
    task, wake = steps.get('task'), steps.get('wake')
    request = None
    bind = steps.get('bind')
    if bind:
        # Activation runs in the prompt hook: no command for Claude, so no background-tasks row of its own. Its one
        # tool is the host's note in the project brief.
        commands = [tool for tool in bind['tools'] if not brief_edit(tool)]
        if commands or any(item['subtype'] == 'task_started' for item in bind['tasks']):
            problems.append('bind: ran as a command (' + json.dumps(commands)[:200] + ')')
        if 'CLI-MODE Activated' not in plain_strong(bind['result']):
            problems.append('bind: no activation card')
        report['hostNote'] = host_note(workspace)
        if not report['hostNote'] or 'has not written this note yet' in report['hostNote']:
            problems.append('bind: the host did not write its note in the project brief')
    if task:
        follows = [tool['command'] for tool in task['tools'] if ' follow --request ' in (tool['command'] or '')]
        relays = [tool for tool in task['tools'] if ' relay --request ' in (tool['command'] or '')]
        if len(follows) != 1:
            problems.append('task: expected one follow command, got ' + str(len(follows)))
        else:
            request = follows[0].split(' follow --request ')[1].split()[0]
        if relays:
            problems.append('task: ran the relay in the /d turn instead of waiting for the wake-up')
        started_tasks = [item for item in task['tasks'] if item['subtype'] == 'task_started']
        expected = label + ' · ' + PROMPT[:30].rstrip() + '…'
        if not any(item['backgrounded'] and item['description'] == expected for item in started_tasks):
            problems.append('task: the follow was not a background task labelled ' + expected + ': ' +
                            json.dumps(started_tasks))
        if 'Passing to ' + label not in plain_strong(task['result']) and not any(
                'Passing to ' + label in plain_strong(block.get('text', ''))
                for event in split['task'] if event.get('type') == 'assistant'
                for block in (event.get('message') or {}).get('content') or []):
            problems.append('task: no "Passing to ' + label + '" line')
        if MARKER in task['result']:
            problems.append('task: the answer came in the /d turn, not after the wake-up')
        report['taskTurnSeconds'] = round(ended - started, 1)
    if task and marks and not wake:
        problems.append('wake: no separate wake-up turn; the end of the follow was handled inside the /d turn')
    if wake:
        notices = [item for item in (steps['task']['tasks'] + wake['tasks']) if item['subtype'] == 'task_notification']
        if not notices:
            problems.append('wake: no task notification before the wake-up')
        relays = [tool for tool in wake['tools'] if ' relay --request ' in (tool['command'] or '')]
        report['wakeRelayCalls'] = len(relays)
        if not relays:
            problems.append('wake: the relay did not run')
        if MARKER not in wake['result']:
            problems.append('wake: the codename did not come back')
        if request:
            try:
                words = agent_message(session, workspace, request)
                report['finalAgentMessage'] = words
                if words and words not in wake['result']:
                    problems.append('wake: the agent\'s final message was not posted verbatim')
            except (OSError, KeyError, ValueError, StopIteration) as exc:
                problems.append('wake: could not read the event log: ' + str(exc))
        report['agentAndWakeSeconds'] = round(woken - ended, 1)
    if 'stop' in steps and 'CLI-MODE is off' not in plain_strong(steps['stop']['result']):
        problems.append('stop: no "CLI-MODE is off"')
    report['costUsd'] = round(max([step['costUsd'] for step in steps.values()] or [0]), 4)
    report['problems'] = problems
    report['passed'] = not problems
    # Close only this test's ownership, even if a turn failed before /cli stop.
    if session:
        import host as host_module
        from controller import Controller
        from state import Store
        host_module.select(host_module.CLAUDE)
        store = Store(session, workspace, claude_data())
        if store.path.exists():
            report['cleanup'] = Controller(store).off()['shutdownComplete']
    (workspace / 'host-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not keep:
        shutil.rmtree(workspace, ignore_errors=True)
        if session:
            key = hashlib.sha256(session.encode()).hexdigest()
            for path in (claude_data() / 'sessions').glob(key + '*'):
                path.unlink(missing_ok=True)
    return report


def run_pair(agents, model, keep):
    """Two named agents side by side: the second starts while the first works; each answer is relayed under its
    own name; closing the first leaves the second running; closing the last turns CLI-MODE off."""
    workspace = Path(tempfile.mkdtemp(prefix='cli-mode-agents-')).resolve()
    (workspace / 'README.md').write_text('# Notes\n\nThe project codename is ' + MARKER + '. It tracks garden '
                                         'planting dates and watering reminders.\n', encoding='utf-8')
    (workspace / 'NOTES.md').write_text('# Shopping\n\nTag word: ' + SECOND_MARKER + '. Lists seeds, compost and '
                                        'a new watering can.\n', encoding='utf-8')
    report, problems = dict(agents=list(agents), workspace=str(workspace)), []
    host = Session(workspace, model)
    session, first, second, close_mark = None, None, None, None
    try:
        host.send('/cli spawn ' + agents[0])
        if not host.wait(1, 300):
            raise RuntimeError('first spawn: no result')
        with host.lock:
            session = next(event.get('session_id') for event in host.events if event.get('session_id'))
        first = newest_name(session, workspace)
        host.send('/d ' + first.lower() + ' ' + PROMPT)
        if not host.wait(2, 300):
            raise RuntimeError('first /d: no result')
        host.send('/cli spawn ' + agents[1])  # While the first agent works.
        if not host.wait(None, 300, done=lambda: len(saved_state(session, workspace)['owned']) == 2
                         and all(item.get('ready') for item in saved_state(session, workspace)['owned'])):
            raise RuntimeError('second spawn: the agent never became ready')
        second = newest_name(session, workspace)
        report['names'] = [first, second]
        host.send('/d ' + second + ' ' + SECOND_PROMPT)
        if not host.wait(None, 900, done=lambda: len(saved_state(session, workspace).get('requests') or {}) == 2
                         and relayed(session, workspace, every=True)):
            raise RuntimeError('both answers were never relayed')
        if not host.wait(None, 120, done=host.relay_turn_ended):
            raise RuntimeError('the last relay turn never ended')
        time.sleep(3)  # A second relay turn may still be finishing.
        close_mark = host.mark()
        host.send('/cli close ' + first)
        host.wait(len(host.results()) + 1, 120)
        state = saved_state(session, workspace)
        report['afterClose'] = dict(owned=[item['alias'] for item in state['owned']], active=state['active'],
                                    current=next((item['alias'] for item in state['owned']
                                                  if item['name'] == state['main']), None))
        host.send('/cli close')
        host.wait(len(host.results()) + 1, 120)
    except RuntimeError as exc:
        problems.append(str(exc))
    finally:
        host.close()
    with host.lock:
        events = list(host.events)
    (workspace / 'host-events.jsonl').write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
    sys.path.insert(0, str(DEV / 'scripts'))
    from presentation import plain_strong
    texts = [plain_strong(block.get('text', '')) for event in events if event.get('type') == 'assistant'
             for block in (event.get('message') or {}).get('content') or [] if block.get('type') == 'text']
    texts += [plain_strong(event.get('result') or '') for event in events if event.get('type') == 'result']
    started = [event for event in events if event.get('type') == 'system' and event.get('subtype') == 'task_started']
    tools = [block for event in events if event.get('type') == 'assistant'
             for block in (event.get('message') or {}).get('content') or [] if block.get('type') == 'tool_use']
    for tool in tools:
        command = (tool.get('input') or {}).get('command') or ''
        if tool.get('name') not in ('Bash', 'PowerShell') or 'controller.py' not in command:
            problems.append('used ' + str(tool.get('name')) + ' ' + command[:120])
        elif not any(' ' + word + ' ' in command for word in ('follow', 'relay')):
            problems.append('ran a control as a command (a row of its own): ' + command[-80:])
    for kind, name, prompt, marker in ((agents[0], first, PROMPT, MARKER), (agents[1], second, SECOND_PROMPT,
                                                                           SECOND_MARKER)):
        if not name:
            continue
        label = LABELS.get(kind, kind) + ' ' + name
        expected = label + ' · ' + prompt[:30].rstrip() + '…'
        if not any(event.get('is_backgrounded') and event.get('description') == expected for event in started):
            problems.append(name + ': no background row labelled ' + expected + ': ' +
                            json.dumps([event.get('description') for event in started]))
        if not any('Passing to ' + label in text for text in texts):
            problems.append(name + ': no "Passing to ' + label + '" line')
        if not any(label + ' says...' in text and marker in text for text in texts):
            problems.append(name + ': its answer (' + marker + ') was not relayed under "' + label + ' says..."')
    after = report.get('afterClose') or {}
    if first and second and after.get('owned') != [second]:
        problems.append('close ' + first + ': expected only ' + str(second) + ' left, got ' + json.dumps(after))
    if second and after.get('current') != second:
        problems.append('close ' + str(first) + ': ' + str(second) + ' did not become current')
    closing = [plain_strong(event.get('result') or '') for event in events[close_mark or len(events):]
               if event.get('type') == 'result']
    if first and not any(first + ' is closed.' in text for text in closing):
        problems.append('close ' + first + ': no "' + first + ' is closed." reply')
    if not any('CLI-MODE is off' in text for text in closing):
        problems.append('/cli close with one agent left: no "CLI-MODE is off"')
    results = [event for event in events if event.get('type') == 'result']
    report['costUsd'] = round(max([event.get('total_cost_usd') or 0 for event in results] or [0]), 4)
    report['problems'] = problems
    report['passed'] = not problems
    if session:
        import host as host_module
        from controller import Controller
        from state import Store
        host_module.select(host_module.CLAUDE)
        store = Store(session, workspace, claude_data())
        if store.path.exists():
            report['cleanup'] = Controller(store).off()['shutdownComplete']
    (workspace / 'host-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not keep:
        shutil.rmtree(workspace, ignore_errors=True)
        if session:
            key = hashlib.sha256(session.encode()).hexdigest()
            for path in (claude_data() / 'sessions').glob(key + '*'):
                path.unlink(missing_ok=True)
    return report


TOOLS_ASK = 'Reply with only the codename in README.md.'
TOOLS_EDIT = 'Append one line with the single word checked to NOTES.md, then reply with only the word done.'
TOOLS_RECALL = 'What single word did you append to NOTES.md earlier? Reply with only that word.'
# No location given: the working-folder paragraph CLI-MODE adds must decide where the file goes.
NOTE_WORD = 'LANTERN_' + MARKER[-6:]  # Not the README codename: only the saved note carries it.
TOOLS_SAVE = ('Write a three-line research note about this project that includes the word ' + NOTE_WORD +
              ', and save it as a Markdown file. Reply with only the file\'s path.')
# The hand-off: the other agent gets only what the first answer's copy box holds.
TOOLS_HANDOFF = 'Which word in capitals with an underscore appears in the files below? Reply with only that word.\n\n'


def folded_answers(events, plain):
    """Agent answers posted before their turn's last message: the desktop app folds those out of view.

    Relays tell Claude to post each answer in the turn's last message, and to carry every relay's output there
    when several run in one turn (agents that finish together). An answer anywhere else counts as lost, even
    though a plain transcript shows it (live, 2026-09-25: a run passed only because Claude happened to post
    after each relay).
    """
    problems, turn = [], []
    for event in events:
        if event.get('type') == 'assistant':
            turn += [block['text'] for block in (event.get('message') or {}).get('content') or []
                     if block.get('type') == 'text' and block.get('text', '').strip()]
        elif event.get('type') == 'result':
            for text in turn[:-1]:
                heads = [line for line in plain(text).splitlines() if line.rstrip('*').endswith(' says...')]
                if heads:
                    problems.append('folded: ' + ', '.join(head.strip('*') for head in heads) + ' posted before '
                                    'the last message of its turn')
            turn = []
    return problems


def run_tools(agents, model, keep):
    """The agent tools, live, in a git repository: one prompt to two agents, a change receipt and /cli diff,
    a note saved (with no location given) in the agent's git-ignored Agent_Working_Folder/<NAME>/ and its
    saved-files line and its answer's copy box, handed by that box's text alone to the other agent, /cli timeout,
    then /cli attach of an open agent from an ended session, which remembers its
    earlier turn."""
    workspace = Path(tempfile.mkdtemp(prefix='cli-mode-tools-')).resolve()
    (workspace / 'README.md').write_text('# Notes\n\nThe project codename is ' + MARKER + '.\n', encoding='utf-8')
    (workspace / 'NOTES.md').write_text('# Notes\n\nfirst line\n', encoding='utf-8')
    for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=check', '-c', 'user.email=check@example.com',
                                                'commit', '-qm', 'start']):
        subprocess.run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
    report, problems = dict(agents=list(agents), workspace=str(workspace)), []
    sys.path.insert(0, str(DEV / 'scripts'))
    from presentation import plain_strong
    sessions, names = [], {}

    def ask(host, prompt, timeout=300):
        mark = host.mark()
        host.send(prompt)
        if not host.wait(len(host.results()) + 1, timeout):
            raise RuntimeError(prompt[:40] + ': no result')
        return mark

    def said(host, since=0):
        with host.lock:
            events = list(host.events[since:])
        texts = [block.get('text', '') for event in events if event.get('type') == 'assistant'
                 for block in (event.get('message') or {}).get('content') or [] if block.get('type') == 'text']
        return texts + [event.get('result') or '' for event in events if event.get('type') == 'result']

    def settle(host, session, count):
        if not host.wait(None, 900, done=lambda: len(saved_state(session, workspace).get('requests') or {}) >= count
                         and relayed(session, workspace, every=True)):
            raise RuntimeError('an answer was never relayed')
        host.wait(None, 120, done=host.relay_turn_ended)
        time.sleep(3)  # Another wake-up may still be finishing.

    first = Session(workspace, model)
    sessions.append(first)
    session = later = None
    try:
        ask(first, '/cli spawn ' + agents[0])
        with first.lock:
            session = next(event.get('session_id') for event in first.events if event.get('session_id'))
        names[agents[0]] = newest_name(session, workspace)
        ask(first, '/cli spawn ' + agents[1])
        names[agents[1]] = newest_name(session, workspace)
        one, two = names[agents[0]], names[agents[1]]
        report['names'] = [one, two]
        both = first.mark()
        first.send('/d ' + one.lower() + ',' + two.lower() + ' ' + TOOLS_ASK)
        settle(first, session, 2)
        texts = [plain_strong(text) for text in said(first, both)]
        labels = [LABELS[kind] + ' ' + names[kind] for kind in agents]
        if not any('Passing to ' + labels[0] + ' and ' + labels[1] in text for text in texts):
            problems.append('multi: no "Passing to ' + labels[0] + ' and ' + labels[1] + '" line')
        with first.lock:
            rows = [event.get('description') for event in first.events[both:]
                    if event.get('type') == 'system' and event.get('subtype') == 'task_started'
                    and event.get('is_backgrounded')]
        for label in labels:
            if label + ' · ' + TOOLS_ASK[:30].rstrip() + '…' not in rows:
                problems.append('multi: no background row for ' + label + ': ' + json.dumps(rows))
            if not any(label + ' says...' in text and MARKER in text for text in texts):
                problems.append('multi: ' + label + '\'s answer did not come back under its name')
        editor = names[agents[1]]
        edit = first.mark()
        first.send('/d ' + editor + ' ' + TOOLS_EDIT)
        settle(first, session, 3)
        raw = said(first, edit)
        state = saved_state(session, workspace)
        latest = max(state['requests'].values(), key=lambda record: record.get('capturedAt') or 0)
        receipt = latest.get('changes') or {}
        report['receipt'] = {key: receipt.get(key) for key in ('files', 'added', 'removed')}
        if receipt.get('files') != 1 or [item['path'] for item in receipt.get('paths') or []] != ['NOTES.md']:
            problems.append('receipt: expected NOTES.md alone, got ' + json.dumps(report['receipt']))
        if not any('changed 1 file' in plain_strong(text) and '\\color{cf222e}' in text and '\\color{228b22}' in text
                   for text in raw):
            problems.append('receipt: the relayed answer has no coloured "changed 1 file" line')
        saver = names[agents[0]]
        save = first.mark()
        first.send('/d ' + saver.lower() + ' ' + TOOLS_SAVE)
        settle(first, session, 4)
        state = saved_state(session, workspace)
        latest = max(state['requests'].values(), key=lambda record: record.get('capturedAt') or 0)
        stored = latest.get('saved') or {}
        folder = 'Agent_Working_Folder/' + saver
        report['saved'] = {key: stored.get(key) for key in ('folder', 'saved', 'paths')}
        if stored.get('folder') != folder or not stored.get('saved'):
            problems.append('saved: no file saved in ' + folder + ': ' + json.dumps(report['saved']))
        if (latest.get('changes') or {}).get('files'):
            problems.append('saved: the note also changed project files: ' + json.dumps(latest.get('changes')))
        if not any(saver + ' saved' in plain_strong(text) and '`' + folder + '/`' in text for text in said(first, save)):
            problems.append('saved: the relayed answer has no "saved ... in `' + folder + '/`" line')
        status = subprocess.run(['git', '-C', str(workspace), 'status', '--porcelain'], capture_output=True,
                                text=True).stdout
        if 'Agent_Working_Folder' in status:
            problems.append('saved: git status shows the working folder: ' + status)
        # The copy box: the saved answer and its files, at the end of the relayed answer.
        refs = latest.get('refs') or {}
        report['refs'] = refs
        boxes = [text[text.rindex('```text\n') + 8:].rsplit('\n```', 1)[0] for text in said(first, save)
                 if '```text\n' in text and ' answer: ' + folder + '/answers/' in text]
        if not boxes:
            problems.append('box: the saved note\'s answer has no copy box naming ' + folder + '/answers/')
        elif not refs.get('answer') or not (workspace / refs['answer']).is_file():
            problems.append('box: the answer file it names does not exist: ' + json.dumps(refs))
        diff = ' '.join(plain_strong(text) for text in said(first, ask(first, '/cli diff ' + editor)))
        if '```diff' not in diff or '+checked' not in diff.casefold():
            problems.append('diff: no diff block with +checked')
        if boxes:
            # Agent to agent: the other agent is given only the box's text, and must find the note's word.
            handoff = first.mark()
            first.send('/d ' + editor.lower() + ' ' + TOOLS_HANDOFF + boxes[-1])
            settle(first, session, 5)
            report['handoff'] = [plain_strong(text)[-200:] for text in said(first, handoff)][-1:]
            if not any(NOTE_WORD in text and editor + ' says' in plain_strong(text) for text in said(first, handoff)):
                problems.append('handoff: ' + editor + ' did not find ' + NOTE_WORD + ' through the copy box')
        timed = ' '.join(said(first, ask(first, '/cli timeout 90m')))
        if 'now stop after 90 minutes' not in timed:
            problems.append('timeout: no "now stop after 90 minutes"')
        first.close()  # The session ends with both agents still open.
        later = Session(workspace, model)
        sessions.append(later)
        listing = ' '.join(said(later, ask(later, '/cli attach')))
        with later.lock:
            session_two = next(event.get('session_id') for event in later.events if event.get('session_id'))
        report['sessions'] = [session, session_two]
        if editor not in listing:
            problems.append('attach: ' + editor + ' not listed: ' + listing[:300])
        attached = ' '.join(said(later, ask(later, '/cli attach ' + editor.lower())))
        if 'is attached' not in attached:
            problems.append('attach: no "is attached": ' + attached[:300])
        if any(item['alias'] == editor for item in saved_state(session, workspace)['owned']):
            problems.append('attach: the earlier session still owns ' + editor)
        recall = later.mark()
        later.send('/d ' + TOOLS_RECALL)
        settle(later, session_two, 1)
        if not any('checked' in text.casefold() and editor + ' says' in plain_strong(text)
                   for text in said(later, recall)):
            problems.append('attach: ' + editor + ' did not remember its earlier turn')
        closed = ' '.join(plain_strong(text) for text in said(later, ask(later, '/cli close all')))
        if 'CLI-MODE is off' not in closed:
            problems.append('close all: no "CLI-MODE is off"')
    except (RuntimeError, StopIteration) as exc:
        problems.append(str(exc))
    finally:
        for host in sessions:
            host.close()
    for index, host in enumerate(sessions):
        with host.lock:
            events = list(host.events)
        (workspace / ('host-events-%d.jsonl' % index)).write_text('\n'.join(json.dumps(event) for event in events),
                                                                  encoding='utf-8')
        for event in events:
            for block in (event.get('message') or {}).get('content') or [] if event.get('type') == 'assistant' else []:
                command = (block.get('input') or {}).get('command') or ''
                if block.get('type') == 'tool_use' and not (
                        block.get('name') in ('Bash', 'PowerShell') and 'controller.py' in command
                        and (' follow --request ' in command or ' relay --request ' in command)):
                    problems.append('session %d used %s %s' % (index, block.get('name'), command[-100:]))
        problems += ['session %d %s' % (index, problem) for problem in folded_answers(events, plain_strong)]
    report['problems'] = problems
    report['passed'] = not problems
    import host as host_module
    from controller import Controller
    from state import Store
    host_module.select(host_module.CLAUDE)
    for each in report.get('sessions') or ([session] if session else []):
        store = Store(each, workspace, claude_data())
        if store.path.exists():
            Controller(store).off()
    (workspace / 'host-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not keep:
        shutil.rmtree(workspace, ignore_errors=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', default='grok-build', help='agy, claude, grok-build, cursor, copilot or codex.')
    parser.add_argument('--agents', help='Two agents at once, comma-separated (for example grok-build,codex).')
    parser.add_argument('--tools', action='store_true', help='With --agents: one prompt to both, a change receipt, '
                        '/cli diff, /cli timeout and /cli attach, in a git repository.')
    parser.add_argument('--model', help='Claude Code model for the host turns (default: your configured model).')
    parser.add_argument('--keep', action='store_true', help='Keep the workspace and CLI-MODE state for inspection.')
    args = parser.parse_args()
    assert (DEV / '.claude-plugin/plugin.json').is_file(), 'Build first: python scripts/package_plugin.py'
    if args.agents:
        pair = [item.strip() for item in args.agents.split(',') if item.strip()]
        if len(pair) != 2:
            raise SystemExit('--agents takes exactly two agents.')
        report = (run_tools if args.tools else run_pair)(pair, args.model, args.keep)
        print(json.dumps(dict(marker=MARKER, report=report), indent=2, ensure_ascii=False))
        raise SystemExit(0 if report['passed'] else 1)
    report = run(args.agent, args.model, args.keep)
    for step in report.get('turns', {}).values():
        step['result'] = step['result'][:1500]
    print(json.dumps(dict(marker=MARKER, report=report), indent=2, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
