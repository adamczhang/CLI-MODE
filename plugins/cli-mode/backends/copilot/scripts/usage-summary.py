"""GitHub Copilot's monthly request quotas, from the same GitHub endpoint Copilot's own tools read
(`copilot_internal/user`, through the GitHub CLI's sign-in). No model request is made.

The endpoint is GitHub's own and undocumented, so any change or failure reports "unavailable". It is read only when
the GitHub CLI is signed in as the account Copilot CLI uses, so the numbers are never someone else's.

Prints the shared usage summary: {"status": "ok", "windows": [...]} or {"status": "unavailable", "reason": ...}.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess

QUOTAS = (('premium_interactions', 'Premium requests'), ('chat', 'Chat requests'))
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def gh(*args):
    command = shutil.which('gh')
    if not command:
        raise RuntimeError('the GitHub CLI (gh) is not installed, which Copilot usage is read through')
    result = subprocess.run([command, 'api', *args], capture_output=True, text=True, encoding='utf-8',
                            timeout=30, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError('the GitHub CLI could not read it (' + (result.stderr.strip() or 'error')[:120] + ')')
    return json.loads(result.stdout)


def copilot_login():
    """The account Copilot CLI is signed in with (~/.copilot/config.json, JSON with // comment lines)."""
    path = Path.home() / '.copilot' / 'config.json'
    text = '\n'.join(line for line in path.read_text(encoding='utf-8').splitlines()
                     if not line.lstrip().startswith('//'))
    return ((json.loads(text).get('lastLoggedInUser') or {}).get('login') or '').casefold()


def countdown(reset_at, now):
    try:
        seconds = (dt.datetime.fromisoformat(reset_at.replace('Z', '+00:00')) - now).total_seconds()
    except (AttributeError, ValueError, TypeError):
        return 'unavailable'
    if seconds <= 0:
        return 'due now; refresh required'
    days, hours = divmod(int(seconds // 3600), 24)
    return str(days) + ' days ' + str(hours) + ' hours'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--session')
    parser.parse_args()
    try:
        signed_in = copilot_login()
        login = (gh('user').get('login') or '').casefold()
        if not signed_in or login != signed_in:
            raise RuntimeError('the GitHub CLI is signed in as a different account than Copilot CLI')
        user = gh('copilot_internal/user')
        now = dt.datetime.now(dt.timezone.utc)
        reset = countdown(user.get('quota_reset_date_utc'), now)
        windows = []
        for key, name in QUOTAS:
            quota = (user.get('quota_snapshots') or {}).get(key) or {}
            if quota.get('unlimited'):
                windows.append(dict(window=name, utilization='unlimited', resetRemaining=reset))
            elif isinstance(quota.get('entitlement'), (int, float)) and quota['entitlement'] > 0:
                left = quota.get('remaining', 0)
                used = round(100 - float(quota.get('percent_remaining', 100)))
                windows.append(dict(window=name, utilization=str(used) + '% used (' + str(round(left)) + ' of ' +
                                    str(round(quota['entitlement'])) + ' left)', resetRemaining=reset))
        if not windows:
            raise RuntimeError('this Copilot plan has no request quota to report')
        print(json.dumps(dict(status='ok', windows=windows, plan=user.get('access_type_sku'))))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='unavailable', reason=str(exc) or 'Copilot usage lookup failed')))


if __name__ == '__main__':
    main()
