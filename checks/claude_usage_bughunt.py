"""The `bughunt` task set for checks/claude_usage_live.py: a Bug Hunt Bench-style hunt (bughunt.productcompass.pm)
in a real project. Bugs are planted in a copy of a public codebase: real fixes reversed from their own commits, and
changes written in the same style. The project's own tests that would catch them are taken out of the visible suite,
so it stays green, and are kept as hidden tests that decide the score. The prompt follows the bench's own.

The replica is built outside this repository, because it holds the answers (BUGHUNT_BENCH, default below: build.py,
grade.py and one folder per set). H3 is the pilot (3 bugs), H5 a mid-size hunt (5 bugs), H20 the full hunt (20)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BENCH = Path(os.environ.get('BUGHUNT_BENCH') or Path.home() / 'Desktop' / 'Claude' / 'OpenMausBot-bench')
UPSTREAM = Path(os.environ.get('OMB_UPSTREAM') or Path.home() / 'Desktop' / 'Claude' / 'OpenMausBot')
SETS = {'H3': 'pilot', 'H5': 'five', 'H20': 'full'}
NAMES = tuple(SETS)
IGNORED = ('node_modules', 'Agent_Working_Folder/', '*.hidden.test.ts')


def manifest(name):
    return json.loads((BENCH / SETS[name] / 'manifest.json').read_text(encoding='utf-8'))


def hunt_file(name):
    listing = '\n'.join('- `' + module + '`' for module in manifest(name)['modules'])
    return ('# The hunt\n\nThe planted bugs are in these server modules, and nowhere else:\n\n' + listing +
            '\n\nTheir unit tests run with `pnpm test:hunt`.\n')


def seeder(name):
    def seed(workspace):
        """The seeded project in a fresh repository (no history), with the upstream's installed packages linked in."""
        workspace = Path(workspace)
        shutil.copytree(BENCH / SETS[name] / 'seed', workspace, dirs_exist_ok=True)
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(workspace / 'node_modules'), str(UPSTREAM / 'node_modules')],
                       check=True, capture_output=True)
        package = workspace / 'package.json'
        data = json.loads(package.read_text(encoding='utf-8'))
        data['scripts']['test:hunt'] = 'vitest run ' + ' '.join(manifest(name)['tests'])
        # The project's own `test` runs the whole monorepo (over 10 minutes on Windows): CLI-MODE's test gate would
        # time out on it after every handoff. The hunt suite is the test command for this project.
        data['scripts']['test'] = data['scripts']['test:hunt']
        data['scripts']['typecheck:server'] = 'tsc -p tsconfig.server.json --noEmit'
        package.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        (workspace / 'HUNT.md').write_text(hunt_file(name), encoding='utf-8')
        ignore = workspace / '.gitignore'
        old = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
        ignore.write_text(old.rstrip('\n') + '\n' + '\n'.join(IGNORED) + '\n', encoding='utf-8')
        for command in (['init', '-q'], ['add', '-A'],
                        ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Seed']):
            subprocess.run(['git', *command], cwd=workspace, check=True, capture_output=True)
    return seed


def seed(workspace, name):
    return seeder(name)(workspace)


PROMPT = (
    'This repository is OpenMausBot, a local-first chat app for running a team of AI agents: a TypeScript server '
    'under `server/` (Node 24), with a React client, an Electron shell and phone apps beside it. A number of subtle '
    'bugs have been deliberately planted in the server modules listed in `HUNT.md`. The server typechecks '
    '(`pnpm typecheck:server`) and their unit tests pass (`pnpm test:hunt`), so the planted bugs are runtime '
    'logic/behavior defects, not crashes or syntax errors. Be aware that some tests that would have caught a planted '
    'bug were neutralized when the bug was planted - a green suite does not prove a file is clean.\n\n'
    'Your task:\n\n'
    '1. Find and fix as many of the planted bugs as you can. Edit the source files in place.\n'
    '2. Keep the repo healthy: `pnpm typecheck:server` must stay clean and `pnpm test:hunt` must still pass after '
    'your fixes. Do not weaken, skip, or delete existing tests to make them pass - fix the source. Adding new tests '
    'is allowed but not required.\n'
    '3. When you are done, write a report to `BUGS_FOUND.md` at the repository root: one numbered entry per bug you '
    'fixed, each with the file and approximate line, the symptom a user would see, the root cause, and the exact fix '
    'you made. Add a final section titled "Suspected but not fixed" for anything you flagged but chose to leave '
    'alone.\n\n'
    'Notes:\n'
    '- `AGENTS.md` documents the project and its conventions.\n'
    '- There is no git history and no network access - work only from the code in this repository.\n'
    '- Prioritize genuine planted defects over style opinions; do not refactor beyond what a fix requires.\n'
    '- Work without asking me questions. Do not commit.')


def tasks():
    return {name: (PROMPT, {}) for name in NAMES}


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
    sys.path.insert(0, str(BENCH))
    import grade
    result = grade.grade(SETS[name], workspace)
    healthy = result['typecheck'] and result['visibleSuite']
    tests = dict(ok=healthy, passed=result['fixed'], failed=result['of'] - result['fixed'],
                 summary=f"fixed {result['fixed']}/{result['of']}, typecheck " +
                         ('clean' if result['typecheck'] else 'FAILED') + ', visible suite ' +
                         ('green' if result['visibleSuite'] else 'FAILED'))
    checks = {'tests pass': healthy, 'typecheck': result['typecheck'], 'visible suite': result['visibleSuite'],
              'hidden': f"{result['fixed']}/{result['of']}", 'bugs': result['bugs'],
              'claimed entries': result['claimedEntries'], 'report': result['report']}
    return tests, checks
