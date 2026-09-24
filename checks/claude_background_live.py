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

    python scripts/package_plugin.py
    python checks/claude_background_live.py --agent grok-build [--model <id>] [--keep]
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


def relayed(session, workspace):
    """True once CLI-MODE has shown the latest request's whole answer, whichever turn relayed it."""
    sys.path.insert(0, str(DEV / 'scripts'))
    from state import Store
    try:
        state = Store(session, workspace, claude_data()).read()
        latest = max(state.get('requests') or {}, key=lambda key: state['requests'][key].get('capturedAt') or 0)
        return bool(((state.get('relayProgress') or {}).get(latest) or {}).get('done'))
    except (OSError, ValueError, KeyError):
        return False


def summary(events):
    blocks = [block for event in events if event.get('type') == 'assistant'
              for block in (event.get('message') or {}).get('content') or []]
    result = events[-1] if events and events[-1].get('type') == 'result' else {}
    return dict(
        tools=[dict(name=block.get('name'), command=(block.get('input') or {}).get('command'))
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
            if tool['name'] not in ('Bash', 'PowerShell') or 'controller.py' not in (tool['command'] or ''):
                problems.append(name + ': used ' + str(tool['name']) + ' ' + str(tool['command'])[:120])
    sys.path.insert(0, str(DEV / 'scripts'))
    from presentation import plain_strong
    label = {'grok-build': 'Grok', 'agy': 'Antigravity', 'claude': 'Claude Code', 'codex': 'Codex',
             'copilot': 'Copilot', 'cursor': 'Cursor'}.get(agent, agent)
    task, wake = steps.get('task'), steps.get('wake')
    request = None
    bind = steps.get('bind')
    if bind:
        # Activation runs in the prompt hook: no command for Claude, so no background-tasks row of its own.
        if bind['tools'] or any(item['subtype'] == 'task_started' for item in bind['tasks']):
            problems.append('bind: ran as a command (' + json.dumps(bind['tools'])[:200] + ')')
        if 'CLI-MODE Activated' not in plain_strong(bind['result']):
            problems.append('bind: no activation card')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', default='grok-build', help='agy, claude, grok-build, cursor, copilot or codex.')
    parser.add_argument('--model', help='Claude Code model for the host turns (default: your configured model).')
    parser.add_argument('--keep', action='store_true', help='Keep the workspace and CLI-MODE state for inspection.')
    args = parser.parse_args()
    assert (DEV / '.claude-plugin/plugin.json').is_file(), 'Build first: python scripts/package_plugin.py'
    report = run(args.agent, args.model, args.keep)
    for step in report.get('turns', {}).values():
        step['result'] = step['result'][:1500]
    print(json.dumps(dict(marker=MARKER, report=report), indent=2, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
