"""Plugin-only responsiveness audit for Claude Code: every point where CLI-MODE's code runs.

Claude Code starts the plugin's hook as a new `python` process for every session
start, prompt, `python` command, subagent launch and turn end, in EVERY session,
whether or not CLI-MODE is in use there. Claude also starts the controller as a
new process for every relay and control command. This measures each of those
exactly as Claude Code runs them (the hook with its event on stdin), without any
model or agent: a throwaway data folder and project, a fake agent for the active
session, and no queue worker.

    python checks/claude_performance_audit.py                    # the installed plugin
    python checks/claude_performance_audit.py --plugin plugins/cli-mode --output after.json
    python checks/claude_performance_audit.py --agents           # plus each agent's scan and usage lookup

Times are wall-clock milliseconds per process: median, 90th percentile and minimum.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

INSTALLED = Path(os.path.expandvars(r'%LOCALAPPDATA%\CLI-MODE\claude-marketplace\plugins\cli-mode'))
REAL_DATA = Path.home() / '.claude' / 'plugins' / 'data' / 'cli-mode-cli-mode'
PYTHON = shutil.which('python') or sys.executable  # What the exec-form hook command resolves to.


def timed(argv, stdin='', env=None, repeat=15):
    samples, output = [], ''
    for _ in range(repeat):
        started = time.perf_counter()
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding='utf-8', env=env)
        samples.append((time.perf_counter() - started) * 1000)
        output = done.stdout
        if done.returncode not in (0, 1):
            raise RuntimeError(' '.join(argv[:3]) + ' failed: ' + done.stderr[-400:])
    samples.sort()
    return dict(median=round(statistics.median(samples), 1), p90=round(samples[int(len(samples) * .9) - 1], 1),
                min=round(samples[0], 1), output=output)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--plugin', type=Path, default=INSTALLED, help='Plugin folder to measure (default: installed).')
    parser.add_argument('--repeat', type=int, default=15)
    parser.add_argument('--output', type=Path, help='Also write the results here as JSON.')
    parser.add_argument('--agents', action='store_true',
                        help='Also time each agent\'s installation scan and usage lookup (local CLIs, no prompts).')
    args = parser.parse_args()
    plugin = args.plugin.resolve()
    hook = [PYTHON, str(plugin / 'hooks' / 'claude.py')]
    work = Path(tempfile.mkdtemp(prefix='cli-mode-perf-'))
    data, project = work / 'data', work / 'project'
    project.mkdir()
    data.mkdir()
    if (REAL_DATA / 'onboarding').is_dir():  # A returning user's setup receipts (copied, never modified).
        shutil.copytree(REAL_DATA / 'onboarding', data / 'onboarding')
    env = {key: value for key, value in os.environ.items() if not key.startswith(('CLI_MODE_', 'CLAUDE_'))}
    env.update(CLAUDE_PLUGIN_DATA=str(data), CLAUDE_PROJECT_DIR=str(project), CLI_MODE_TEST_DISABLE_AUTORUN='1')
    results = {}

    def event(session, name, **fields):
        return json.dumps(dict(session_id=session, cwd=str(project), hook_event_name=name,
                               transcript_path=str(work / 'transcript.jsonl'), **fields))

    def measure(key, what, argv, stdin='', fresh=None):
        """`fresh` makes a new session per run: a session's first event, never a repeat."""
        if fresh:
            samples, output = [], ''
            for _ in range(args.repeat):
                one = timed(argv, fresh(), env, repeat=1)
                samples.append(one['median'])
                output = one['output']
            samples.sort()
            result = dict(median=round(statistics.median(samples), 1), p90=round(samples[int(len(samples) * .9) - 1], 1),
                          min=round(samples[0], 1), output=output)
        else:
            result = timed(argv, stdin, env, args.repeat)
        result.update(what=what, outputChars=len(result.pop('output')))
        results[key] = result
        print('%-34s %7.1f %7.1f %7.1f  %s' % (key, result['median'], result['p90'], result['min'], what), flush=True)

    print('%-34s %7s %7s %7s' % ('touchpoint (ms)', 'median', 'p90', 'min'))
    measure('python.bare', 'Interpreter start alone (floor for every hook)', [PYTHON, '-c', 'pass'])
    # Global: runs in every Claude Code session, CLI-MODE in use or not.
    new = lambda name, **fields: (lambda: event(uuid.uuid4().hex, name, **fields))
    measure('global.session-start', 'SessionStart, any session', hook, fresh=new('SessionStart', source='startup'))
    measure('global.prompt.fresh', 'An ordinary prompt, CLI-MODE never used here', hook,
            fresh=new('UserPromptSubmit', prompt='How do I read a file in Python?'))
    measure('global.python-command', 'PreToolUse: an unrelated `python` command', hook,
            event('s-global', 'PreToolUse', tool_name='Bash', tool_input={'command': 'python -m pytest -q'}))
    measure('global.subagent', 'PreToolUse: Agent tool, CLI-MODE never used here', hook,
            fresh=new('PreToolUse', tool_name='Agent', tool_input={}))
    measure('global.stop.fresh', 'Stop (turn end), CLI-MODE never used here', hook, fresh=new('Stop', stop_hook_active=False))
    idle = 's-idle'
    for prompt in ('/cli help', 'x'):  # CLI-MODE was opened once and closed: state exists, nothing active.
        timed(hook, event(idle, 'UserPromptSubmit', prompt=prompt), env, repeat=1)
    measure('global.prompt.used-before', 'An ordinary prompt, CLI-MODE used earlier (inactive)', hook,
            event(idle, 'UserPromptSubmit', prompt='What does this function do?'))
    measure('global.stop.used-before', 'Stop, CLI-MODE used earlier (inactive)', hook, event(idle, 'Stop', stop_hook_active=False))

    # CLI-MODE: an active session (fake agent, no worker), shown the default way (chat).
    os.environ['CLI_MODE_TEST_PLUGIN'] = str(plugin)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_controller import FakeBackend  # noqa: E402  (imports the measured plugin's own scripts)
    from controller import Controller  # noqa: E402
    from state import Store  # noqa: E402
    active = 's-active'
    control = Controller(Store(active, project, data), FakeBackend())
    os.environ['CLAUDE_PLUGIN_DATA'] = str(data)  # Activation checks the host's data folder.
    control.frontend()
    control.activate('gemini-3.8-flash-high', 'allow')
    for key, prompt, what in (('cli.help', '/cli help', 'Help card'),
                              ('cli.menu', '/cli menu', 'Agent Settings of the active agent'),
                              ('cli.queue', '/cli queue', 'Queue'),
                              ('cli.d', '/d add a docstring to main()', 'A /d task: captured, relay context')):
        measure(key, what, hook, event(active, 'UserPromptSubmit', prompt=prompt))
        if key in ('cli.help', 'cli.menu'):
            timed(hook, event(active, 'UserPromptSubmit', prompt='x'), env, repeat=1)  # Close it again.
    state = json.loads((data / 'sessions' / (hashlib.sha256(active.encode()).hexdigest() + '.json')).read_text('utf-8'))
    if not state.get('active') or not state['turnRoute'].get('requestId'):
        raise RuntimeError('The session did not stay active; the timings above are not of CLI-MODE turns.')
    request = state['turnRoute']['requestId']
    controller = (plugin / 'scripts' / 'controller.py').as_posix()
    words = ['--host', 'claude-code', '--thread', active, '--workspace', project.resolve().as_posix(),
             '--data-root', data.as_posix()]
    relay = 'python ' + ' '.join([controller] + words + ['relay', '--request', request])
    measure('cli.approve-relay', 'PreToolUse: approving a relay command', hook,
            event(active, 'PreToolUse', tool_name='PowerShell', tool_input={'command': relay}))
    measure('cli.stop.relaying', 'Stop during a relay turn (the relay guard)', hook,
            event(active, 'Stop', stop_hook_active=False))
    measure('cli.relay-call', 'Controller process: one relay call, nothing new yet', [PYTHON, controller] + words +
            ['relay', '--request', request, '--wait', '0'])
    measure('cli.queue-call', 'Controller process: queue', [PYTHON, controller] + words + ['queue'])
    # Last: X on the agent list stops CLI-MODE, so nothing active follows it.
    measure('cli.home', 'Agent list (chat display)', hook, event(active, 'UserPromptSubmit', prompt='/cli'))

    if args.agents:
        # Per agent: steps a user waits on that run the agent's CLI locally but never prompt a model.
        # The installation scan runs for a first-time check or R; the usage lookup fills the activation card.
        import adapters  # noqa: E402  (the measured plugin's, imported above through test_controller)
        import confirmation  # noqa: E402
        import frontends  # noqa: E402
        print('\n%-11s %10s %10s  %s' % ('agent (ms)', 'scan', 'usage', 'usage result'))
        for agent in adapters.implemented():
            began = time.perf_counter()
            scan = frontends.installation_check(agent)
            scanned = (time.perf_counter() - began) * 1000
            began = time.perf_counter()
            usage = confirmation.usage(agent, {'model': adapters.module(agent).DEFAULTS['model']}, str(project))
            looked = (time.perf_counter() - began) * 1000
            results['agent.' + agent] = dict(scan=round(scanned), usage=round(looked), installed=scan.get('confirmed'),
                                             usageStatus=usage.get('status', 'ok'), quota=adapters.descriptor(agent)['quota'])
            print('%-11s %10.0f %10.0f  %s' % (agent, scanned, looked, usage.get('status', 'ok') + (
                '' if adapters.descriptor(agent)['quota'] == 'account' else ' (no account quota to look up)')))

    summary = dict(plugin=str(plugin), python=PYTHON, repeat=args.repeat, results=results)
    if args.output:
        args.output.write_text(json.dumps(summary, indent=1), encoding='utf-8')
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
