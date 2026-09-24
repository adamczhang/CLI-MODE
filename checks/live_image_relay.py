"""Opt-in installed-plugin image request through the captured queue and relay."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid


def run(plugin, output):
    plugin = plugin.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / 'workspace'
    workspace.mkdir(exist_ok=True)
    sys.path.insert(0, str(plugin / 'scripts'))
    sys.path.insert(0, str(plugin / 'hooks'))
    from controller import Controller
    from state import Store
    import route as hook
    store = Store('installed-image-relay-' + uuid.uuid4().hex, workspace, output / 'state')
    control = Controller(store, agent='agy')
    evidence = dict(plugin=str(plugin), agent='agy', workspace=str(workspace))
    try:
        hook.handle(dict(hook_event_name='SessionStart', session_id=store.thread,
                         cwd=str(workspace), source='startup'), store.root)
        bound = control.bind('agy', require_hooks=True)
        assert bound['active']
        evidence['main'] = bound['main']
        prompt = ('/d Use native image generation to make image-queued.png in this workspace: '
                  'a small ink-and-watercolor purple octopus holding a yellow umbrella. '
                  'Do not draw it with code, SVG, HTML, Pillow, canvas, or a shell tool. '
                  'If native image generation is unavailable, say so plainly and make no substitute.')
        context = hook.handle(dict(hook_event_name='UserPromptSubmit', session_id=store.thread,
                                   cwd=str(workspace), prompt=prompt), store.root)
        request = store.read()['turnRoute'].get('requestId')
        assert request and 'relay --request ' + request in str(context)
        evidence['requestId'] = request
        cursor, updates, artifacts = 0, [], []
        started = time.monotonic()
        while time.monotonic() - started < 360:
            result = control.relay(request, cursor=cursor, wait=3, view_dir=output / 'views')
            assert result['cursor'] >= cursor
            cursor = result['cursor']
            if result['markdown']:
                updates.append(result['markdown'])
            artifacts += result['artifacts']
            if result['done']:
                break
        else:
            raise TimeoutError('Relay did not settle in 360 seconds')
        evidence['seconds'] = round(time.monotonic() - started, 2)
        evidence['status'] = result['status']
        evidence['cursor'] = cursor
        evidence['updates'] = updates
        evidence['artifacts'] = artifacts
        evidence['finalText'] = result['text']
        evidence['finalView'] = result['messageView']
        file = workspace / 'image-queued.png'
        evidence['file'] = dict(path=str(file), exists=file.is_file(),
                                bytes=file.stat().st_size if file.is_file() else 0,
                                png=file.is_file() and file.open('rb').read(8) == b'\x89PNG\r\n\x1a\n')
        evidence['requestCount'] = len(store.read()['requests'])
        assert result['status'] == 'completed' and result['messageView']
        assert evidence['file']['png'] and evidence['requestCount'] == 1
    except Exception as exc:
        evidence['error'] = str(exc)
    finally:
        try:
            evidence['shutdownComplete'] = control.off()['shutdownComplete']
            evidence['ownedAfterShutdown'] = len(store.read()['owned'])
        except Exception as exc:
            evidence['shutdownError'] = str(exc)
        (output / 'evidence.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    evidence = run(args.plugin, args.output)
    print(json.dumps({k: v for k, v in evidence.items() if k not in ('updates', 'finalText')}, ensure_ascii=False))
    sys.exit(1 if 'error' in evidence or 'shutdownError' in evidence else 0)
