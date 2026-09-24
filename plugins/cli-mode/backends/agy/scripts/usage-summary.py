"""Read native AGY quota metadata; never report mode activation from this helper."""
import argparse
import datetime as dt
import json
import math
import shutil
import subprocess


def summarize(payload, model_id, now):
    if not isinstance(payload, dict):
        raise ValueError('Malformed AGY usage payload')
    command = payload.get('command')
    if payload.get('status') != 'SUCCESS' or not isinstance(command, dict) or command.get('name') != 'usage':
        raise ValueError('AGY did not return native usage metadata')
    if not model_id.startswith('gemini-'):
        raise ValueError('No verified quota-group mapping for this model')
    data = command.get('data')
    groups = data.get('groups') if isinstance(data, dict) else None
    if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
        raise ValueError('Malformed quota groups')
    matches = [group for group in groups if group.get('name') == 'Gemini Models']
    if len(matches) != 1:
        raise ValueError('Gemini quota group missing or ambiguous')
    summaries = []
    future_resets = []
    buckets = matches[0].get('buckets')
    if not isinstance(buckets, list) or any(not isinstance(bucket, dict) for bucket in buckets):
        raise ValueError('Malformed quota windows')
    for bucket in buckets:
        remaining = bucket.get('remaining_fraction')
        valid = isinstance(remaining, (int, float)) and not isinstance(remaining, bool)
        valid = valid and math.isfinite(remaining) and 0 <= remaining <= 1
        used_text = f'{(1 - remaining) * 100:.1f}% used' if valid else 'unavailable'
        reset_text = 'unavailable'
        reset_at = bucket.get('reset_time')
        if isinstance(reset_at, str):
            try:
                reset = dt.datetime.fromisoformat(reset_at.replace('Z', '+00:00'))
                if reset.tzinfo is None:
                    raise ValueError('Reset timestamp has no timezone')
                seconds = (reset - now).total_seconds()
                if seconds <= 0:
                    reset_text = 'due now; refresh required'
                else:
                    days, hours = divmod(int(seconds // 3600), 24)
                    reset_text = f'{days} days {hours} hours'
                    if seconds < 3600:
                        reset_text += ' (less than 1 hour)'
                    future_resets.append((reset, bucket.get('window', 'unknown'), reset_text))
            except ValueError:
                pass
        summaries.append({
            'window': bucket.get('window', 'unknown'),
            'utilization': used_text,
            'resetRemaining': reset_text,
            'resetAt': reset_at,
        })
    if not summaries:
        raise ValueError('No quota windows returned')
    next_reset = min(future_resets, default=None)
    return {
        'source': 'Antigravity CLI /usage (CLI account; verify it matches the ACP profile)',
        'observedAt': now.isoformat(),
        'group': matches[0]['name'],
        'windows': summaries,
        'nextReset': None if next_reset is None else {
            'window': next_reset[1], 'remaining': next_reset[2], 'at': next_reset[0].isoformat()
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, help='Accepted Antigravity model ID')
    args = parser.parse_args()
    try:
        binary = shutil.which('agy')
        if not binary:
            raise ValueError('Native AGY CLI is unavailable for quota lookup')
        # A working lookup takes about 6 s. AGY 1.2.9's /usage never answers (its own print timeout included),
        # and the activation card waits for this, so the wait is bounded well below the old 40 s.
        result = subprocess.run(
            [binary, '--output-format', 'json', '--print-timeout', '12s', '-p', '/usage'],
            capture_output=True, text=True, encoding='utf-8', timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode:
            raise ValueError('Native AGY quota query failed; check CLI login separately')
        payload = json.loads(result.stdout)
        summary = summarize(payload, args.model, dt.datetime.now(dt.timezone.utc))
        print(json.dumps(summary, indent=2))
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'unavailable', 'reason': str(exc)}))


if __name__ == '__main__':
    main()
