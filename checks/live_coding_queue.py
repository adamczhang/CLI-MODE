"""Opt-in live menu, detached queue, coding, and shutdown validation.

Uses the source hook/controller with isolated state and workspaces. This does not
claim that the installed Codex Desktop plugin rendered or trusted a menu.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/scripts'))
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/hooks'))

import adapters
from controller import Controller
import frontends
import route as hook
from state import Store


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def cli(controller, *args):
    """Exercise the real command entrypoint and its renderer contract, not just methods."""
    folder = controller.store.root.parent / 'presentation'
    folder.mkdir(exist_ok=True)
    ident = args[0] + '-' + uuid.uuid4().hex[:8]
    command = [sys.executable, str(ROOT / 'plugins/cli-mode/scripts/controller.py'),
               '--thread', controller.store.thread, '--workspace', controller.store.workspace,
               '--data-root', str(controller.store.root),
               '--menu-output', str(folder / (ident + '-menu.html')),
               '--message-output', str(folder / (ident + '-message.html')), *map(str, args)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=240)
    require(completed.returncode == 0, f'{args[0]} CLI failed: {completed.stdout} {completed.stderr}')
    result = json.loads(completed.stdout)
    save(folder / (ident + '.json'), result)
    views = [result.get('menuView'), result.get('messageView'),
             (result.get('activation') or {}).get('messageView')]
    for view in filter(None, views):
        reference = view.get('reference') or result.get('reference', '')
        require(reference.startswith('\ue200visualize\ue202') and reference.endswith('\ue201'),
                'View lacks Codex rendering delimiters')
        payload = json.loads(reference[len('\ue200visualize\ue202'):-1])
        require(Path(payload['path']) == Path(view['path']), 'Reference points at another file')
        html = Path(view['path']).read_text(encoding='utf-8')
        require('<section ' in html and '</section>' in html, 'Missing HTML fragment')
    return result


def main_session(controller):
    state = controller.store.read()
    require(state['active'] and len(state['owned']) == 1 and state['main'] == state['owned'][0]['name'],
            'Expected one active main session')
    return state['main']


def hook_message(controller, message):
    output = hook.handle(dict(hook_event_name='UserPromptSubmit', session_id=controller.store.thread,
                              cwd=controller.store.workspace, prompt=message), controller.store.root)
    route = controller.store.read().get('turnRoute') or {}
    return route, output


def choose_displayed_default(controller, phase, agent):
    state = controller.store.read()
    pending = state['pending']
    snapshot = pending['draft']['snapshot']
    settings = pending['draft']['settings']
    wanted = frontends.selected_key(controller.store.root, agent, phase, settings, snapshot)
    choices = pending['choices']
    index = next((i for i, item in enumerate(choices, 1) if item['value'] == wanted), None)
    if index is None and phase == 'effort':
        fallback = adapters.module(agent).DEFAULTS.get('effort')
        if fallback is not None:
            index = next((i for i, item in enumerate(choices, 1)
                          if str(item['value']).casefold() == str(fallback).casefold()
                          or str(item['label']).casefold() == str(fallback).casefold()), None)
    if wanted is None and len(choices) == 1 and choices[0]['value'] is None:
        index = 1
    require(index is not None, f'No displayed {phase} option matches saved default {wanted}')
    # Pages have continuous option numbers. Navigate until the saved choice is visible.
    while True:
        page = frontends.phase_menu(controller.store.root, agent, phase, settings,
                                    pending['page'], snapshot)
        if page['start'] <= index < page['start'] + page['shown']:
            break
        require(pending['page'] < page['pages'], f'{phase} default is not reachable in menu')
        cli(controller, 'navigate', '>')
        pending = controller.store.read()['pending']
    return cli(controller, 'choose', index)


def observe_until(controller, request_id, deadline, evidence, label):
    cursor = 0
    relay_cursor = 0
    events = []
    first_event = None
    started = time.monotonic()
    while time.monotonic() < deadline:
        result = controller.observe(request_id, cursor=cursor)
        if result['events'] and first_event is None:
            first_event = time.monotonic() - started
        events.extend(result['events'])
        cursor = result['cursor']
        relayed = cli(controller, 'relay', '--request', request_id, '--cursor', relay_cursor, '--wait', 0,
                      '--view-dir', str(evidence['folder'] / 'presentation'))
        relay_cursor = relayed['cursor']
        status = result['receipt']['status']
        if status in ('completed', 'failed', 'rejected', 'canceled', 'uncertain', 'superseded', 'acknowledged'):
            answer = ''.join(item.get('text', '') for item in events if item.get('type') == 'message')
            row = dict(status=status, seconds=round(time.monotonic() - started, 2),
                       firstPublicEventSeconds=round(first_event, 2) if first_event is not None else None,
                       eventTypes=dict(Counter(item.get('type') for item in events)),
                       result=result['receipt'].get('result'), answer=answer)
            evidence['turns'][label] = row
            save(evidence['folder'] / 'evidence.json', {k: v for k, v in evidence.items() if k != 'folder'})
            require(status == 'completed', f'{label} ended as {status}: {controller.queue_status()}')
            require(any(item.get('type') == 'done' for item in events), f'{label} has no completion event')
            return row
        if result['worker'] is None and status == 'captured':
            worker = controller.ensure_pump()
            require(worker['worker'] != 'blocked', f'{label} queue blocked: {controller.queue_status()}')
        time.sleep(.5)
    raise TimeoutError(f'{label} did not settle before timeout: {controller.queue_status()}')


def run_acceptance(workspace):
    module = workspace / 'timebox.py'
    require(module.is_file(), 'timebox.py was not created')
    program = r'''
from timebox import parse_duration, summarize
assert parse_duration('1:05') == 65
assert parse_duration(' 00:00 ') == 0
assert parse_duration('23:59') == 1439
for value in ('24:00', '1:5', '-1:00', '1:60', '', '1:02:03', None):
    try:
        parse_duration(value)
    except ValueError:
        pass
    else:
        raise AssertionError('accepted invalid duration: ' + repr(value))
assert summarize(['alpha,1:05', '', 'beta,0:30', 'alpha,0:10']) == {'alpha': 75, 'beta': 30}
for lines in (['missing-comma'], [',1:00'], ['alpha,24:00']):
    try:
        summarize(lines)
    except ValueError:
        pass
    else:
        raise AssertionError('accepted invalid record: ' + repr(lines))
'''
    result = subprocess.run([sys.executable, '-B', '-c', program], cwd=workspace,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    require(result.returncode == 0, f'Independent API checks failed: {result.stderr[-1200:]}')
    input_path = workspace / 'acceptance-input.txt'
    input_path.write_text('zeta,0:20\nalpha,1:05\nzeta,0:10\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-B', 'timebox.py', str(input_path), '--json'],
                            cwd=workspace, capture_output=True, text=True, encoding='utf-8', timeout=30)
    require(result.returncode == 0 and result.stderr == '',
            f'CLI valid-input check failed: {result.returncode} {result.stderr[-1200:]}')
    require(result.stdout == '{"alpha": 65, "zeta": 30}\n',
            f'CLI JSON output incorrect: {result.stdout!r}')
    input_path.write_text('broken,24:00\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-B', 'timebox.py', str(input_path), '--json'],
                            cwd=workspace, capture_output=True, text=True, encoding='utf-8', timeout=30)
    require(result.returncode != 0 and result.stderr and not result.stdout,
            'CLI invalid input must fail on stderr without stdout output')
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-p', 'test*.py', '-q'],
                           cwd=workspace, capture_output=True, text=True, encoding='utf-8', timeout=60)
    require(tests.returncode == 0, f'Agent test suite failed: {tests.stderr[-1200:]}')
    return dict(api='pass', cli='pass', invalidInput='pass', agentTests='pass',
                testOutput=tests.stderr[-500:])


def run(agent, folder):
    folder.mkdir(parents=True, exist_ok=True)
    workspace = folder / 'workspace'
    workspace.mkdir(exist_ok=True)
    store = Store('live-code-' + uuid.uuid4().hex, workspace, folder / 'state')
    controller = Controller(store, agent=agent)
    marker = 'QUEUE_' + uuid.uuid4().hex[:12].upper()
    evidence = dict(agent=agent, sourceCommit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        hostIntegrationVerified=False, workspace=str(workspace), marker=marker,
        checks={}, turns={}, folder=folder)
    save(folder / 'evidence.json', {k: v for k, v in evidence.items() if k != 'folder'})
    print(agent, 'evidence:', folder, flush=True)
    try:
        start = time.monotonic()
        hook.handle(dict(hook_event_name='SessionStart', session_id=store.thread,
                         cwd=str(workspace), source='startup'), store.root)
        route, _ = hook_message(controller, '/cli')
        require(route['route'] == 'home', 'First /cli did not route to the home menu')
        home = cli(controller, 'frontend', '--agent', 'home')
        agent_ids = [item['value'] for item in home['pending']['choices']]
        require(len(agent_ids) == 6 and agent in agent_ids and not home['active'],
                'Home menu omitted a backend or activated unexpectedly')
        evidence['checks']['home'] = dict(agents=agent_ids, defaultMode=home['routingMode'],
                                          menu=home['activationMenu'])
        require(home['routingMode'] == 'direct', 'Direct is not the default mode')
        selected = cli(controller, 'choose', agent_ids.index(agent) + 1)
        evidence['checks']['firstPrerequisiteScan'] = selected.get('checks')
        if not selected.get('setupReady'):
            route, _ = hook_message(controller, 'R')
            require(route['route'] == 'setup', 'Setup R did not stay in local onboarding')
            time.sleep(1)
            selected = cli(controller, 'first-time-check', '--agent', agent)
            evidence['checks']['recheckedPrerequisites'] = selected.get('checks')
        require(selected.get('setupReady') and selected.get('confirmed'),
                f'{agent} first-time prerequisite check failed: {selected.get("checks")}')
        page = cli(controller, 'frontend', '--agent', agent)
        require('Direct' in page['activationMenu'] and not page['active'],
                'Activation page did not retain Direct while inactive')
        route, _ = hook_message(controller, '3')
        require(route['route'] == 'mode', 'Activation routing selector did not open')
        cli(controller, 'mode')
        route, _ = hook_message(controller, 'X')
        require(route['route'] == 'mode-dismiss', 'Routing X did not dismiss menu')
        cli(controller, 'mode', '--dismiss')
        cli(controller, 'frontend', '--agent', agent)
        cli(controller, 'options', '--phase', 'model')
        choose_displayed_default(controller, 'model', agent)
        choose_displayed_default(controller, 'effort', agent)
        activated = choose_displayed_default(controller, 'access', agent)
        require(activated['active'], 'Agent did not activate after access selection')
        main = main_session(controller)
        evidence['checks']['activation'] = dict(seconds=round(time.monotonic() - start, 2),
                                                  main=main, settings=activated['settings'],
                                                  mode=activated['routingMode'])
        print(agent, 'activated in', evidence['checks']['activation']['seconds'], 's', flush=True)

        route, _ = hook_message(controller, '/help')
        cli(controller, 'commands')
        require(route['route'] == 'help' and main_session(controller) == main,
                'Help changed the active session')
        route, _ = hook_message(controller, '/cli menu')
        require(route['route'] == 'settings', 'Settings menu control did not route')
        settings = cli(controller, 'settings')
        require('Progress: Activity' in settings['activationMenu'], 'Activity progress is not the default')
        route, _ = hook_message(controller, 'X')
        require(route['route'] == 'settings-dismiss', 'Settings X did not dismiss')
        controller.settings_menu(dismiss=True)
        controller.progress('quiet')
        require(controller.store.read()['progressMode'] == 'quiet', 'Quiet preference did not save')
        controller.progress('activity')
        require(main_session(controller) == main, 'Control-only turns changed the provider session')
        evidence['checks']['controls'] = 'pass'

        prompts = [
            f'''Stage 1 of a three-stage Python coding challenge. Work only in this directory: {workspace}.
Create timebox.py with parse_duration(text) -> integer minutes. Accept H:MM or HH:MM with hours 0-23 and minutes 00-59; surrounding whitespace is allowed. Raise ValueError for invalid input, including non-strings. Add unittest tests in test_timebox.py and run them. Do not implement stages 2 or 3 yet. In your final reply include marker {marker}.''',
            '''Stage 2. Extend the same timebox.py with summarize(lines) -> dict[str, int]. Each nonblank string is label,duration; trim the label, require it to be nonempty, and sum durations by case-sensitive label in first-seen order. Reuse parse_duration and raise ValueError for invalid records. Add and run tests while keeping stage 1 working. Repeat the exact marker from stage 1 in your final reply.''',
            '''Stage 3. Add a command-line interface: python timebox.py INPUT --json. Read UTF-8 records from INPUT, summarize them, and print exactly one JSON line with keys sorted alphabetically. Invalid input must exit nonzero, explain the error on stderr, and print nothing on stdout. Add subprocess CLI tests, run the full test suite, and fix any failures. Repeat the exact marker from stage 1 in your final reply.''',
        ]
        for number, prompt in enumerate(prompts, 1):
            (folder / f'stage-{number}-prompt.txt').write_text(prompt + '\n', encoding='utf-8')
        route, _ = hook_message(controller, '/d ' + prompts[0])
        require(route['route'] == 'direct' and route.get('requestId'), 'Stage 1 was not captured')
        first = route['requestId']
        submit_deadline = time.monotonic() + 45
        while time.monotonic() < submit_deadline:
            status = controller.observe(first)['receipt']['status']
            if status == 'submitting':
                break
            require(status == 'captured', f'Stage 1 unexpectedly settled before queueing: {status}')
            time.sleep(.2)
        require(controller.observe(first)['receipt']['status'] == 'submitting',
                'Stage 1 did not enter active submission before queueing')
        followups = []
        for prompt in prompts[1:]:
            route, _ = hook_message(controller, '/d ' + prompt)
            require(route['route'] == 'direct' and route.get('requestId'), 'Follow-up was not captured')
            followups.append(route['requestId'])
        request_ids = [first, *followups]
        queue = controller.queue_status()
        require([item['requestId'] for item in queue['requests']][-3:] == request_ids,
                'Queue order changed during capture')
        require([controller.observe(key)['receipt']['status'] for key in followups] == ['captured', 'captured'],
                'Follow-ups did not wait behind active stage 1')
        evidence['checks']['queueCapture'] = dict(ids=request_ids, statuses=['submitting', 'captured', 'captured'])
        print(agent, 'queued 2 follow-ups behind active stage 1', flush=True)

        deadline = time.monotonic() + 900
        for number, request_id in enumerate(request_ids, 1):
            row = observe_until(controller, request_id, deadline, evidence, f'stage{number}')
            require(marker in row['answer'], f'Stage {number} did not repeat the conversation marker')
            require(main_session(controller) == main, f'Stage {number} changed main ownership')
            print(agent, f'stage {number}:', row['seconds'], 's,', row['eventTypes'], flush=True)
        evidence['checks']['queueResolution'] = [controller.observe(key)['receipt']['status'] for key in request_ids]
        evidence['checks']['acceptance'] = run_acceptance(workspace)
        print(agent, 'independent coding acceptance passed', flush=True)

        controller.mode('passthrough')
        route, _ = hook_message(controller, 'Reply with exactly PASSTHROUGH_OK. Do not use tools.')
        require(route['route'] == 'delegate' and route.get('requestId'), 'Passthrough did not capture plain prompt')
        row = observe_until(controller, route['requestId'], time.monotonic() + 180, evidence, 'passthrough')
        require('PASSTHROUGH_OK' in row['answer'], 'Passthrough reply incorrect')
        controller.mode('direct')
        route, _ = hook_message(controller, 'This ordinary host message must stay in Codex.')
        require(route['route'] == 'host' and not route.get('requestId'), 'Direct mode captured a host prompt')
        evidence['checks']['routingModes'] = 'pass'

        route, _ = hook_message(controller, '/cli stop')
        require(route['route'] == 'off' and not controller.store.read()['active'],
                'Stop hook did not gate routing immediately')
        stopped = controller.off()
        require(stopped['shutdownComplete'], f'Stop did not close ownership: {stopped}')
        require(controller.off()['shutdownComplete'], 'Repeated stop was not idempotent')
        route, _ = hook_message(controller, '/d Must never reach the provider')
        require(route['route'] == 'hint' and not route.get('requestId'),
                'Off-state direct prompt was captured')
        evidence['checks']['closure'] = stopped
        print(agent, 'closed successfully', flush=True)

        hook.handle(dict(hook_event_name='SessionStart', session_id=store.thread,
                         cwd=str(workspace), source='startup'), store.root)
        reopened = controller.bind(agent, require_hooks=True)
        require(reopened['active'] and len(reopened['owned']) == 1, 'Rebind did not reopen one session')
        route, _ = hook_message(controller, '/d Reply with exactly REOPEN_OK. Do not use tools.')
        require(route.get('requestId'), 'Reopen probe was not captured')
        row = observe_until(controller, route['requestId'], time.monotonic() + 180, evidence, 'reopen')
        require('REOPEN_OK' in row['answer'], 'Reopened session did not respond')
        evidence['checks']['reopen'] = dict(main=reopened['main'], newOwnership=reopened['main'] != main)
        evidence['success'] = True
    except Exception as exc:
        evidence['success'] = False
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
        evidence['finalState'] = dict(active=store.read()['active'], owned=store.read()['owned'],
                                      inflight=store.read()['inflight'])
        save(folder / 'evidence.json', {k: v for k, v in evidence.items() if k != 'folder'})
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=adapters.implemented(), required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = run(arguments.agent, arguments.output.resolve())
    raise SystemExit(0 if result.get('success') else 1)
