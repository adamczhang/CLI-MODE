"""Opt-in live parity probe: one activation and one turn per CLI; consumes quota.

For each agent: bind with the defaults, record advertised provider commands,
run a turn that uses a tool and answers in two paragraphs, relay it the way the
host does, refuse an unknown slash command, then stop. Evidence (events, relay
output, timings) is written outside the repository.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
# Outside Codex, model the Full Access profile the host would report (as the offline suite does).
os.environ.setdefault('CODEX_PERMISSION_PROFILE', ':danger-full-access')
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/scripts'))
import adapters  # noqa: E402
from controller import Controller  # noqa: E402
import host  # noqa: E402
from state import Store  # noqa: E402

# One read-only provider command per agent, run only if the agent advertises it.
# Antigravity is left out: its commands hand off to the native CLI in a fresh
# conversation, which this probe does not want.
SAFE_COMMANDS = {'claude': '/context', 'grok-build': '/context', 'copilot': '/context', 'codex': '/status'}

PROMPT = ('Read the file notes.txt in the current folder with your file tool. Then reply in exactly two short '
          'paragraphs: first, what the file says; second, one concrete improvement. Do not modify any files.')


def relay_for_host(control, request, cursor, folder, views):
    return (control.relay(request, cursor, wait=1, view_dir=folder / 'views') if views
            else control.relay_chain([request], cursor, wait=1))


def event_profile(events):
    kinds = collections.Counter(event.get('type') for event in events)
    ids = [event.get('messageId') for event in events if event.get('type') == 'message']
    groups = []
    for identity in ids:
        if not groups or groups[-1] != identity:
            groups.append(identity)
    return dict(kinds=dict(kinds), messageEvents=len(ids), distinctMessageIds=len(set(ids)),
                messageGroups=len(groups), idsPresent=any(ids),
                activityKinds=sorted({event.get('kind') for event in events if event.get('type') == 'activity'}),
                plan=any(event.get('type') == 'plan' for event in events),
                usage=any(event.get('type') == 'usage' for event in events))


def probe(agent, folder, model=None):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir(exist_ok=True)
    (work / 'notes.txt').write_text('Release checklist: run tests, update the changelog, tag the release.\n',
                                    encoding='utf-8')
    control = Controller(Store('parity-' + uuid.uuid4().hex, work, folder / 'state'), agent=agent)
    result = dict(agent=agent, host=host.current())
    # Claude Code shows text: no view files, confirmations and relay results as chat text.
    views = host.views()
    try:
        control.frontend(agent)
        began = time.monotonic()
        if model:
            adapter = adapters.module(agent)
            defaults = adapter.DEFAULTS
            prefetched = control.prefetch_usage(agent, model, defaults['access'], None)
            control.activate(model, defaults['access'], agent=agent)
            bound = dict(activation=control.activation_message(folder / 'activation.html' if views else None, prefetched))
        else:
            bound = control.bind(agent, require_hooks=False, message_output=folder / 'activation.html' if views else None,
                                 confirm=not views)
        result['bindSeconds'] = round(time.monotonic() - began, 1)
        result['activationText'] = bound['activation']['text']
        owned = control.store.read()['owned'][0]
        result['advertisedCommands'] = owned.get('advertisedCommands')
        control.mode('passthrough')
        began = time.monotonic()
        sent = control.send(PROMPT, output=lambda event: None)
        result['turnSeconds'] = round(time.monotonic() - began, 1)
        events = [json.loads(line) for line in Path(sent['events']).read_text(encoding='utf-8').splitlines()]
        result['events'] = event_profile(events)
        (folder / 'events.jsonl').write_text(Path(sent['events']).read_text(encoding='utf-8'), encoding='utf-8')
        # Relay the settled turn exactly as the host would.
        cursor, updates = 0, []
        while True:
            relayed = relay_for_host(control, sent['requestId'], cursor, folder, views)
            if relayed['markdown']:
                updates.append(relayed['markdown'])
            cursor = relayed['cursor']
            if relayed['done']:
                break
        result['relay'] = dict(updates=len(updates), reference=relayed.get('reference'),
                               finalText=relayed['text'][:400])
        if not views:
            import relay_view
            posted = updates + [relayed['text']]
            last = relay_view.messages(events, last_only=True).strip()
            result['relay'].update(
                longestResult=max(len(text) for text in posted),
                htmlWritten=any(folder.rglob('*.html')),
                finalMessageVerbatim=bool(last) and last in '\n\n'.join(posted))
        (folder / 'relay.md').write_text('\n\n---\n\n'.join(updates), encoding='utf-8')
        for key, text in (('unknownCommand', '/definitely-not-a-command please'), ('hostOwned', '/model x')):
            try:
                control.send(text, output=lambda event: None)
                result[key] = 'dispatched'
            except (RuntimeError, ValueError) as exc:
                result[key] = 'refused: ' + str(exc)[:160]
        command = SAFE_COMMANDS.get(agent)
        if command and command[1:] in (result['advertisedCommands'] or []):
            began = time.monotonic()
            sent = control.send(command, output=lambda event: None)
            events = [json.loads(line) for line in Path(sent['events']).read_text(encoding='utf-8').splitlines()]
            import relay_view
            result['providerCommand'] = dict(command=command, seconds=round(time.monotonic() - began, 1),
                                             events=event_profile(events),
                                             text=relay_view.messages(events)[:240])
    except Exception as exc:  # Evidence, not a crash: keep probing the other agents.
        result['error'] = type(exc).__name__ + ': ' + str(exc)[:400]
    finally:
        began = time.monotonic()
        stopped = control.off()
        result['offSeconds'] = round(time.monotonic() - began, 2)
        result['shutdownComplete'] = stopped['shutdownComplete']
    (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', nargs='+', default=adapters.implemented(), choices=adapters.implemented())
    parser.add_argument('--model', action='append', default=[], help='agent=model to override the default')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / ('cli-mode-parity-' + uuid.uuid4().hex[:8]))
    parser.add_argument('--host', choices=host.HOSTS, default=host.CODEX,
                        help='codex relays into inline views; claude-code relays as chat text.')
    args = parser.parse_args()
    host.select(args.host)
    models = dict(item.split('=', 1) for item in args.model)
    for agent in args.agents:
        result = probe(agent, args.output / agent, models.get(agent))
        print(json.dumps({key: value for key, value in result.items() if key != 'activationText'}), flush=True)
    print('Evidence: ' + str(args.output))


if __name__ == '__main__':
    main()
