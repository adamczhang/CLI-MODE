"""Read native Claude Code quota metadata; never report mode activation from this helper.

Uses the native read-only /usage local command, not an agent prompt through ACPX.
The CLI reports it as a zero-turn local command, so no model request is made and
no quota is spent. `--output-format stream-json` is required: the plain JSON
envelope carries only rendered markdown, while the stream carries the structured
`usage_report` with percent-used values and ISO reset timestamps.
"""
import argparse
import datetime as dt
import json
import math
import shutil
import subprocess

# Window keys the CLI reports, mapped to the labels the activation banner uses.
WINDOWS = {
    'session': 'Five hour',
    'weekly_all': 'Weekly',
    'weekly_opus': 'Weekly (Opus)',
    'weekly_sonnet': 'Weekly (Sonnet)',
}


def window_label(limit):
    kind = limit.get('kind')
    if kind in WINDOWS:
        return WINDOWS[kind]
    scope = limit.get('scope')
    if limit.get('group') == 'weekly':
        if isinstance(scope, dict):
            labels = []
            for field in ('model', 'surface'):
                value = scope.get(field)
                label = value.get('display_name') if isinstance(value, dict) else value
                if isinstance(label, str) and label.strip() and not any(c in label for c in '\r\n'):
                    labels.append(label.strip())
            scope = ', '.join(labels) or ('scoped' if any(scope.values()) else None)
        return 'Weekly (' + str(scope) + ')' if scope else 'Weekly'
    return str(kind or 'unknown')


def countdown(reset_at, now):
    if not isinstance(reset_at, str):
        return 'unavailable'
    try:
        reset = dt.datetime.fromisoformat(reset_at.replace('Z', '+00:00'))
    except ValueError:
        return 'unavailable'
    if reset.tzinfo is None:
        return 'unavailable'  # A naive timestamp cannot be compared honestly.
    seconds = (reset - now).total_seconds()
    if seconds <= 0:
        return 'due now; refresh required'
    days, hours = divmod(int(seconds // 3600), 24)
    text = str(days) + ' days ' + str(hours) + ' hours'
    return text + ' (less than 1 hour)' if seconds < 3600 else text


def summarize(payload, now):
    """Accept only a verified zero-turn local /usage response."""
    if not isinstance(payload, dict):
        raise ValueError('Malformed Claude usage payload')
    report = payload.get('usage_report')
    if payload.get('local_command_run', {}).get('command') not in ('usage', 'cost'):
        raise ValueError('Claude did not handle /usage as a local command')
    if not isinstance(report, dict):
        raise ValueError('Claude did not return structured usage metadata')
    rate_limits = report.get('rate_limits')
    if not isinstance(rate_limits, dict):
        raise ValueError('No subscription rate limits were reported')
    limits = rate_limits.get('limits')
    if not isinstance(limits, list) or not limits:
        raise ValueError('No quota windows returned')
    summaries = []
    future = []
    for limit in limits:
        if not isinstance(limit, dict):
            raise ValueError('Malformed quota window')
        percent = limit.get('percent')
        valid = (isinstance(percent, (int, float)) and not isinstance(percent, bool)
                 and math.isfinite(percent) and 0 <= percent <= 100)
        reset_at = limit.get('resets_at')
        remaining = countdown(reset_at, now)
        summaries.append({
            'window': window_label(limit),
            # The CLI already reports percent used; no conversion is applied.
            'utilization': (f'{percent:g}% used' if valid else 'unavailable'),
            'resetRemaining': remaining,
            'resetAt': reset_at if isinstance(reset_at, str) else None,
            'severity': limit.get('severity'),
        })
        if remaining not in ('unavailable', 'due now; refresh required'):
            future.append((reset_at, window_label(limit), remaining))
    extra = rate_limits.get('extra_usage')
    next_reset = min(future, default=None)
    return {
        'source': 'Claude Code /usage (signed-in CLI account; zero-turn local command)',
        'observedAt': now.isoformat(),
        'windows': summaries,
        'extraUsage': extra if isinstance(extra, dict) and extra.get('is_enabled') else None,
        'nextReset': None if next_reset is None else {
            'window': next_reset[1], 'remaining': next_reset[2], 'at': next_reset[0]},
    }


def account():
    """Structured attribution. An API key outranks a subscription and pays for turns."""
    binary = shutil.which('claude')
    if not binary:
        return None
    try:
        result = subprocess.run([binary, 'auth', 'status', '--json'],
            capture_output=True, text=True, encoding='utf-8', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            return None
        status = json.loads(result.stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None
    return {
        'loggedIn': status.get('loggedIn'),
        'authMethod': status.get('authMethod'),
        'subscriptionType': status.get('subscriptionType'),
        'apiKeySource': status.get('apiKeySource'),
        'billedTo': ('api-key' if status.get('apiKeySource') else
                     'subscription' if status.get('subscriptionType') else 'unknown'),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', help='Accepted Claude model ID, recorded for context')
    args = parser.parse_args()
    try:
        binary = shutil.which('claude')
        if not binary:
            raise ValueError('Native Claude Code CLI is unavailable for quota lookup')
        # A literal argv item: never a shell string. A POSIX-style shell on
        # Windows can rewrite a leading-slash argument into a path, which would
        # turn this zero-turn command into a billed prompt.
        result = subprocess.run(
            [binary, '-p', '/usage', '--output-format', 'stream-json', '--verbose'],
            capture_output=True, text=True, encoding='utf-8', timeout=90,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('Native Claude quota query failed; check CLI login separately')
        payload = None
        turns = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith('{'):
                continue
            event = json.loads(line)
            if event.get('type') == 'assistant' and 'usage_report' in event:
                payload = event
            if event.get('type') == 'result':
                turns = event.get('num_turns')
        if turns not in (0, None):
            raise ValueError('Claude did not treat /usage as a zero-turn local command')
        if payload is None:
            raise ValueError('Claude returned no structured usage report')
        summary = summarize(payload, dt.datetime.now(dt.timezone.utc))
        summary['model'] = args.model
        summary['account'] = account()
        print(json.dumps(summary, indent=2))
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'unavailable', 'reason': str(exc)}))


if __name__ == '__main__':
    main()
