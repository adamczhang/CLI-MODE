"""Opt-in real-provider cancellation and queued-follow-up validation.

Uses the source hook and an isolated conversation. It does not verify installed
Codex Desktop hook trust or presentation.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_coding_queue import ROOT, hook_message, require, save

sys.path.insert(0, str(ROOT / 'plugins/cli-mode/scripts'))
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/hooks'))
import adapters
from controller import Controller
import route as hook
from state import Store


def settled(controller, request_id, deadline):
    cursor = 0
    events = []
    while time.monotonic() < deadline:
        result = controller.observe(request_id, cursor=cursor)
        cursor = result['cursor']
        events.extend(result['events'])
        status = result['receipt']['status']
        if status not in ('captured', 'submitting'):
            return dict(status=status, eventTypes=dict(Counter(e.get('type') for e in events)),
                        answer=''.join(e.get('text', '') for e in events if e.get('type') == 'message'),
                        result=result['receipt'].get('result'))
        time.sleep(.5)
    raise TimeoutError(f'Request {request_id} did not settle: {controller.queue_status()}')


def run(agent, folder):
    folder.mkdir(parents=True, exist_ok=True)
    workspace = folder / 'workspace'
    workspace.mkdir(exist_ok=True)
    store = Store('live-cancel-' + uuid.uuid4().hex, workspace, folder / 'state')
    controller = Controller(store, agent=agent)
    marker = 'CANCEL_QUEUE_' + uuid.uuid4().hex[:12].upper()
    evidence = dict(agent=agent, sourceCommit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        hostIntegrationVerified=False, marker=marker, workspace=str(workspace),
        checks={}, success=False)
    save(folder / 'evidence.json', evidence)
    try:
        hook.handle(dict(hook_event_name='SessionStart', session_id=store.thread,
                         cwd=str(workspace), source='startup'), store.root)
        start = time.monotonic()
        state = controller.bind(agent, require_hooks=True)
        main = state['main']
        evidence['checks']['activationSeconds'] = round(time.monotonic() - start, 2)
        require(state['active'] and len(state['owned']) == 1,
                'Bind did not activate exactly one owned session')
        print(agent, 'activated in', evidence['checks']['activationSeconds'], 's', flush=True)

        prompt = ('Write a detailed tutorial on building a small event loop, with twenty sections '
                  'and several examples per section. Explain each example carefully. '
                  'This is a writing task only; do not use tools.')
        route, _ = hook_message(controller, '/d ' + prompt)
        require(route['route'] == 'direct' and route.get('requestId'),
                'Long turn was not captured')
        first = route['requestId']
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            observation = controller.observe(first)
            if observation['receipt']['status'] == 'submitting':
                break
            require(observation['receipt']['status'] == 'captured',
                    'Long turn settled before cancellation could be tested')
            time.sleep(.2)
        require(controller.observe(first)['receipt']['status'] == 'submitting',
                'Long turn did not start')
        route, _ = hook_message(controller,
            '/d Reply with exactly ' + marker + '. Do not use tools.')
        require(route['route'] == 'direct' and route.get('requestId'),
                'Follow-up was not captured')
        second = route['requestId']
        require(controller.observe(second)['receipt']['status'] == 'captured',
                'Follow-up was not waiting behind active turn')
        evidence['checks']['queued'] = [first, second]
        route, _ = hook_message(controller, '/cli cancel')
        require(route['route'] == 'cancel', 'Cancel control did not route locally')
        first_result = settled(controller, first, time.monotonic() + 120)
        evidence['checks']['canceledTurn'] = first_result
        require(first_result['status'] == 'canceled',
                f'Active turn did not settle as canceled: {first_result["status"]}')
        second_result = settled(controller, second, time.monotonic() + 300)
        evidence['checks']['queuedFollowup'] = second_result
        require(second_result['status'] == 'completed' and marker in second_result['answer'],
                'Queued follow-up did not complete correctly after cancellation')
        require(controller.store.read()['main'] == main,
                'Cancellation changed main session ownership')
        require(not controller.store.read()['inflight'],
                'Cancellation left an unresolved inflight operation')
        evidence['checks']['sameMain'] = True
        evidence['success'] = True
        print(agent, 'canceled active turn; queued follow-up completed', flush=True)
    except Exception as exc:
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        print(agent, 'FAILED:', evidence['error'], flush=True)
    finally:
        try:
            evidence['finalShutdown'] = controller.off()
            if not evidence['finalShutdown']['shutdownComplete']:
                evidence['success'] = False
                evidence['shutdownError'] = 'Owned session cleanup incomplete'
        except Exception as exc:
            evidence['success'] = False
            evidence['shutdownError'] = f'{type(exc).__name__}: {exc}'
        save(folder / 'evidence.json', evidence)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=adapters.implemented(), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.agent, args.output.resolve())
    raise SystemExit(0 if result['success'] else 1)
