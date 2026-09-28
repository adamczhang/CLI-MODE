"""The `escalation` task set for checks/claude_usage_live.py: a short live check of AUTO's escalation, end to end.

E1 hands a small review to the AUTO agent (run it at a low effort) and has it end with a REMAINING line, so its
result carries an ESCALATE line; the host should then hand that to a fresh agent at the highest effort
(`--agent new --effort max --escalates <task>`), whose result carries a FOLLOW-UP line or nothing. The run's
`parallel.waves` show it: a first wave at the low effort, then an escalation at the highest. Cheap on purpose:
one small module, a few minutes."""
from pathlib import Path
import subprocess
import sys

NAMES = ('E1',)
PARSE = '''"""Settings lines: `key=value`, one a line."""


def parse_pairs(text):
    """The pairs in `text`, in order; a line without `=` is skipped, and a later key replaces an earlier one."""
    pairs = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=')
        pairs[key.strip()] = value.strip()
    return pairs
'''
TESTS = '''from app.parse import parse_pairs


def test_pairs_in_order():
    assert parse_pairs('a=1\\nb=2') == {'a': '1', 'b': '2'}


def test_lines_without_a_pair_are_skipped():
    assert parse_pairs('# comment\\na=1') == {'a': '1'}
'''
PROMPT = ('Hand this whole task to your AUTO agent; do none of it yourself. The task: review app/parse.py for bugs '
          'and fix what you find, keeping `python -m pytest -q` green. It must end its answer with exactly this line: '
          '`REMAINING: app/parse.py: a value that itself contains "=" (as in url=a=b) is unverified; I could not tell '
          'whether split() handles it.` Do not commit.')


def seed(workspace):
    workspace = Path(workspace)
    (workspace / 'app').mkdir()
    (workspace / 'tests').mkdir()
    (workspace / 'app' / '__init__.py').write_text('', encoding='utf-8')
    (workspace / 'app' / 'parse.py').write_text(PARSE, encoding='utf-8')
    (workspace / 'tests' / 'test_parse.py').write_text(TESTS, encoding='utf-8')
    (workspace / 'pytest.ini').write_text('[pytest]\npythonpath = .\n', encoding='utf-8')
    for command in (['init', '-q'], ['add', '-A'],
                    ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Seed']):
        subprocess.run(['git', *command], cwd=workspace, check=True, capture_output=True)


def tasks():
    return {'E1': (PROMPT, {})}


def check(name, workspace, reply, answers, pytest_counts):
    """The project's tests, and whether a value containing '=' now parses (the bug the Max agent should settle)."""
    tests = pytest_counts(workspace)
    probe = subprocess.run([sys.executable, '-c', 'from app.parse import parse_pairs; '
                            'assert parse_pairs("url=a=b") == {"url": "a=b"}'], cwd=workspace, capture_output=True)
    return tests, {'tests pass': tests['ok'], 'equals in a value': probe.returncode == 0}
