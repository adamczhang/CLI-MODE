"""Opt-in live settings, idle, native-command and cancellation checks.

Uses only isolated test ownership. Run after five_cli_live.py, not concurrently.
"""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import threading
import time
import uuid

from five_cli_live import adapters, Controller, Store, save
import frontends


def run(agent, folder, skip_model=False):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir(exist_ok=True)
    adapter = adapters.module(agent)
    class ShortIdle(adapter.Backend):
        owner_ttl = 5
    c = Controller(Store('controls-' + uuid.uuid4().hex, work, folder / 'state'), ShortIdle(), agent)
    evidence = dict(agent=agent, routingMode='direct', hostIntegrationVerified=False, checks={})
    def record(name, value):
        evidence['checks'][name] = value
        save(folder / 'evidence.json', evidence)
        print(agent, name, json.dumps(value), flush=True)
        if value is False or isinstance(value, dict) and value.get('sameMain') is False:
            raise AssertionError(name + ' failed')
    def send(prompt, name):
        events = []
        result = c.send('/d ' + prompt, output=events.append, timeout=120)
        save(folder / (name + '-events.json'), events)
        answer = ''.join(e['text'] for e in events if e['type'] == 'message')
        return answer, result
    def activate(model=None, effort=None, access=None):
        s = c.store.read().get('settings') or adapter.DEFAULTS
        c.frontend(agent)
        return c.activate(model or s['model'], access or s['access'],
                          effort=effort if effort is not None else (None if agent == 'agy' else s.get('effort')),
                          agent=agent)
    try:
        # Exercise bind's real prerequisite scan, while explicitly retaining the
        # controller-only boundary (no synthetic Desktop hook receipts).
        state = c.bind(agent, require_hooks=False)
        main = state['main']
        record('activation', state['settings'])
        original = dict(state['settings'])
        for phase in ('model', 'effort', 'access'):
            menu = frontends.phase_menu(c.store.root, agent, phase, original)
            save(folder / (phase + '-menu.json'), menu)
        record('menus', True)
        marker = 'iris-' + uuid.uuid4().hex[:12]
        answer, _ = send('Remember ' + marker + '. Reply with only that marker. Do not use tools.', 'marker')
        if marker not in answer:
            raise RuntimeError('Account cannot complete the simple marker probe.')
        time.sleep(7)
        owned = c.store.read()['owned'][0]
        status = c.backend.control(owned, ['status', '-s', owned['name']])
        record('idleStatus', status)
        if status.get('status') not in ('idle', 'dead', 'not-running', 'stopped'):
            raise AssertionError('Test idle timeout did not expire the owner.')
        # Restore production TTL after proving expiry. A five-second owner
        # lifetime during multi-step tuning introduces unrelated restart races.
        c.backend.owner_ttl = 1800
        answer, result = send('Repeat the exact marker I gave you. Do not use tools.', 'idle-recall')
        record('idleMemory', marker in answer)
        if marker not in answer:
            raise AssertionError('Context lost after idle restart.')
        advertised_access = {value for _, value in frontends.phase_options(c.store.root, agent, 'access', original)}
        for access in ('prompt', 'auto-edit', 'allow'):
            if access not in advertised_access:
                continue
            state = activate(access=access)
            record('access-' + access, dict(settings=state['settings'], sameMain=state['main'] == main))
            target = work / ('permission-' + access + '.txt')
            try:
                answer, result = send('Create the file ' + str(target) +
                    ' containing exactly PERMISSION_PROOF. Use your file tool. Do not ask me to create it.',
                    'permission-' + access)
                record('permissionResponse-' + access, answer)
            except RuntimeError as exc:
                record('permissionResponse-' + access, str(exc))
                if access != 'prompt':
                    raise
            record('permissionEnforced-' + access,
                   not target.exists() if access == 'prompt' else
                   target.is_file() and target.read_text(encoding='utf-8').strip() == 'PERMISSION_PROOF')
        efforts = frontends.phase_options(c.store.root, agent, 'effort', original)
        alternate = next(((label, value) for label, value in efforts if value is not None
                          and str(label).lower() != str(original.get('effort')).lower()), None)
        if alternate:
            state = activate(model=alternate[1] if agent == 'agy' else None,
                             effort=None if agent == 'agy' else alternate[1])
            record('effortChange', dict(settings=state['settings'], sameMain=state['main'] == main))
            activate(model=original['model'], effort=None if agent == 'agy' else original.get('effort'))
        else:
            record('effortChange', 'N/A: no advertised selector')
        models = frontends.phase_options(c.store.root, agent, 'model', original)
        alternate_model = None if skip_model else next((value for _, value in models if value != original['model']), None)
        if alternate_model:
            state = activate(model=alternate_model)
            record('modelChange', dict(settings=state['settings'], sameMain=state['main'] == main))
            activate(model=original['model'], effort=None if agent == 'agy' else original.get('effort'))
        else:
            record('modelChange', 'BLOCKED: alternative model requires plan access; testing current model only'
                   if skip_model else 'N/A: no advertised selector')
        answer, _ = send('Repeat the iris- test marker I originally asked you to remember. Do not use tools.',
                         'tuning-recall')
        record('memoryAfterTuning', marker in answer)
        owned = c.store.read()['owned'][0]
        commands = owned.get('advertisedCommands') or []
        record('advertisedCommands', commands)
        command = next(('/' + name for name in ('context', 'status', 'copy-request-id') if name in commands), None)
        if command:
            answer, result = send(command, 'native-command')
            record('nativeCommand', dict(command=command, result=result, response=answer,
                                          transport=c.store.read()['owned'][0].get('transport', 'acp')))
        else:
            record('nativeCommand', 'NOT RUN: no safe read-only command advertised')
        before = c.store.read()
        try:
            c.send('/d ' + '/model')
        except RuntimeError as exc:
            record('hostOwnedCommandRejected', str(exc))
        else:
            raise AssertionError('Provider model command bypassed CLI-MODE settings.')
        if commands or agent == 'agy':
            try:
                c.send('/d ' + '/definitely-unavailable-cli-validation-command')
            except RuntimeError as exc:
                record('unknownCommandRejected', str(exc))
            else:
                raise AssertionError('Unknown command was admitted.')
        else:
            record('unknownCommandRejected', 'NOT RUN: command catalog unavailable')
        record('unknownPreservesOwnership', before['owned'] == c.store.read()['owned'])
        answer, _ = send('Remember ' + marker + '. Reply only with the marker, no tools.', 'post-command-marker')
        answer, _ = send('Repeat the exact marker from my previous message, no tools.', 'post-command-recall')
        record('postCommandMemory', marker in answer)
        outcome = {}
        def active_turn():
            try:
                outcome['result'] = send('Explain virtual environments in twenty detailed paragraphs. Do not use tools.', 'cancel-turn')
            except Exception as exc:
                outcome['error'] = str(exc)
        worker = threading.Thread(target=active_turn, daemon=True)
        worker.start()
        deadline = time.monotonic() + 15
        while worker.is_alive() and not c.store.read()['inflight'] and time.monotonic() < deadline:
            time.sleep(.1)
        record('inflightBeforeStop', bool(c.store.read()['inflight']))
        record('stopDuringTurn', c.off())
        worker.join(30)
        record('submitterSettled', not worker.is_alive())
        record('settledShutdown', c.off())
    except Exception as exc:
        evidence['error'] = str(exc)
        print(agent, 'FAILED:', exc, flush=True)
    finally:
        try:
            shutdown = c.off()
            record('finalShutdown', shutdown)
            if not shutdown['shutdownComplete']:
                evidence['shutdownError'] = 'Owned session cleanup is incomplete'
        except Exception as exc:
            evidence['shutdownError'] = str(exc)
        save(folder / 'evidence.json', evidence)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', nargs='+', choices=adapters.implemented(), default=adapters.implemented())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-model-for', nargs='*', choices=adapters.implemented(), default=[])
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if len(args.agents) == 1:
        results = [run(args.agents[0], root / args.agents[0], args.agents[0] in args.skip_model_for)]
    else:
        # Fresh imports per backend make an interrupted development run resumable
        # without keeping an obsolete controller loaded for subsequent agents.
        results = []
        for agent in args.agents:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--agents', agent,
                            '--output', str(root), '--skip-model-for', *args.skip_model_for], check=False)
            results.append(json.loads((root / agent / 'evidence.json').read_text(encoding='utf-8')))
    save(args.output.resolve() / 'summary.json', results)
    sys.exit(1 if any('error' in r or 'shutdownError' in r for r in results) else 0)
