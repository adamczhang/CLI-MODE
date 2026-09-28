"""The `adambench` task set for checks/claude_usage_live.py (a reference copy: it lives beside Adam Bench, which
holds the answers, and is meant to be copied into CLI-MODE's checks/ as is). It only shells out to
`adambench.py`, so nothing of the bench is imported into, or published with, CLI-MODE.

Sets pin their bench version, so results stay comparable when a new version is released, and pass the same run
mode to `prompt` and to `grade` (the grade then says whether the hunt followed the mode's stop rule):
  A10, A10G5         adam-10@1: its default prompt, and "do not stop until at least 5 are found"
  B10                adam-10@2 open: unknown count, stops when it reports completion (A10's prompt, word for word)
  B10T20             adam-10@2 timed: unknown count, keep hunting for 20 minutes. The harness must enforce the
                     budget (end the run at 20 minutes); the grade measures from the seed commit to grading.
  B10K               adam-10@2 known: told there are 10, find all of them
  B10G5              adam-10@2 goal: at least 5
v1 and v2 plant the same bugs in byte-identical seeds, so every set's `fixed`/`bugs`/`byTier` compare directly.
ADAMBENCH_PROMPT (a template file) overrides the prompt for one run, with ADAMBENCH_GOAL for its `{goal}`.

Wiring, as for bughunt: `import claude_usage_adambench as adambench`, then in claude_usage_live.py
SETS['adambench'] = (adambench.seed, adambench.tasks), and in check(): `if name in adambench.NAMES: return
adambench.check(name, workspace, reply, answers)`."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ADAMBENCH = Path(os.environ.get('ADAMBENCH') or Path.home() / 'Desktop' / 'Claude' / 'Adam Bench')
SETS = {  # set name -> (bench, mode arguments for both prompt and grade)
    'A10': ('adam-10@1', []),
    'A10G5': ('adam-10@1', ['--variant', 'goal', '--goal', '5']),
    'B10': ('adam-10@2', ['--mode', 'open']),
    'B10T20': ('adam-10@2', ['--mode', 'timed', '--minutes', '20']),
    'B10K': ('adam-10@2', ['--mode', 'known']),
    'B10G5': ('adam-10@2', ['--mode', 'goal', '--goal', '5']),
}
NAMES = tuple(SETS)


def cli(*args, timeout=900):
    result = subprocess.run([sys.executable, str(ADAMBENCH / 'adambench.py'), *args], cwd=ADAMBENCH,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'adambench.py {" ".join(args)} failed ({result.returncode}): {result.stderr[-2000:]}')
    return result.stdout


def seed(workspace, name):
    """The seeded project in a fresh one-commit repository, node_modules linked in."""
    cli('seed', SETS[name][0], str(workspace), '--json')


def prompt(name):
    bench, args = SETS[name]
    template = os.environ.get('ADAMBENCH_PROMPT')
    if template:
        goal = os.environ.get('ADAMBENCH_GOAL')
        args = ['--template', template, *(['--goal', goal] if goal else [])]
    return cli('prompt', bench, *args).rstrip('\n')


def tasks():
    return {name: (prompt(name), {}) for name in NAMES}


def keep(workspace, stem):
    """The run's evidence beside its results: every change against the seed as one patch, and BUGS_FOUND.md."""
    workspace, stem = Path(workspace), Path(str(stem))
    subprocess.run(['git', 'add', '-A'], cwd=workspace, capture_output=True)
    diff = subprocess.run(['git', 'diff', '--cached', '--no-color'], cwd=workspace, capture_output=True).stdout
    stem.with_suffix('.diff').write_bytes(diff)
    report = workspace / 'BUGS_FOUND.md'
    if report.exists():
        shutil.copy2(report, stem.with_name(stem.name + '-BUGS_FOUND.md'))


def check(name, workspace, reply, answers):
    bench, mode_args = SETS[name]
    result = json.loads(cli('grade', bench, str(workspace), *mode_args, '--json'))
    mode = result['mode']
    healthy = result['typecheck'] and result['visibleSuite']
    tests = dict(ok=healthy, passed=result['fixed'], failed=result['of'] - result['fixed'],
                 summary=f"fixed {result['fixed']}/{result['of']} ({', '.join(f'{t} {v}' for t, v in result['byTier'].items())}), "
                         f"typecheck {'clean' if result['typecheck'] else 'FAILED'}, "
                         f"visible suite {'green' if result['visibleSuite'] else 'FAILED'}"
                         + ('' if result['pristineSuite'] else ', a visible test was weakened')
                         + f", {mode['name']} mode: stop rule "
                         + {True: 'followed', False: 'NOT followed', None: 'unknown'}[mode['followedStopRule']])
    checks = {'tests pass': healthy, 'typecheck': result['typecheck'], 'visible suite': result['visibleSuite'],
              'pristine suite': result['pristineSuite'], 'hidden': f"{result['fixed']}/{result['of']}",
              'by tier': result['byTier'], 'bugs': result['bugs'], 'claimed entries': result['claimedEntries'],
              'report': result['report'], 'bench': result['bench'], 'mode': mode,
              'timing': result['timing'], 'changes': result['changes']}
    return tests, checks
