"""Opt-in live five-provider research test; consumes account quota.

Controller integration only: never fabricates Desktop hook trust/readiness.
Evidence and disposable workspaces live outside the repository by default.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/scripts'))
import adapters
from controller import Controller
from state import Store


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def run(agent, folder):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir(exist_ok=True)
    controller = Controller(Store('five-cli-' + uuid.uuid4().hex, work, folder / 'state'), agent=agent)
    evidence = dict(agent=agent, hostIntegrationVerified=False, checks={}, turns=[])
    marker = 'orchid-' + uuid.uuid4().hex[:12]
    artifact = work / 'research.md'
    prompts = [
        "Research Python's standard-library module venv and the pip package installer using the official "
        "Python and pip documentation. In at most 180 words, explain what each does, give one Windows "
        "PowerShell command for creating a virtual environment, and provide two direct documentation links "
        "supporting your explanation. Remember the exact marker " + marker + ' and output path ' + str(artifact) +
        ' for later turns. Include the marker in your reply. Do not create files yet.',
        'Using the two tools and Windows environment from your previous answer, research whether activation '
        'is required to install a package into that environment. Give the explicit-interpreter command for '
        'installing requests, explain why it targets that environment, and cite the relevant official '
        'documentation. Repeat my exact marker. Do not install anything or create files.',
        'Combine your previous two answers into a beginner checklist of at most six steps. Include the '
        'environment creation command, installation without activation, and a command that prints the '
        'Python executable in use. Preserve the official source links and my exact marker. Save the '
        'checklist to the output path I supplied earlier, then return the full path and a short summary. '
        'Do not install packages or run the checklist commands.',
    ]
    def snapshot(label):
        state = controller.store.read()
        save(folder / (label + '-state.json'), state)
        return state

    def send(prompt, label):
        (folder / (label + '-prompt.txt')).write_text(prompt, encoding='utf-8')
        events = []
        started = time.monotonic()
        def output(event):
            events.append(event)
            save(folder / (label + '-events.json'), events)
        result = controller.send('/d ' + prompt, output=output, timeout=240)
        answer = ''.join(e['text'] for e in events if e['type'] == 'message')
        (folder / (label + '-answer.md')).write_text(answer, encoding='utf-8')
        row = dict(label=label, seconds=round(time.monotonic() - started, 2),
                   result=result, markerPresent=marker in answer)
        evidence['turns'].append(row)
        save(folder / 'evidence.json', evidence)
        snapshot(label)
        print(agent, label, json.dumps(row), flush=True)
        return answer

    try:
        controller.frontend(agent)
        defaults = adapters.module(agent).DEFAULTS
        # This test explicitly verifies the controller layer, not installed hooks.
        state = controller.activate(defaults['model'], defaults['access'],
                                    effort=defaults.get('effort'), agent=agent)
        evidence['checks']['activation'] = state['settings']
        first_main = state['main']
        snapshot('activation')
        print(agent, 'activated', flush=True)
        for index, prompt in enumerate(prompts, 1):
            answer = send(prompt, 'research-' + str(index))
            if index < 3 and marker not in answer:
                raise AssertionError('Research answer does not retain marker; inspect refusal/context failure.')
            state = controller.store.read()
            if state['main'] != first_main or len(state['owned']) != 1:
                raise AssertionError('Main-session ownership changed during research.')
        contents = artifact.read_text(encoding='utf-8')
        if marker not in contents or 'sys.executable' not in contents or 'https://' not in contents:
            raise AssertionError('Artifact missing marker, interpreter verification or citations.')
        steps = re.findall(r'^\s*\d+\.\s', contents, re.MULTILINE)
        if not 1 <= len(steps) <= 6:
            raise AssertionError('Artifact must contain between one and six checklist steps.')
        evidence['checks']['artifact'] = dict(path=str(artifact), sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
        evidence['researchPassed'] = True
        closed = controller.off()
        evidence['checks']['close'] = closed
        if not closed['shutdownComplete']:
            raise AssertionError('Shutdown incomplete.')
        evidence['checks']['repeatOff'] = controller.off()
        try:
            controller.send('This must not reach the provider.')
        except RuntimeError:
            evidence['checks']['offDispatchRejected'] = True
        else:
            raise AssertionError('Off-state dispatch was admitted.')
        controller.frontend(agent)
        controller.activate(defaults['model'], defaults['access'], effort=defaults.get('effort'), agent=agent)
        answer = send('Reply with exactly REOPEN_OK. Do not use tools.', 'reopen')
        evidence['checks']['reopen'] = 'REOPEN_OK' in answer
        if not evidence['checks']['reopen']:
            raise AssertionError('Reopened agent did not respond successfully.')
        evidence['success'] = True
    except Exception as exc:
        evidence.update(success=False, error=str(exc))
        print(agent, 'FAILED:', str(exc), flush=True)
    finally:
        try:
            evidence['shutdown'] = controller.off()
            if not evidence['shutdown']['shutdownComplete']:
                evidence.update(success=False, shutdownError='Incomplete cleanup')
        except Exception as exc:
            evidence.update(success=False, shutdownError=str(exc))
        snapshot('final')
        save(folder / 'evidence.json', evidence)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', nargs='+', choices=adapters.implemented(), default=adapters.implemented())
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    folder = (args.output or Path(tempfile.mkdtemp(prefix='cli-mode-five-'))).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    print('Evidence:', folder, flush=True)
    results = []
    for agent in args.agents:
        results.append(run(agent, folder / agent))
        save(folder / 'summary.json', results)
    return 0 if all(row.get('success') for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
