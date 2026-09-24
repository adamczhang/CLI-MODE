"""Opt-in live ACPX/AGY test; creates only owned temp sessions/files, closes in finally.

Run python checks/live_smoke.py [--image]. Does not install or activate the plugin
in a real host conversation. Uses the installed profile/account and model quota.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/scripts'))
from controller import Controller
from state import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', action='store_true')
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix='cli-mode-v011-proof-'))
    work = folder / 'workspace'
    work.mkdir()
    controller = Controller(Store('development-smoke-' + uuid.uuid4().hex, work, folder / 'state'))
    evidence = dict(workspace=str(work), hostIntegrationVerified=False, checks={})
    def collect(text):
        messages = []
        def output(event):
            if event['type'] == 'message': messages.append(event['text'])
            elif event['type'] in ('plan', 'error', 'context_warning'): print(json.dumps(event), flush=True)
        result = controller.send('/d ' + text, output=output)
        print('Completed: ' + ''.join(messages), flush=True)
        return ''.join(messages), result
    try:
        controller.frontend()
        current = controller.activate('gemini-3.8-flash-high', 'allow')
        evidence['checks']['activation'] = current['settings']
        print('Verified activation', flush=True)
        marker = 'orchid-' + uuid.uuid4().hex[:8]
        collect('Remember this test marker for the next message: ' + marker + '. Reply only with ACK. Do not use tools.')
        answer, _ = collect('What is the exact test marker from my previous message? Reply with only the marker; do not use tools.')
        assert marker in answer
        evidence['checks']['memory'] = True
        collect('Create a file answer.txt in the working directory containing exactly "artifact proof 42". Return its absolute path.')
        assert (work / 'answer.txt').read_text(encoding='utf-8').strip() == 'artifact proof 42'
        evidence['checks']['fileArtifact'] = True
        status = controller.backend.control(current['owned'][0], ['status', '-s', current['main']])
        evidence['checks']['liveStatusBetweenPrompts'] = status
        if args.image:
            collect('Use your native generate_image tool to make a tiny simple blue circle on a white background. '
                    'Save the generated raster as proof.png in the working directory. Do not draw it using code or SVG. '
                    'Return the exact absolute path and report a real tool failure if image generation is unavailable.')
            image = work / 'proof.png'
            assert image.is_file() and image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            evidence['checks']['generatedImage'] = str(image)
        assert len(controller.store.read()['owned']) == 1
        evidence['checks']['singleMainSession'] = True
        evidence['success'] = True
    except BaseException as exc:
        evidence.update(success=False, error=str(exc))
        raise
    finally:
        evidence['shutdown'] = controller.off()
        if not evidence['shutdown']['shutdownComplete']:
            evidence.update(success=False, shutdownError='Owned session shutdown is incomplete.')
        result = folder / 'evidence.json'
        result.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print('Evidence: ' + str(result), flush=True)
        print(json.dumps(evidence['shutdown']), flush=True)
        if not evidence['shutdown']['shutdownComplete']:
            raise RuntimeError(evidence['shutdownError'])


if __name__ == '__main__': main()
