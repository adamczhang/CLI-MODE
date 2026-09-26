"""Codex CLI's plan limits, read the way Codex Desktop reads them: `account/rateLimits/read` on a short-lived
`codex app-server`. No thread is started and no model request is made, so no quota is spent.

Prints the shared usage summary: {"status": "ok", "windows": [{"window", "utilization", "resetRemaining"}]}, or
{"status": "unavailable", "reason": ...}.
"""
import argparse
import datetime as dt
import json
import queue
import shutil
import subprocess
import threading

NAMES = {300: 'Five hour', 10080: 'Weekly'}


def countdown(seconds):
    if seconds <= 0:
        return 'due now; refresh required'
    days, hours = divmod(int(seconds // 3600), 24)
    text = str(days) + ' days ' + str(hours) + ' hours'
    return text + ' (less than 1 hour)' if seconds < 3600 else text


def window(limit, now):
    minutes = limit.get('windowDurationMins')
    name = NAMES.get(minutes) or (str(minutes // 60) + ' hour' if isinstance(minutes, int) else 'Limit')
    used = limit.get('usedPercent')
    resets = limit.get('resetsAt')
    return dict(window=name, utilization=str(round(used)) + '% used' if isinstance(used, (int, float)) else 'unknown',
                resetRemaining=countdown(resets - now) if isinstance(resets, (int, float)) else 'unavailable')


def rate_limits(timeout=40):
    """One app-server, three messages: initialize, initialized, account/rateLimits/read."""
    codex = shutil.which('codex')
    if not codex:
        raise RuntimeError('Codex CLI is not on PATH')
    process = subprocess.Popen([codex, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(dict(jsonrpc='2.0', **message)) + '\n')
        process.stdin.flush()

    def reply(ident):
        while True:
            message = json.loads(lines.get(timeout=timeout))
            if message.get('id') == ident and 'method' not in message:
                if 'error' in message:
                    raise RuntimeError('Codex: ' + json.dumps(message['error'])[:200])
                return message.get('result') or {}
    try:
        send(dict(id=1, method='initialize', params=dict(clientInfo=dict(name='cli-mode-usage', version='1'))))
        reply(1)
        send(dict(method='initialized', params={}))
        send(dict(id=2, method='account/rateLimits/read', params={}))
        return reply(2)
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--session')
    parser.parse_args()
    try:
        limits = (rate_limits().get('rateLimits') or {})
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        windows = [window(limits[key], now) for key in ('primary', 'secondary') if isinstance(limits.get(key), dict)]
        if not windows:
            raise ValueError('Codex reported no plan limits')
        print(json.dumps(dict(status='ok', windows=windows, plan=limits.get('planType'))))
    except (OSError, ValueError, RuntimeError, queue.Empty) as exc:
        print(json.dumps(dict(status='unavailable', reason=str(exc) or 'Codex usage lookup failed')))


if __name__ == '__main__':
    main()
