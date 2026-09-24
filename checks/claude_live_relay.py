"""Live check: Claude Code as the host, driving a real CLI agent through CLI-MODE.

This spends quota: Claude Code's model for the relay turn, and the agent's own
usage. It uses your Claude Code sign-in, but loads the unpacked build
(dist/claude-dev/cli-mode) for these sessions only; it does not install or
enable anything in your Claude Code settings.

For each agent it runs three turns in one Claude Code session:
1. /cli bind <agent>: Claude runs the exact bind command, which CLI-MODE's
   hook must approve (no permission denials) and which returns the confirmation.
2. /d <task>: the agent reads a file and answers; Claude relays it. The
   agent's final message must appear verbatim in Claude's output, no subagent
   may be used, and every shell command must be CLI-MODE's controller.
3. /cli stop: executed by the hook; chat display uses one model turn to show it,
   while instant display uses zero model turns.

    python scripts/package_plugin.py
    python checks/claude_live_relay.py --agents copilot [--shell auto|bash|powershell] [--model <id>]
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
DEV = PROJECT / 'dist' / 'claude-dev' / 'cli-mode'
MARKER = 'PERIWINKLE-' + uuid.uuid4().hex[:6].upper()
SHELLS = {'bash': 'Run shell commands with the Bash tool.', 'powershell': 'Run shell commands with the PowerShell tool.'}


def claude_data():
    config = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    return config / 'plugins' / 'data' / 'cli-mode-inline'


def claude_binary():
    found = Path(shutil.which('claude') or 'claude')
    if os.name == 'nt' and found.suffix.lower() in ('.cmd', '.bat'):
        # npm's %* forwarding reparses quotes in prompts and --settings JSON.
        native = found.parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
        if native.is_file():
            return str(native)
    return str(found)


def turn(prompt, session, workspace, first, shell, model, extra=()):
    """One `claude -p` turn in the shared session, with every event recorded."""
    args = [claude_binary(), '-p', prompt, '--plugin-dir', str(DEV), '--output-format', 'stream-json',
            '--verbose', '--session-id' if first else '--resume', session]
    if shell in SHELLS:
        args += ['--append-system-prompt', SHELLS[shell]]
    if model:
        args += ['--model', model]
    args += list(extra)
    env = {key: value for key, value in os.environ.items() if not key.startswith('CLI_MODE_')}
    env['MSYS_NO_PATHCONV'] = '1'
    started = time.monotonic()
    result = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=900, stdin=subprocess.DEVNULL)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    final = next((event for event in reversed(events) if event.get('type') == 'result'), {})
    tools = [dict(name=block.get('name'), command=(block.get('input') or {}).get('command'),
                  input=json.dumps(block.get('input'))[:300])
             for event in events if event.get('type') == 'assistant'
             for block in (event.get('message') or {}).get('content') or [] if block.get('type') == 'tool_use']
    blocks = [block for event in events if event.get('type') == 'assistant'
              for block in (event.get('message') or {}).get('content') or []]
    texts = [block['text'] for block in blocks if block.get('type') == 'text']
    order = [block['type'] for block in blocks if block.get('type') in ('text', 'tool_use')]
    # total_cost_usd is cumulative for a resumed session; run_agent turns it into per-turn cost.
    return dict(prompt=prompt, seconds=round(time.monotonic() - started, 1), turns=final.get('num_turns'),
                sessionCostUsd=final.get('total_cost_usd') or 0, denials=final.get('permission_denials') or [],
                tools=tools, texts=texts, order=order, result=final.get('result') or '',
                error=(str(final.get('errors') or final.get('result') or result.stderr[-2000:] or
                           'Host failed without a result')
                       if not final or final.get('is_error') or result.returncode else None))


def final_agent_message(session, workspace):
    """The agent's last message, rebuilt from CLI-MODE's public event log."""
    sys.path.insert(0, str(DEV / 'scripts'))
    from state import Store
    import relay_view
    store = Store(session, workspace, claude_data())
    state = store.read()
    request = state['turnRoute'].get('requestId') or next(reversed(state['requests']))
    path = Path(state['requests'][request]['events'])
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return relay_view.messages(events, last_only=True).strip()


def run_agent(agent, shell, model, keep):
    workspace = Path(tempfile.mkdtemp(prefix='cli-mode-live-' + agent + '-')).resolve()
    (workspace / 'README.md').write_text('# Notes\n\nThe project codename is ' + MARKER + '. It tracks garden '
                                         'planting dates and watering reminders.\n', encoding='utf-8')
    session = str(uuid.uuid4())
    report = dict(agent=agent, session=session, shell=shell, workspace=str(workspace))
    try:
        display = json.loads((claude_data() / 'display.json').read_text(encoding='utf-8')).get('display', 'chat')
    except (OSError, ValueError, AttributeError):
        display = 'chat'
    report['display'] = display
    try:
        bind = turn('/cli bind ' + agent, session, workspace, True, shell, model)
        task = turn('/d Read README.md in this folder. Reply in two short paragraphs: what the project tracks, '
                    'and its codename.', session, workspace, False, shell, model)
        stop = turn('/cli stop', session, workspace, False, shell, model)
        report['turns'] = [bind, task, stop]
        spent = 0
        for step in report['turns']:
            step['costUsd'] = round(max(step['sessionCostUsd'] - spent, 0), 4)
            spent = max(spent, step['sessionCostUsd'])
        problems = []
        for step in (bind, task, stop):
            if step['error']:
                problems.append(step['prompt'] + ': no result: ' + step['error'])
            if step['denials']:
                problems.append(step['prompt'] + ': permission denials ' + json.dumps(step['denials']))
            for tool in step['tools']:
                if tool['name'] in ('Agent', 'Task'):
                    problems.append(step['prompt'] + ': used a subagent')
                elif tool['name'] in ('Bash', 'PowerShell') and 'controller.py' not in (tool['command'] or ''):
                    problems.append(step['prompt'] + ': ran a non-CLI-MODE command: ' + str(tool['command']))
                elif tool['name'] not in ('Bash', 'PowerShell'):
                    problems.append(step['prompt'] + ': used ' + str(tool['name']))
        sys.path.insert(0, str(DEV / 'scripts'))
        from presentation import plain_strong
        if 'CLI-MODE Activated' not in plain_strong(bind['result']):
            problems.append('bind: no activation confirmation')
        if MARKER not in task['result']:
            problems.append('task: the codename did not come back')
        shown = plain_strong('\n\n'.join(task['texts']))
        if shown.count('**Passing to ') != 1:
            problems.append('task: expected exactly one passing announcement')
        order = task['order']
        calls = [i for i, kind in enumerate(order) if kind == 'tool_use']
        if calls and ('text' in order[calls[0] + 1:calls[-1]] or order[0] != 'text'):
            problems.append('task: passing must precede tools and no text may be posted between relay calls')
        try:
            words = final_agent_message(session, workspace)
            report['finalAgentMessage'] = words
            if words and words not in task['result']:
                problems.append('task: the agent\'s final message was not posted verbatim')
        except (OSError, KeyError, ValueError, StopIteration) as exc:
            problems.append('task: could not read the event log: ' + str(exc))
        expected_turns = 0 if display == 'instant' else 1
        if (stop['turns'] != expected_turns or stop['tools'] or
                (display == 'instant' and stop['costUsd'] != 0) or
                'CLI-MODE is off' not in plain_strong(stop['result'])):
            problems.append('stop: did not match hook execution and ' + display + ' display')
        report['shells'] = sorted({tool['name'] for step in (bind, task) for tool in step['tools']})
        report['costUsd'] = round(spent, 4)
        report['problems'] = problems
        report['passed'] = not problems
    finally:
        # Always close only this test's ownership, even if a host turn timed out
        # before it could issue /cli stop. Preserve evidence for failed runs.
        sys.path.insert(0, str(DEV / 'scripts'))
        import host
        from controller import Controller
        from state import Store
        host.select(host.CLAUDE)
        store = Store(session, workspace, claude_data())
        if store.path.exists():
            report['cleanup'] = Controller(store).off()['shutdownComplete']
            if not report['cleanup']:
                report['passed'] = False
        (workspace / 'host-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        key = hashlib.sha256(session.encode()).hexdigest()
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)
            for path in (claude_data() / 'sessions').glob(key + '*'):
                path.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agents', default='copilot', help='Comma-separated: agy, claude, grok-build, cursor, copilot, codex.')
    parser.add_argument('--shell', choices=('auto', 'bash', 'powershell'), default='auto')
    parser.add_argument('--model', help='Claude Code model for the host turns (default: your configured model).')
    parser.add_argument('--keep', action='store_true', help='Keep workspaces and CLI-MODE state for inspection.')
    args = parser.parse_args()
    assert (DEV / '.claude-plugin/plugin.json').is_file(), 'Build first: python scripts/package_plugin.py'
    reports = [run_agent(agent.strip(), args.shell, args.model, args.keep) for agent in args.agents.split(',')]
    for report in reports:
        for step in report.get('turns', []):
            step['result'] = step['result'][:1500]
    print(json.dumps(dict(marker=MARKER, agents=reports), indent=2))
    if not all(report.get('passed') for report in reports):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
