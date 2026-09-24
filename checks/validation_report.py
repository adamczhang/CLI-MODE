"""The validation matrix: feature ID x host x agent -> pass, fail or skip (plan Phase 6).

It reads every summary.json under the evidence folder (the host harnesses write them,
with `results` rows naming their feature IDs), plus `rows.jsonl` files holding rows for
the other layers ({"feature", "host", "agent", "status", "reason", "evidence"}); hosts
there are `offline`, `controller`, `claude-code` or `codex`. Folders named `superseded`
are skipped (runs replaced by later ones, kept as evidence). A row's status is pass,
fail, skip, or fixed (failed, then fixed on the branch and verified again). A cell is
FAIL if any row failed, else FIXED if any was fixed, else PASS if any passed, else SKIP.
Features with no row at all are listed as untested, so a gap is never silent.

    python checks/validation_report.py %TEMP%\\cmv [--output report.md]
"""
import argparse
import collections
import json
from pathlib import Path

from validation_scenarios import FEATURES

HOSTS = ('offline', 'controller', 'claude-code', 'codex')
AGENTS = ('agy', 'claude', 'grok-build', 'cursor', 'copilot', 'codex', '-')
MARK = {'pass': 'P', 'fail': 'F', 'skip': 'S', 'fixed': 'X'}


def current(path):
    return 'superseded' not in Path(path).parts


def rows(folder):
    found = []
    for path in sorted(filter(current, Path(folder).rglob('summary.json'))):
        try:
            summary = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            continue
        if not isinstance(summary, dict):
            continue  # Another script's summary (a list): its rows come through rows.jsonl.
        for result in summary.get('results') or []:
            for feature in result.get('features') or []:
                found.append(dict(feature=feature, host=summary.get('host'), agent=summary.get('agent'),
                                  status=result['status'], step=result.get('step'),
                                  reason='; '.join(result.get('problems') or []) or result.get('reason') or '',
                                  evidence=str(path.parent)))
    for path in sorted(filter(current, Path(folder).rglob('rows.jsonl'))):
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                item = json.loads(line)
                item.setdefault('evidence', str(path.parent))
                found.append(item)
    return found


def cells(found):
    table = collections.defaultdict(list)
    for item in found:
        table[item['feature'], item['host'], item.get('agent') or '-'].append(item)
    out = {}
    for key, items in table.items():
        statuses = {item['status'] for item in items}
        out[key] = next(status for status in ('fail', 'fixed', 'pass', 'skip') if status in statuses)
    return out, table


def markdown(found):
    out, table = cells(found)
    columns = [(h, a) for h in HOSTS for a in AGENTS if any(key[1:] == (h, a) for key in out)]
    head = '| Feature | ' + ' | '.join(h.replace('claude-code', 'claude').replace('controller', 'ctl')[:6] + ':' + a[:5]
                                      for h, a in columns) + ' |'
    lines = ['# CLI-MODE validation matrix', '',
             'P pass, X failed then fixed on the branch and verified again, F fail, S skip (reason below), '
             'blank: not run for that pair.', '', head,
             '|---|' + '---|' * len(columns)]
    untested = []
    for feature, name in FEATURES.items():
        marks = [MARK.get(out.get((feature, h, a)), '') for h, a in columns]
        if not any(marks):
            untested.append(feature + ' ' + name)
        lines.append('| ' + feature + ' ' + name + ' | ' + ' | '.join(marks) + ' |')
    totals = collections.Counter(out.values())
    lines += ['', 'Cells: %d pass, %d fixed, %d fail, %d skip.' % (totals['pass'], totals['fixed'], totals['fail'],
                                                                  totals['skip'])]
    if untested:
        lines += ['', '## Untested features', ''] + ['- ' + item for item in untested]
    lines += ['', '## Failures', '']
    for key, items in sorted(table.items()):
        for item in items:
            if item['status'] == 'fail':
                lines.append('- **%s** %s/%s (%s): %s  \n  evidence: `%s`' % (
                    key[0], key[1], key[2], item.get('step') or '', item.get('reason') or '', item.get('evidence')))
    lines += ['', '## Fixed on the branch', '']
    seen = set()
    for key, items in sorted(table.items()):
        for item in items:
            text = (key[0], key[1], key[2], item.get('reason') or '')
            if item['status'] == 'fixed' and text not in seen:
                seen.add(text)
                lines.append('- **%s** %s/%s: %s' % text)
    lines += ['', '## Skips', '']
    seen = set()
    for key, items in sorted(table.items()):
        for item in items:
            text = (key[0], key[1], key[2], item.get('reason'))
            if item['status'] == 'skip' and text not in seen:
                seen.add(text)
                lines.append('- %s %s/%s: %s' % text)
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = markdown(rows(args.folder))
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
