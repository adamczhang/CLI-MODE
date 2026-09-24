"""Explicit native sign-in verification, with no task prompt or stored output."""
import json
import shutil
import subprocess
import sys


def verify_native():
    binary = shutil.which('agy')
    if not binary:
        raise RuntimeError('Antigravity CLI is not available.')
    result = subprocess.run([binary, '--output-format', 'json', '--print-timeout', '10m',
        '-p', '/usage'], capture_output=True, text=True, encoding='utf-8', timeout=660)
    try:
        data = json.loads(result.stdout)
        valid = (result.returncode == 0 and data['status'] == 'SUCCESS' and
                 data['num_turns'] == 0 and data['command']['name'] == 'usage')
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise RuntimeError('Native sign-in not verified. Run agy interactively, sign in, then rerun /cli.')
    print('Native sign-in verified without an agent task.')


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['--native']:
            raise RuntimeError('Explicit --native is required.')
        verify_native()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
