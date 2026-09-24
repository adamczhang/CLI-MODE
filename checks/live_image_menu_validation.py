"""Opt-in live model/effort menu and native image-request validation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_coding_queue import ROOT, cli, hook, require, save
from controller import Controller
from state import Store
import adapters


def menu_choice(control, phase, desired=None, alternate=False):
    page = cli(control, 'options', '--phase', phase)
    while True:
        choices = control.store.read()['pending']['choices']
        selected = next(((i, x) for i, x in enumerate(choices, 1)
                         if (x['value'] != desired if alternate else x['value'] == desired)), None)
        if selected:
            return cli(control, 'choose', selected[0]), selected[1]
        if page.get('page', 1) >= page.get('pages', 1):
            return None, None
        page = cli(control, 'navigate', '>')


def image_files(work):
    found = []
    for path in work.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            continue
        signature = path.open('rb').read(16)
        if signature.startswith(b'\x89PNG\r\n\x1a\n') or signature.startswith(b'\xff\xd8\xff') or signature.startswith(b'RIFF'):
            found.append(dict(path=str(path), bytes=path.stat().st_size))
    return found


def run(agent, folder, menus_only=False, images_only=False, image_numbers=(1, 2)):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir(exist_ok=True)
    control = Controller(Store('image-menu-' + uuid.uuid4().hex, work, folder / 'state'), agent=agent)
    evidence = dict(agent=agent, workspace=str(work), hostIntegrationVerified=False, menus={}, images=[], errors=[])
    def persist():
        save(folder / 'evidence.json', evidence)
    try:
        hook.handle(dict(hook_event_name='SessionStart', session_id=control.store.thread,
                         cwd=str(work), source='startup'), control.store.root)
        home = cli(control, 'frontend', '--agent', 'home')
        require(agent in [x['value'] for x in home['pending']['choices']], 'Agent omitted from home menu')
        cli(control, 'frontend', '--agent', agent)
        default = adapters.module(agent).DEFAULTS
        for phase in ('model', 'effort', 'access'):
            page = cli(control, 'options', '--phase', phase)
            evidence['menus']['initial-' + phase] = dict(text=page['activationMenu'],
                choices=control.store.read()['pending']['choices'])
        activated = cli(control, 'bind', '--agent', agent)
        require(activated['active'], 'Activation failed')
        original = dict(activated['settings'])
        main = activated['main']
        evidence['initialSettings'] = original
        print(agent, 'active', original, flush=True)
        for phase in (() if images_only else ('model', 'effort')):
            try:
                settings = control.store.read()['settings']
                cli(control, 'settings')
                cli(control, 'tune', '--phase', phase)
                desired = settings['model'] if phase == 'model' else (
                    settings['model'] if adapters.descriptor(agent)['effortRepresentation'] == 'combined'
                    else settings.get('effortValue'))
                result, choice = menu_choice(control, phase, desired, alternate=True)
                if choice is None:
                    evidence['menus'][phase] = 'N/A: no advertised alternative'
                    cli(control, 'settings', '--dismiss')
                    continue
                if phase == 'model':
                    # Model selection advances through effort and access.
                    effort = control.store.read()['pending']['draft']['settings'].get('effortValue')
                    if adapters.descriptor(agent)['effortRepresentation'] == 'combined':
                        effort = choice['value']
                    done, _ = menu_choice(control, 'effort', effort)
                    if done is None:
                        cli(control, 'options', '--phase', 'effort')
                        done = cli(control, 'choose', 1)
                    require(done is not None, 'No effort choice after model selection')
                access = control.store.read()['pending']['draft']['settings']['access']
                done, _ = menu_choice(control, 'access', access)
                require(done and done['active'] and done['main'] == main,
                        'Choice failed or replaced the main session')
                evidence['menus'][phase] = dict(choice=choice, applied=done['settings'], sameMain=True)
                # Restore original accepted settings using the same real menu path.
                cli(control, 'settings')
                cli(control, 'tune', '--phase', 'model')
                done, _ = menu_choice(control, 'model', original['model'])
                require(done is not None, 'Original model no longer offered')
                wanted_effort = original['model'] if adapters.descriptor(agent)['effortRepresentation'] == 'combined' else original.get('effortValue')
                done, _ = menu_choice(control, 'effort', wanted_effort)
                if done is None:
                    cli(control, 'options', '--phase', 'effort')
                    done = cli(control, 'choose', 1)
                require(done is not None, 'Original effort no longer offered')
                done, _ = menu_choice(control, 'access', original['access'])
                require(done and done['active'] and done['main'] == main, 'Restore failed')
                evidence['menus'][phase]['restored'] = done['settings']
            except Exception as exc:
                evidence['errors'].append(f'{phase} menu: {exc}')
                print(agent, phase, 'menu error:', exc, flush=True)
                try:
                    control.store.read()['pending'] and cli(control, 'settings', '--dismiss')
                except Exception:
                    pass
            persist()
        if not control.store.read()['active']:
            raise RuntimeError('Agent inactive before image requests')
        prompts = [] if menus_only else [
            'Use a native image-generation capability available to this agent to create a small PNG in this workspace named image-1.png: a watercolor orange cat reading a blue book under a lamp. Do not draw using code, SVG, HTML, Pillow, canvas, or a shell tool. If native image generation is unavailable, say so plainly and do not create a substitute.',
            'Use native image generation again to make image-2.png in this workspace: a photorealistic red bicycle beside a yellow sunflower field at sunrise. This is a separate image. Do not draw using code, SVG, HTML, Pillow, canvas, or a shell tool. If native image generation is unavailable, say so plainly and do not create a substitute.',
        ]
        for number, prompt in enumerate(prompts, 1):
            if number not in image_numbers:
                continue
            events = []
            started = time.monotonic()
            try:
                result = control.send('/d ' + prompt, output=events.append, timeout=360)
                status = 'completed'
            except Exception as exc:
                result = str(exc)
                status = 'failed'
            answer = ''.join(x.get('text', '') for x in events if x.get('type') == 'message')
            artifacts = [{k: v for k, v in x.get('content', {}).items() if k not in ('data', 'blob')}
                         for x in events if x.get('type') == 'artifact']
            row = dict(number=number, prompt=prompt, status=status, seconds=round(time.monotonic()-started, 2),
                       answer=answer, result=result, eventTypes=[x.get('type') for x in events],
                       artifacts=artifacts, files=image_files(work))
            evidence['images'].append(row)
            persist()
            print(agent, 'image', number, status, 'artifacts', len(artifacts), 'files', len(row['files']), flush=True)
        evidence['finalSettings'] = control.store.read().get('settings')
    except Exception as exc:
        evidence['errors'].append(str(exc))
        print(agent, 'ERROR', exc, flush=True)
    finally:
        try:
            stopped = cli(control, 'off')
            evidence['shutdownComplete'] = stopped['shutdownComplete']
            evidence['ownedAfterShutdown'] = control.store.read()['owned']
        except Exception as exc:
            evidence['errors'].append('shutdown: ' + str(exc))
        persist()
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', nargs='+', choices=adapters.implemented(), default=adapters.implemented())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--menus-only', action='store_true')
    parser.add_argument('--images-only', action='store_true')
    parser.add_argument('--image-numbers', nargs='+', type=int, choices=(1, 2), default=(1, 2))
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for agent in args.agents:
        results.append(run(agent, root / agent, args.menus_only, args.images_only, args.image_numbers))
    save(root / 'summary.json', results)
    sys.exit(1 if any(x['errors'] or not x.get('shutdownComplete') for x in results) else 0)
