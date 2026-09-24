"""Drive one real Claude Code session through a script of prompts, using the INSTALLED plugin.

Unlike the other checks, this uses your real Claude Code configuration and the
CLI-MODE plugin as installed there, so it shows real-world behavior. Prompts that
CLI-MODE answers itself cost nothing; prompts that reach Claude use your plan's
quota, as do agent turns (the agent's own account).

    python checks/claude_session_script.py --project <folder> "/cli help" "/cli" "5" ...
    python checks/claude_session_script.py --claude "%APPDATA%\\Claude\\claude-code\\2.1.280\\claude.exe" ...

Each prompt runs as one `claude -p` turn in the same session (the first with
--session-id, the rest with --resume). The report lists, per turn: whether the
hook answered it (0 turns), cost, tools used, permission denials, and the reply.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def run_turn(binary, prompt, session, project, first, model=None):
    args = [binary, '-p', prompt, '--output-format', 'stream-json', '--verbose',
            '--session-id' if first else '--resume', session]
    if model:
        args += ['--model', model]
    env = {key: value for key, value in os.environ.items() if not key.startswith('CLI_MODE_')}
    env['MSYS_NO_PATHCONV'] = '1'
    started = time.monotonic()
    result = subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=900, stdin=subprocess.DEVNULL)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    final = next((event for event in reversed(events) if event.get('type') == 'result'), {})
    tools = []
    for event in events:
        if event.get('type') != 'assistant':
            continue
        for block in (event.get('message') or {}).get('content') or []:
            if block.get('type') == 'tool_use':
                given = block.get('input') or {}
                tools.append(dict(name=block.get('name'), command=given.get('command'),
                                  timeout=given.get('timeout'), input=json.dumps(given)[:200]))
    return dict(prompt=prompt, seconds=round(time.monotonic() - started, 1), turns=final.get('num_turns'),
                sessionCostUsd=final.get('total_cost_usd') or 0, denials=final.get('permission_denials') or [],
                tools=tools, result=final.get('result') or '', error=None if final else result.stderr[-1500:])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('prompts', nargs='+')
    parser.add_argument('--project', required=True, type=Path, help='Folder the session runs in.')
    parser.add_argument('--claude', default=shutil.which('claude'), help='Claude Code binary (default: claude on PATH).')
    parser.add_argument('--model', help='Model for turns that reach Claude (default: your configured model).')
    parser.add_argument('--session', default=str(uuid.uuid4()))
    parser.add_argument('--resume', action='store_true', help='Continue --session instead of starting it.')
    args = parser.parse_args()
    report, spent = [], 0
    for index, prompt in enumerate(args.prompts):
        step = run_turn(args.claude, prompt, args.session, args.project.resolve(), index == 0 and not args.resume,
                        args.model)
        step['costUsd'] = round(max(step.pop('sessionCostUsd') - spent, 0), 4)
        spent += step['costUsd']
        report.append(step)
        print(json.dumps(step), flush=True)
    print(json.dumps(dict(session=args.session, totalCostUsd=round(spent, 4))))


if __name__ == '__main__':
    main()
