"""The test gate: the project's own test command, run by CLI-MODE after each agent turn that changed files.

An agent saying its tests pass is not a check. After a turn that changed project files, CLI-MODE runs the
command set with `/cli test <command>` in the project folder, as you would in a terminal, and the answer's
receipt says whether it passed (exit code 0) with the runner's own summary line. The full output is kept as a
log in the agent's working folder. Runs are one at a time per project, so agents working at once never run
the suite over each other, and each has a time limit. The command is per project folder, for every
conversation on this host.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

TIMEOUT = 600
SETTINGS = 'project-tests.json'
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The summary lines of common runners, most specific first; otherwise the output's last line.
SUMMARIES = (re.compile(r'=+ (.*\b(?:passed|failed|error)\b.*) =+'),  # pytest
             re.compile(r'^Tests?:\s+(.+)$', re.M),                  # Jest, Vitest
             re.compile(r'^(test result: .+)$', re.M),               # cargo test
             re.compile(r'^((?:ok|FAIL)\s+\S+.*)$', re.M))           # go test


def key(workspace):
    return os.path.normcase(str(Path(workspace).resolve()))


OFF = 'off'
NPM_PLACEHOLDER = 'no test specified'  # `npm init`'s test script, which only fails.


def detect(workspace):
    """The project's usual test command from its files, as (command, source), or (None, None).

    package.json with a real test script: npm test; pytest.ini, pyproject.toml with pytest settings, or a tests
    folder of test_*.py files: python -m pytest; Cargo.toml: cargo test; go.mod: go test ./... A folder with none
    of these (research, art, docs) has no tests, so nothing runs there.
    """
    root = Path(workspace)
    try:
        script = ((json.loads((root / 'package.json').read_text(encoding='utf-8')).get('scripts') or {})
                  .get('test') if (root / 'package.json').is_file() else None)
    except (OSError, ValueError, AttributeError):
        script = None
    if isinstance(script, str) and script.strip() and NPM_PLACEHOLDER not in script:
        return 'npm test', 'package.json'
    try:
        pyproject = (root / 'pyproject.toml').read_text(encoding='utf-8') if (root / 'pyproject.toml').is_file() else ''
    except OSError:
        pyproject = ''
    if (root / 'pytest.ini').is_file() or '[tool.pytest' in pyproject or any((root / 'tests').glob('test_*.py')):
        return 'python -m pytest', 'pytest.ini' if (root / 'pytest.ini').is_file() else (
            'pyproject.toml' if '[tool.pytest' in pyproject else 'tests/')
    if (root / 'Cargo.toml').is_file():
        return 'cargo test', 'Cargo.toml'
    if (root / 'go.mod').is_file():
        return 'go test ./...', 'go.mod'
    return None, None


def saved(root, workspace):
    """What `/cli test` saved for this project: a command, OFF, or None (then the command is detected)."""
    try:
        return json.loads((Path(root) / SETTINGS).read_text(encoding='utf-8')).get(key(workspace))
    except (OSError, ValueError, AttributeError):
        return None


def command(root, workspace):
    """The test command for this project: the one set with /cli test, else the detected one; None when off."""
    value = saved(root, workspace)
    if value == OFF:
        return None
    return value or detect(workspace)[0]


def set_command(root, workspace, text):
    """Save this project's test command, or OFF, or with None forget it (detection applies again)."""
    path = Path(root) / SETTINGS
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    if text:
        saved[key(workspace)] = text
    else:
        saved.pop(key(workspace), None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=1), encoding='utf-8')


def summary(output):
    """The runner's own result line, or the output's last line."""
    for pattern in SUMMARIES:
        found = pattern.findall(output)
        if found:
            return found[-1].strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ''


def run(root, workspace, text, log, timeout=TIMEOUT):
    """Run the command in the project, one run per project at a time; the result for the receipt."""
    lock = Path(root) / ('tests-' + hashlib.sha1(key(workspace).encode()).hexdigest()[:12] + '.lock')
    started = time.monotonic()
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > timeout + 60:
                    lock.unlink()  # Left by a run that was killed.
                    continue
            except OSError:
                continue
            if time.monotonic() - started > timeout:
                return dict(command=text, passed=False, summary='another test run held the project too long',
                            seconds=0, log=None)
            time.sleep(.5)
    try:
        began = time.monotonic()
        try:
            done = subprocess.run(text, shell=True, cwd=str(workspace), capture_output=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
            output = (done.stdout + b'\n' + done.stderr).decode('utf-8', errors='replace')
            passed, line = done.returncode == 0, None
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or b'') + b'\n' + (exc.stderr or b'')).decode('utf-8', errors='replace')
            passed, line = False, 'stopped after ' + str(timeout // 60) + ' minutes'
        except OSError as exc:
            output, passed, line = str(exc), False, 'could not run: ' + str(exc)
        seconds = round(time.monotonic() - began)
        kept = None
        if log is not None:
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text('$ ' + text + '\n\n' + output, encoding='utf-8')
                kept = log.relative_to(workspace).as_posix()
            except (OSError, ValueError):
                pass
        return dict(command=text, passed=passed, summary=line or summary(output), seconds=seconds, log=kept)
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)


def line(tests):
    """`✓ Tests passed (npm test) · 41 passed · 12 s`, or `✗ Tests failed (...)`, for a receipt."""
    if not tests:
        return None
    return (('✓ Tests passed (' if tests['passed'] else '✗ Tests failed (') + tests['command'] + ')' +
            (' · ' + tests['summary'] if tests.get('summary') else '') + ' · ' +
            str(tests.get('seconds', 0)) + ' s')


def overlap_line(overlaps):
    """`⚠ Also edited by Codex COD-AC while this turn ran: app.py, api.py`, one line per other agent."""
    agents = {}
    for item in overlaps or []:
        agents.setdefault(item['agent'], []).append(item['path'])
    return ['⚠ Also edited by ' + agent + ' while this turn ran: ' + ', '.join(paths)
            for agent, paths in agents.items()]
