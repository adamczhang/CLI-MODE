"""Live check of the Stop guard on the INSTALLED plugin: a relay that Claude ends early is resumed.

The guard (hooks/claude.py `stop`) answers a turn end while a relay is unfinished with the
next relay command, up to MAX_NUDGES times. Nothing in P7b makes Claude stop early, and an
instruction to stop does not either (Claude follows the relay context), so for this turn only
a test hook (--settings) cancels the second relay call and asks Claude to end the turn, while
the agent runs a 45-second command: the first 25-second call is sure to come back unfinished.
It passes when the guard fired, Claude ran the next relay command with its cursor, the agent's
final words ended the turn, and the relay was saved as done.

It spends real quota: a few Claude turns on your plan, and one short agent task.

    python checks/claude_stop_guard_live.py [--agent codex] [--keep]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from claude_user_validation import (BUILD, CACHE, DATA, INSTALLED, Session, agent_label, desktop_claude, expect,
                                    fingerprint, verbatim)


# A PreToolUse hook that holds relay calls after the first until the guard has fired, so the turn must end early.
# It says it is a test: when it claimed the user had stopped the relay, Claude rightly put that above the guard
# and never resumed; denying one call only, Claude just retried and never ended the turn.
STOPPER = '''import hashlib, json, pathlib, sys
event = json.load(sys.stdin)
if ' relay ' in (event.get('tool_input') or {}).get('command', ''):
    counter = pathlib.Path(__file__).with_suffix('.count')
    count = int(counter.read_text()) + 1 if counter.is_file() else 1
    counter.write_text(str(count))
    state = pathlib.Path(DATA) / 'sessions' / (hashlib.sha256(event['session_id'].encode()).hexdigest() + '.json')
    nudged = state.is_file() and any((json.loads(state.read_text(encoding='utf-8')).get('relayNudges') or {}).values())
    if count >= 2 and not nudged:  # Held until the guard has resumed the turn once.
        held = pathlib.Path(__file__).with_suffix('.held')
        held.write_text(str(int(held.read_text()) + 1 if held.is_file() else 1))
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
              'permissionDecisionReason': 'A test harness is holding relay calls until this turn ends, to check how '
                                          'an ended turn is resumed. End this turn now, posting nothing; a system '
                                          'message will then say how to continue.'}}))
'''


def stopper(evidence):
    """--settings for one turn: the stopper hook on Bash and PowerShell calls."""
    script = evidence / 'stopper.py'
    script.write_text('DATA = ' + repr(str(DATA)) + '\n' + STOPPER, encoding='utf-8')
    hook = {'type': 'command', 'command': 'python "' + script.as_posix() + '"'}
    return json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash|PowerShell', 'hooks': [hook]}]}})


def run(agent, keep):
    evidence = Path(tempfile.gettempdir()) / ('cli-mode-stop-guard-' + time.strftime('%Y%m%d-%H%M%S'))
    evidence.mkdir()
    project = evidence / 'garden-app'
    project.mkdir()
    copies = {'build': fingerprint(BUILD),
              'installed': fingerprint(INSTALLED), 'cache': fingerprint(sorted(CACHE.iterdir())[-1])}
    if len(set(copies.values())) != 1:
        raise SystemExit('The three copies differ; reinstall before validating: ' + json.dumps(copies))
    session = Session(desktop_claude(), project, evidence)
    t = session.send('/cli bind ' + agent, 'bind')
    expect(t, 'CLI-MODE Activated' in session.shown(t), 'bind failed')
    t = session.send('/d Run this exact shell command and wait for it to finish: python -c "import time; '
                     'time.sleep(45)". Then reply with only the word finished.', 'early stop',
                     extra=('--settings', stopper(evidence)))
    # The stopper's denials are the test, and so is the ended turn's last message, which explains them.
    t['problems'] = [problem for problem in t['problems']
                     if not problem.startswith(('permission denials', 'text posted between relay calls'))]
    if len(t['texts']) > 2:
        t['notes'].append('before the guard: ' + t['texts'][1][:140].replace('\n', ' / '))
    state = session.state()
    request = (state.get('turnRoute') or {}).get('requestId')
    relays = [tool['command'] for tool in t['tools'] if ' relay ' in tool['command']]
    nudges = (state.get('relayNudges') or {}).get(request, 0)
    held = int((evidence / 'stopper.held').read_text()) if (evidence / 'stopper.held').is_file() else 0
    t['notes'].append('relay calls: %d (%d held), guard nudges: %d' % (len(relays), held, nudges))
    expect(t, held >= 1, 'the relay was never held, so the guard was not exercised')
    expect(t, nudges >= 1, 'the Stop guard never fired')
    expect(t, len(relays) > held + 1, 'Claude did not run another relay command after the guard')
    expect(t, re.search(r'--cursor \d+', relays[-1]), 'no --cursor on the resumed relay')
    expect(t, ((state.get('relayProgress') or {}).get(request) or {}).get('done'), 'relay not saved as done')
    expect(t, agent_label(agent, short=True) + ' says...**' in session.shown(t), 'no attribution in the final text')
    verbatim(session, t, request)
    t = session.send('/cli stop', 'closing')
    expect(t, not session.state().get('active'), 'stop left the agent active')

    print(json.dumps(dict(copies=copies, session=session.id, evidence=str(evidence), turns=len(session.turns))))
    for index, turn in enumerate(session.turns, 1):
        status = 'FAIL ' + '; '.join(turn['problems']) if turn['problems'] else 'ok'
        print('%02d %-12s %-44s %5ss %s' % (index, turn['scenario'], turn['prompt'][:44], turn['seconds'], status))
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', default='codex', choices=('codex', 'copilot', 'agy', 'grok-build'))
    parser.add_argument('--keep', action='store_true', help='Keep the test project and session state.')
    args = parser.parse_args()
    raise SystemExit(run(args.agent, args.keep))
