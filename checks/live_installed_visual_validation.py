"""Opt-in installed-plugin validation; consumes one Claude provider turn.

Uses an empty workspace/state and the installed hook/controller entrypoints.
This tests the plugin without a host model; browser inspection is a separate
step and must not be reported as verification of Codex's chat renderer.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    plugin, output = args.plugin.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace, data, views = [output / name for name in ('workspace', 'state', 'views')]
    for path in (workspace, data, views):
        path.mkdir()
    (workspace / 'totals.py').write_text('def total(values):\n    return sum(values)\n', encoding='utf-8')
    (workspace / 'notes.txt').write_text('Validate total with empty, positive, and negative inputs.\n', encoding='utf-8')
    os.environ['CODEX_PERMISSION_PROFILE'] = ':danger-full-access'
    for key in list(os.environ):
        if key.startswith('CLI_MODE_'):
            del os.environ[key]
    sys.path.insert(0, str(plugin / 'scripts'))
    sys.path.insert(0, str(plugin / 'hooks'))
    from state import Store
    from controller import Controller
    import route
    import relay_view
    import host
    host.select(host.CODEX)
    thread = 'visual-' + uuid.uuid4().hex
    store = Store(thread, workspace, data)
    control = Controller(store, agent='claude')
    report = dict(plugin=str(plugin), thread=thread, cleanState=True,
                  hostModelInvoked=False, commands=[], updates=[])

    def save():
        (output / 'evidence.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def cli(*words):
        name = '%02d-%s' % (len(report['commands']), words[0])
        result = subprocess.run([sys.executable, str(plugin / 'scripts/controller.py'),
            '--thread', thread, '--workspace', str(workspace), '--data-root', str(data),
            '--menu-output', str(views / (name + '-menu.html')),
            '--message-output', str(views / (name + '-message.html')), *map(str, words)],
            capture_output=True, text=True, encoding='utf-8', timeout=240)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        value = json.loads(result.stdout)
        (output / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')
        report['commands'].append(list(words))
        save()
        return value

    def choose(value):
        choices = store.read()['pending']['choices']
        index = next(i for i, item in enumerate(choices, 1) if item['value'] == value)
        return cli('choose', index)

    try:
        assert not store.read()['owned']
        route.handle(dict(hook_event_name='SessionStart', session_id=thread,
                          cwd=str(workspace), source='startup'), data)
        cli('frontend', '--agent', 'home')
        cli('first-time-check', '--agent', 'claude')
        cli('frontend', '--agent', 'claude')
        cli('options', '--phase', 'model')
        choose('sonnet')
        choose('medium')
        activated = choose('allow')
        assert activated['active']
        report['activation'] = activated.get('activation')
        report['settings'] = activated['settings']
        cli('settings')
        cli('progress', '--choice', 'quiet')
        cli('progress', '--choice', 'activity')
        cli('settings', '--dismiss')
        cli('commands')
        cli('mode', '--choice', 'passthrough')
        prompt = ('Read notes.txt and totals.py. Create test_totals.py using unittest with the three '
                  'requested cases; run python -m unittest -v. Give one brief progress update before '
                  'working. Finish with a Markdown heading, a nested bullet list describing changes '
                  'and test cases, a small results table, and a fenced Python example calling total. '
                  'Use real tools. Stay in this workspace and do not delegate.')
        hook_output = route.handle(dict(hook_event_name='UserPromptSubmit', session_id=thread,
                                  cwd=str(workspace), prompt=prompt), data)
        report['hookOutput'] = hook_output
        request = store.read()['turnRoute']['requestId']
        report['requestId'] = request
        cursor, deadline = 0, time.monotonic() + 300
        while time.monotonic() < deadline:
            result = cli('relay', '--request', request, '--cursor', cursor,
                         '--wait', 3, '--view-dir', str(views))
            cursor = result['cursor']
            if result['markdown']:
                report['updates'].append(result['markdown'])
            save()
            if result['done']:
                assert result['status'] == 'completed', result
                report['final'] = result
                break
        else:
            raise TimeoutError('Provider turn did not finish')
        events, position = [], 0
        while True:
            observed = control.observe(request, position, limit=500)
            events.extend(observed['events'])
            position = observed['cursor']
            if not observed['events']:
                break
        (output / 'events.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
        report['eventTypes'] = dict(Counter(e['type'] for e in events))
        report['activityKinds'] = sorted({e['kind'] for e in events if e['type'] == 'activity'})
        assert (workspace / 'test_totals.py').is_file()
        test = subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=workspace,
                              capture_output=True, text=True, timeout=30)
        report['independentTests'] = dict(code=test.returncode, output=test.stdout + test.stderr)
        assert test.returncode == 0
        # Same public events: compare host formatting without resending a prompt.
        (output / 'claude-host-comparison.md').write_text(
            relay_view.final_markdown('Claude Code', events, events), encoding='utf-8')
        report['quiet'] = dict(zip(('path', 'text', 'artifacts'), relay_view.render(
            'Claude Code', events, views / 'quiet-comparison.html', show_work=False)))
        report['passingAnnouncements'] = sum(text.count('Passing to Claude...')
                                             for text in report['updates'])
        html = Path(report['final']['messageView']['path']).read_text(encoding='utf-8')
        report['presentationDiagnostics'] = dict(
            hasSemanticTable='<table' in html, hasCodeBlock='<pre' in html,
            hasNestedWork='<details' in html,
            repeatedPassing=report['passingAnnouncements'] > 1)
        assert report['passingAnnouncements'] == 1, report['updates']
        assert report['presentationDiagnostics']['hasSemanticTable']
        assert report['presentationDiagnostics']['hasCodeBlock']
        assert any(e.get('title') == 'Run Python tests (unittest)' for e in events)
        report['passed'] = True
    finally:
        report['shutdown'] = cli('off')
        save()
    print(json.dumps({k: report[k] for k in ('passed', 'eventTypes', 'activityKinds', 'settings')}))
    print(output)


if __name__ == '__main__':
    main()
