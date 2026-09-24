"""Opt-in provider-quota check: warm settings, cold resume, identity and memory.

Uses disposable state/workspaces and a short owner TTL on the test backend only.
Requires explicit user approval. Does not install plugins or edit global config.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/cli-mode/scripts'))
import adapters
import frontends
import host
from controller import Controller
from state import Store


def probe(agent, folder):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir()
    backend = adapters.module(agent).Backend()
    backend.owner_ttl = 20
    control = Controller(Store('settings-' + uuid.uuid4().hex, work, folder / 'state'), backend, agent)
    result = dict(agent=agent, changes=[], readinessCalls=0)
    original_readiness = control.readiness

    def readiness(*args, **kwargs):
        result['readinessCalls'] += 1
        return original_readiness(*args, **kwargs)

    control.readiness = readiness

    def save():
        (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

    def identity():
        state = control.store.read()
        assert state['active'] and len(state['owned']) == 1
        return state['owned'][0]['providerSession']

    def send(text, label):
        events = []
        sent = control.send('/d ' + text, output=events.append, timeout=120)
        (folder / (label + '-events.json')).write_text(json.dumps(events, indent=2), encoding='utf-8')
        assert control.store.read()['requests'][sent['requestId']]['status'] == 'completed', sent
        return ''.join(e.get('text', '') for e in events if e['type'] == 'message')

    def change(phase, choice, cold=False):
        before = result['readinessCalls']
        with control.store.edit() as state:
            state['turnRoute'] = dict(route='tune', choice=str(choice))
        began = time.monotonic()
        updated = control.tune_choice(phase)
        assert updated['active'] and not updated.get('pending'), updated.get('message')
        assert identity() == result['providerSession'], 'Provider identity changed'
        wakes = result['readinessCalls'] - before
        row = dict(phase=phase, choice=choice, cold=cold, readinessCalls=wakes,
                   seconds=round(time.monotonic() - began, 2), settings=updated['settings'])
        result['changes'].append(row)
        save()
        assert wakes == (1 if cold else 0), 'Unexpected readiness prompt count'

    try:
        control.frontend(agent)
        defaults = control.adapter.DEFAULTS
        control.activate(defaults['model'], defaults['access'], effort=defaults.get('effort'), agent=agent)
        result['providerSession'] = identity()
        accepted = dict(control.store.read()['settings'])
        control.settings_menu()
        control.tune('model')
        refreshed = control.refresh()
        result['refresh'] = dict(refreshed=refreshed['refreshed'], catalogStatus=refreshed['catalogStatus'])
        assert refreshed['refreshed'], refreshed.get('message')
        assert identity() == result['providerSession'] and control.store.read()['settings'] == accepted
        control.settings_menu(dismiss=True)
        marker = 'MEMORY_' + uuid.uuid4().hex
        answer = send('Remember this exact marker for later: ' + marker +
                      '. Reply with the marker only. Do not use tools.', 'seed')
        assert marker in answer, 'Seed marker missing'
        # Choose only advertised values; prefer modest models for follow-up turns.
        for phase in ('model', 'effort', 'access'):
            settings = control.store.read()['settings']
            options = frontends.phase_options(control.store.root, agent, phase, settings)
            current = (settings['model'] if phase == 'model' or
                       (phase == 'effort' and adapters.descriptor(agent)['effortRepresentation'] == 'combined')
                       else settings.get('effortValue') if phase == 'effort' else settings['access'])
            choices = [(label, value) for label, value in options if value is not None and value != current]
            if not choices:
                result.setdefault('unsupportedChanges', []).append(phase)
                continue
            if phase == 'model':
                choices.sort(key=lambda item: (not any(word in str(item).lower()
                    for word in ('flash', 'haiku', 'mini', 'sol', 'sonnet')), str(item)))
            change(phase, choices[0][1])
            if phase == 'access':
                change('access', 'allow')
        answer = send('What exact marker did I ask you to remember? Reply with the marker only. Do not use tools.', 'warm-recall')
        assert marker in answer, 'Memory lost after warm settings'
        assert identity() == result['providerSession']
        result['warmRecall'] = True
        # Model-specific controls must return when switching away from a model
        # without an effort selector (for example Claude Haiku -> Opus).
        settings = control.store.read()['settings']
        if (adapters.descriptor(agent)['effortRepresentation'] == 'separate'
                and settings.get('effortKey') is None and settings['model'] != defaults['model']):
            change('model', defaults['model'])
            result['effortRestored'] = control.store.read()['settings'].get('effortKey') is not None
            assert result['effortRestored'], 'Effort selector did not return'
        # Also finish parity checks skipped by the old harness's relay signature.
        for text in ('/definitely-not-a-command please', '/model x'):
            try:
                control.send('/d ' + text, output=lambda event: None)
            except (RuntimeError, ValueError):
                pass
            else:
                raise AssertionError('Unsupported command dispatched: ' + text)
        result['commandRefusals'] = True
        command = {'codex': '/status', 'copilot': '/context', 'grok-build': '/context', 'claude': '/context'}.get(agent)
        owned = control.store.read()['owned'][0]
        if command and command[1:] in (owned.get('advertisedCommands') or []):
            events = []
            control.send('/d ' + command, output=events.append, timeout=120)
            result['providerCommand'] = command
            (folder / 'command-events.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
        deadline = time.monotonic() + 60
        while True:
            status = backend.control(owned, ['status', '-s', owned['name']])['status']
            if status in ('idle', 'dead'):
                break
            assert time.monotonic() < deadline, 'Test owner did not expire'
            time.sleep(.5)
        result['expiredStatus'] = status
        change('access', 'allow', cold=True)
        answer = send('What exact marker did I ask you to remember? Reply with the marker only. Do not use tools.', 'cold-recall')
        assert marker in answer, 'Memory lost after cold resume'
        assert identity() == result['providerSession']
        result['coldRecall'] = result['passed'] = True
    except Exception as exc:
        result.update(passed=False, error=type(exc).__name__ + ': ' + str(exc)[:1000])
    finally:
        try:
            result['shutdownComplete'] = control.off()['shutdownComplete']
            if not result['shutdownComplete']:
                result['passed'] = False
        except Exception as exc:
            result.update(passed=False, shutdownError=str(exc))
        save()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', nargs='+', required=True, choices=adapters.implemented())
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / ('cli-mode-settings-' + uuid.uuid4().hex[:8]))
    args = parser.parse_args()
    host.select(host.CODEX)
    print('Evidence: ' + str(args.output), flush=True)
    results = []
    for agent in args.agents:
        result = probe(agent, args.output / agent)
        results.append(result)
        print(json.dumps(result), flush=True)
    sys.exit(0 if all(row.get('passed') for row in results) else 1)
