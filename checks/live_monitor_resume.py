"""Opt-in live check: interrupt a host observer, then resume monitoring a CLI turn."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_coding_queue import cli, hook, hook_message, require, save
from controller import Controller
from state import Store


def run(folder, agent='agy'):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir(exist_ok=True)
    control = Controller(Store('monitor-resume-' + uuid.uuid4().hex, work, folder / 'state'), agent=agent)
    evidence = dict(workspace=str(work), agent=agent)
    monitor = None
    try:
        hook.handle(dict(hook_event_name='SessionStart', session_id=control.store.thread,
                         cwd=str(work), source='startup'), control.store.root)
        active = cli(control, 'bind', '--agent', agent)
        require(active['active'], 'Activation failed')
        evidence['main'] = active['main']
        prompt = ('Create a Python file slow_check.py in this workspace containing a correct sieve of Eratosthenes '
                  'function primes_below(n). Create unittest tests for edge cases and n=10000; run the tests. '
                  'In the final answer include the exact token RESUME_MONITOR_OK and the number of primes below 10000.')
        route, _ = hook_message(control, '/d ' + prompt)
        require(route.get('requestId'), 'Prompt was not captured')
        request = route['requestId']
        evidence['requestId'] = request
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            view = cli(control, 'observe', '--request', request)
            if view['receipt']['status'] == 'submitting':
                break
            time.sleep(.2)
        require(view['receipt']['status'] == 'submitting', 'Turn did not reach provider submission')
        evidence['beforeInterruption'] = dict(status=view['receipt']['status'],
                                              workerState=view['workerState'], cursor=view['cursor'])
        watcher = '''import json,subprocess,sys,time
while True:
 p=subprocess.run([sys.executable,*sys.argv[1:]],capture_output=True,text=True)
 if p.returncode: raise SystemExit(p.returncode)
 x=json.loads(p.stdout)
 print(x['receipt']['status'],flush=True)
 time.sleep(.2)
'''
        command = [sys.executable, '-u', '-c', watcher, str(Path(__file__).resolve().parents[1] / 'plugins/cli-mode/scripts/controller.py'),
                   '--thread', control.store.thread, '--workspace', control.store.workspace,
                   '--data-root', str(control.store.root), 'observe', '--request', request]
        monitor = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        observed = monitor.stdout.readline().strip()
        require(observed in ('submitting', 'completed'), 'Observer did not read request')
        monitor.terminate()
        monitor.wait(timeout=10)
        evidence['interruptedMonitor'] = dict(firstStatus=observed, exitCode=monitor.returncode)
        resumed = cli(control, 'resume')
        evidence['resumeCommand'] = resumed
        cursor, seen = 0, []
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            view = cli(control, 'observe', '--request', request, '--cursor', cursor)
            cursor = view['cursor']
            seen += view['events']
            if view['receipt']['status'] not in ('captured', 'submitting'):
                break
            time.sleep(.5)
        evidence['afterReattach'] = dict(status=view['receipt']['status'], workerState=view['workerState'],
                                         eventTypes=[x.get('type') for x in seen],
                                         answer=''.join(x.get('text', '') for x in seen if x.get('type') == 'message'))
        relay = cli(control, 'relay', '--request', request, '--cursor', 0, '--wait', 0)
        evidence['relayDone'] = relay['done']
        require(view['receipt']['status'] == 'completed' and relay['done'],
                'Reattached monitor did not observe completion')
        require('RESUME_MONITOR_OK' in evidence['afterReattach']['answer'], 'Final marker missing')
    except Exception as exc:
        evidence['error'] = str(exc)
    finally:
        if monitor and monitor.poll() is None:
            monitor.terminate()
        try:
            evidence['shutdownComplete'] = cli(control, 'off')['shutdownComplete']
        except Exception as exc:
            evidence['shutdownError'] = str(exc)
        save(folder / 'evidence.json', evidence)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--agent', default='agy')
    args = parser.parse_args()
    result = run(args.output.resolve(), args.agent)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(1 if 'error' in result or not result.get('shutdownComplete') else 0)
