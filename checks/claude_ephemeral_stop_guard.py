"""Live Stop-guard fault injection with an ephemeral plugin, no user install."""
import argparse
import json
from pathlib import Path
import re
import sys
import uuid

from claude_live_relay import DEV, claude_data, turn
from claude_stop_guard_live import STOPPER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--agent', default='codex')
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    session = str(uuid.uuid4())
    sys.path.insert(0, str(DEV / 'scripts'))
    from controller import Controller
    from state import Store
    from presentation import plain_strong
    import host
    host.select(host.CLAUDE)
    store = Store(session, folder, claude_data())
    report = dict(agent=args.agent, session=session, ephemeralPlugin=str(DEV), turns=[])
    try:
        bound = turn('/cli bind ' + args.agent, session, folder, True, 'auto', 'sonnet')
        report['turns'].append(bound)
        assert not bound['error'] and 'CLI-MODE Activated' in plain_strong(bound['result']), 'Bind failed'
        script = folder / 'stopper.py'
        script.write_text('DATA = ' + repr(str(claude_data())) + '\n' + STOPPER, encoding='utf-8')
        settings = json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash|PowerShell', 'hooks': [
            {'type': 'command', 'command': 'python "' + script.as_posix() + '"'}]}]}})
        response = turn('/d Run this exact shell command and wait for it to finish: python -c "import time; '
                        'time.sleep(45)". Then reply only VERIFIED_STOP_GUARD.',
                        session, folder, False, 'auto', 'sonnet', ('--settings', settings))
        report['turns'].append(response)
        state = store.read()
        request = state['turnRoute']['requestId']
        relays = [t['command'] for t in response['tools'] if ' relay ' in (t['command'] or '')]
        held_file = folder / 'stopper.held'
        held = int(held_file.read_text()) if held_file.exists() else 0
        nudges = state.get('relayNudges', {}).get(request, 0)
        report.update(heldCalls=held, nudges=nudges, relayCalls=len(relays))
        assert held >= 1 and nudges >= 1, 'Fault injection did not exercise the Stop guard'
        assert len(relays) > held + 1 and re.search(r'--cursor \d+', relays[-1]), 'Relay was not resumed'
        assert state.get('relayProgress', {}).get(request, {}).get('done'), 'Relay not saved as done'
        assert 'VERIFIED_STOP_GUARD' in response['result'] and not response['error'], 'Final output missing'
        assert len(state['requests']) == 1, 'Request was resent'
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False, error=str(exc))
    finally:
        report['shutdownComplete'] = Controller(store).off()['shutdownComplete']
        (folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k != 'turns'}), flush=True)
    return 0 if report['passed'] and report['shutdownComplete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
