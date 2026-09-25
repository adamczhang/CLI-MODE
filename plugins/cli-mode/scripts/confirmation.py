"""One activation template for every provider; usage is native metadata only."""
from pathlib import Path
import json
import os
import subprocess
import sys

import adapters
import host
from presentation import access_display, access_note, effort_display, strong

UNAVAILABLE = 'Usage not available through CLI'


def usage(agent, settings, workspace):
    if adapters.descriptor(agent)['quota'] != 'account':
        return {'status': 'unavailable'}
    helper = Path(__file__).resolve().parents[1] / 'backends' / agent / 'scripts/usage-summary.py'
    try:
        # Run in the same workspace as the provider (including project auth config).
        result = subprocess.run([sys.executable, str(helper), '--model', settings['model']],
            cwd=workspace, capture_output=True, text=True, encoding='utf-8', timeout=130,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            return {'status': 'unavailable', 'reason': 'Native usage helper failed.'}
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {'status': 'unavailable'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'status': 'unavailable', 'reason': 'Native usage lookup unavailable.'}


def utilization(summary):
    if (summary.get('status') == 'unavailable' or summary.get('accountMatched') is False
            or (summary.get('account') or {}).get('billedTo') == 'api-key'):
        return UNAVAILABLE
    windows = summary.get('windows')
    if not isinstance(windows, list) or not windows:
        return UNAVAILABLE
    if not any(isinstance(window, dict) and isinstance(window.get('utilization'), str)
               and '% used' in window['utilization'] for window in windows):
        return UNAVAILABLE
    rows = []
    for window in windows:
        if not isinstance(window, dict):
            return UNAVAILABLE
        label, used, reset = (window.get(key) for key in ('window', 'utilization', 'resetRemaining'))
        if not all(isinstance(value, str) and value and '\n' not in value and '\r' not in value
                   for value in (label, used, reset)):
            return UNAVAILABLE
        label = {'five_hour': 'Five hour', 'five-hour': 'Five hour', '5h': 'Five hour',
                 'weekly': 'Weekly', '7d': 'Weekly'}.get(label, label)
        reset_text = ('reset unavailable' if reset == 'unavailable' else reset
                      if reset.startswith('due now') else 'resets in ' + reset)
        rows.append(label + ': ' + used + ' | ' + reset_text)
    return ('\n' + ' ' * 13).join(rows)


def activation(agent, settings, summary, color=False, name=None):
    adapter = adapters.module(agent)
    access = access_display(settings['access'], settings.get('accessName'))
    # Claude Code's own /help is built in; CLI-MODE's help is /cli help there.
    question = '/cli help' if host.claude() else '/help'
    return (strong('CLI-MODE Activated', color) + '\n\n' +
            ('**Agent:** ' + adapter.LABEL + ' ' + name + ' | ' if name else '') +
            '**Model:** ' + settings['modelName'] + ' | **Effort:** ' + effort_display(settings['effort']) +
            ' | **Access:** ' + access + access_note(settings['access']) + ' | **Question:** `' + question + '`\n\n'
            '**Utilization:** ' + utilization(summary))
