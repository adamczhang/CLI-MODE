"""Public activity validation, coalescing, and deterministic display text."""
import math
import re
import time
import unicodedata

PROGRESS_MODES = ('activity', 'quiet')
DEFAULT_PROGRESS_MODE = 'activity'
KINDS = dict(read='Read', edit='Edit', delete='Delete', move='Move', search='Search',
             execute='Run command', fetch='Fetch', switch_mode='Change mode', other='Tool activity')
STATUSES = dict(pending='Pending', in_progress='Running', completed='Completed', failed='Failed')
TERMINAL = ('completed', 'failed')
BREAKDOWN = dict(inputTokens='input', outputTokens='output', cachedReadTokens='cache read',
                 cachedWriteTokens='cache write', totalTokens='total')
EXECUTION_TITLES = {'Run Python tests (unittest)', 'Run Python tests (pytest)', 'Run npm tests'} | {
    'Run ' + program + ' command' for program in
    ('Python', 'Node.js', 'npm', 'Git', 'PowerShell', 'Bash', 'shell')}


def progress_mode(state):
    value = state.get('progressMode', DEFAULT_PROGRESS_MODE)
    if value not in PROGRESS_MODES:
        raise ValueError('Unsupported progress setting; choose activity or quiet.')
    return value


def clean(value, limit=240):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    return ' '.join(''.join(' ' if unicodedata.category(c) in ('Cc', 'Cf', 'Cs') else c
                           for c in value).split())[:limit]


def count(value):
    return type(value) is int and 0 <= value <= 9007199254740991


def public_progress(event):
    """Revalidate bridge/file data before logging or rendering; allowlist fields."""
    if not isinstance(event, dict):
        return None
    if event.get('type') == 'activity':
        ident, kind, status = (event.get(key) for key in ('toolCallId', 'kind', 'status'))
        if (not isinstance(ident, str) or not 0 < len(ident) <= 200 or not isinstance(kind, str)
                or not isinstance(status, str) or kind not in KINDS or status not in STATUSES):
            return None
        result = dict(type='activity', toolCallId=ident, kind=kind, status=status)
        title = clean(event.get('title')) if kind != 'other' else ''
        if kind == 'execute' and title not in EXECUTION_TITLES:
            title = ''
        if title:
            result['title'] = title
        locations = event.get('locations')
        if isinstance(locations, list):
            result['locations'] = []
            for location in locations[:5]:
                if not isinstance(location, dict) or not clean(location.get('path')):
                    continue
                item = dict(path=clean(location['path']))
                if count(location.get('line')):
                    item['line'] = location['line']
                result['locations'].append(item)
        return result
    if event.get('type') == 'usage':
        result = dict(type='usage')
        for key in ('used', 'size'):
            if count(event.get(key)):
                result[key] = event[key]
        cost = event.get('cost')
        if isinstance(cost, dict):
            amount, currency = cost.get('amount'), cost.get('currency')
            if (type(amount) in (int, float) and 0 <= amount <= 1e308 and math.isfinite(amount)
                    and isinstance(currency, str) and re.fullmatch('[A-Z]{3}', currency)):
                result['cost'] = dict(amount=amount, currency=currency)
        raw = event.get('breakdown')
        if isinstance(raw, dict):
            breakdown = {key: raw[key] for key in BREAKDOWN if count(raw.get(key))}
            if breakdown:
                result['breakdown'] = breakdown
        return result if len(result) > 1 else None
    return None


class ActivityRelay:
    """Emit lifecycle changes promptly, coalesce metadata and usage bursts."""
    def __init__(self, mode=DEFAULT_PROGRESS_MODE):
        self.enabled = mode == 'activity'
        self.tools, self.pending = {}, {}
        self.usage = None
        self.started = None

    def flush(self):
        values = list(self.pending.values())
        self.pending.clear()
        self.started = None
        return values

    def due(self):
        return self.flush() if self.started is not None and time.monotonic() - self.started >= .5 else []

    def feed(self, raw):
        event = public_progress(raw) if self.enabled else None
        if event is None:
            return []
        if event['type'] == 'activity':
            key = event['toolCallId']
            previous = self.tools.get(key)
            if previous == event or (previous and (previous['status'] in TERMINAL and event['status'] != previous['status']
                    or previous['status'] == 'in_progress' and event['status'] == 'pending')):
                return []
            if previous is None and len(self.tools) >= 4096:
                return []
            self.tools[key] = event
            if previous is None or event['status'] != previous['status']:
                self.pending.pop(('activity', key), None)
                return [event]
            key = ('activity', key)
        else:
            if self.usage == event:
                return []
            self.usage = event
            key = ('usage', '')
        self.pending[key] = event
        if self.started is None:
            self.started = time.monotonic()
        return []


def activity_text(event):
    title = event.get('title') or KINDS[event['kind']]
    locations = ', '.join(item['path'] + (':' + str(item['line']) if 'line' in item else '')
                          for item in event.get('locations', []))
    return STATUSES[event['status']] + ': ' + title + (' — ' + locations if locations else '')


def usage_text(event):
    parts = []
    if 'used' in event:
        context = 'Context: ' + format(event['used'], ',')
        if event.get('size', 0) > 0:
            context += ' / ' + format(event['size'], ',') + ' tokens (' + format(100 * event['used'] / event['size'], '.1f') + '%)'
        else:
            context += ' tokens'
        parts.append(context)
    elif 'size' in event:
        parts.append('Context capacity: ' + format(event['size'], ',') + ' tokens')
    for key, value in event.get('breakdown', {}).items():
        parts.append(format(value, ',') + ' ' + BREAKDOWN[key] + ' tokens')
    if 'cost' in event:
        cost = event['cost']
        parts.append('Reported cost: ' + format(cost['amount'], '.6g') + ' ' + cost['currency'])
    return '; '.join(parts)
