"""The live formatting prompt (validation plan 7b and 7c): one turn per agent, its raw events kept
and drawn by every renderer. Consumes agent quota.

For each agent: bind with its defaults (Allow access), send PROMPT, keep the public events
file, render it through R1-R4 (formatting_corpus.render_*), check what the answer should
contain, and compare the emissions with the agent's known profile. With --viewer, the turn
runs with /cli view on: it must open exactly one viewer window, closing that window mid-turn
must not affect the turn, and /cli stop must leave no viewer process.

    python checks/formatting_prompt.py --agents agy grok-build --output %TEMP%\\cmv\\p1-format [--viewer]

`capture(events_path, folder)` is the raw-events helper the host harnesses (Phases 2 and 3) use
on a relayed request's log.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

CHECKS = Path(__file__).resolve().parent
os.environ.setdefault('CODEX_PERMISSION_PROFILE', ':danger-full-access')
import formatting_corpus as corpus  # noqa: E402  (puts the plugin's scripts on the path)
import adapters  # noqa: E402
from controller import Controller  # noqa: E402
import host  # noqa: E402
from live_parity_probe import event_profile  # noqa: E402
import relay_view  # noqa: E402
from state import Store  # noqa: E402
import viewer  # noqa: E402

PROMPT = ('Formatting check. First read calc.py with your file tool (do not change any file). If you can keep a '
          'task plan, make a three-step plan and update it as you go. Then answer in Markdown with exactly these '
          'parts, in order:\n'
          '1. A heading "Summary".\n'
          '2. A table with the columns Name, Kind and Lines, one row per function in calc.py.\n'
          '3. A nested bullet list: two top-level items, each with one sub-item.\n'
          '4. A python code block showing the add function.\n'
          '5. A Markdown link to calc.py written as [calc.py](file:///<the full path of calc.py>).\n'
          '6. One single line of at least 250 characters describing the file.\n'
          '7. The emoji \U0001f389 and \u2705.\n'
          '8. Exactly this sentence: It costs $5 and $10.')

# What each agent is known to emit (plan P4). None: not known, recorded only.
PROFILES = {
    'claude': dict(messageIds=True, usage=True),
    'codex': dict(messageIds=True, usage=None),
    'grok-build': dict(messageIds=False, usage=None),
    'copilot': dict(messageIds=False, usage=True),
    'agy': dict(messageIds=False, usage=None),
    'cursor': dict(messageIds=None, usage=None),
}


def answer_checks(text):
    """What the formatting prompt asks the answer to contain."""
    return {
        'heading': bool(__import__('re').search(r'(?m)^#{1,6}\s+Summary', text)),
        'table': bool(__import__('re').search(r'(?m)^\|.*Name.*\|.*Kind.*\|', text)),
        'nestedList': bool(__import__('re').search(r'(?m)^\s{2,}[-*+]\s+\S', text)),
        'codeBlock': '```python' in text or '```py' in text,
        'fileLink': 'file:///' in text,
        'longLine': any(len(line) >= 250 for line in text.splitlines()),
        'emoji': '\U0001f389' in text and '\u2705' in text,
        'dollars': 'It costs $5 and $10.' in text,
    }


def capture(events_path, folder, agent=None, shells=None):
    """Keep a request's raw public events and draw them through every renderer. Returns a summary."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / 'events.jsonl'
    shutil.copyfile(events_path, raw)
    turn = corpus.read_events(raw)
    if not any(event.get('type') == 'done' for event in turn):
        turn.append(dict(type='done', stopReason='end_turn'))
    rendered = {mode: corpus.render_r1_r3(turn, folder / 'relay', mode) for mode in ('activity', 'quiet')}
    viewers = {}
    for shell in shells or [viewer.shell()]:
        plain, _, err, code, _ = corpus.render_r4(turn, folder / ('viewer-' + Path(shell).stem), shell, agent or 'Agent', 3)
        viewers[Path(shell).stem] = dict(exit=code, errors=err[-300:], chars=len(plain))
    words = relay_view.messages(turn, last_only=True)
    profile = event_profile(turn)
    expected = PROFILES.get(agent) or {}
    mismatches = [key for key, value in expected.items() if value is not None and
                  (profile['idsPresent'] if key == 'messageIds' else profile['usage']) != value]
    summary = dict(agent=agent, events=profile, answer=answer_checks(words), latexRisks=corpus.latex_risks(words),
                   verbatim={key: relay_view.defuse(words).strip() in rendered['activity'][key] for key in ('r1', 'r3')},
                   r3Parts=rendered['activity']['r3Parts'], viewers=viewers, profileMismatches=mismatches,
                   evidence=str(folder))
    (folder / 'capture.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding='utf-8')
    return summary


def viewer_processes(folder):
    """PowerShell viewer windows following this store's operations folder."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='pwsh.exe' or Name='powershell.exe'\" | "
              "Where-Object { $_.CommandLine -like '*viewer.ps1*' } | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    out = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True).stdout
    return [int(line.split('\t')[0]) for line in out.splitlines() if '\t' in line and str(folder) in line]


def run(agent, folder, with_viewer=False, model=None):
    folder.mkdir(parents=True, exist_ok=True)
    work = folder / 'workspace'
    work.mkdir(exist_ok=True)
    (work / 'calc.py').write_text('def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n',
                                  encoding='utf-8')
    control = Controller(Store('format-' + uuid.uuid4().hex, work, folder / 'state'), agent=agent)
    result = dict(agent=agent, host=host.current(), problems=[])
    try:
        control.frontend(agent)
        if model:
            control.activate(model, adapters.module(agent).DEFAULTS['access'], agent=agent)
        else:
            control.bind(agent, require_hooks=False, confirm=not host.views())
        control.mode('passthrough')
        if with_viewer:
            viewer.save(control.store.root, 'on')
        began = time.monotonic()
        done = {}

        def closer():  # L3: close the window mid-turn; the turn must not notice.
            until = time.monotonic() + 60
            while time.monotonic() < until and not done:
                found = viewer_processes(control.store.root)
                if found:
                    time.sleep(3)
                    result['viewersSeen'] = len(found)
                    for pid in found:
                        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
                    result['viewerClosedMidTurn'] = True
                    return
                time.sleep(.5)
        if with_viewer:
            import threading
            thread = threading.Thread(target=closer, daemon=True)
            thread.start()
        sent = control.send(PROMPT, output=lambda event: None)
        done['yes'] = True
        result['turnSeconds'] = round(time.monotonic() - began, 1)
        result['status'] = (control.store.read().get('requests') or {}).get(sent['requestId'], {}).get('status')
        summary = capture(sent['events'], folder / 'capture', agent,
                          shells=[s for s in (shutil.which('pwsh'),
                                              r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe') if s])
        result['capture'] = summary
        missing = [key for key, ok in summary['answer'].items() if not ok]
        if missing:
            result['notes'] = ['answer lacks: ' + ', '.join(missing)]
        if not summary['verbatim']['r3']:
            result['problems'].append('R3 does not carry the final words verbatim')
        if summary['profileMismatches']:
            result['problems'].append('emissions differ from the known profile: ' + ', '.join(summary['profileMismatches']))
        if with_viewer:
            if not result.get('viewerClosedMidTurn'):
                result['problems'].append('no viewer window opened for the turn')
            elif result.get('viewersSeen') != 1:
                result['problems'].append('%s viewer windows for one session' % result.get('viewersSeen'))
            if result['status'] != 'completed':
                result['problems'].append('turn did not complete after the viewer closed: ' + str(result['status']))
            # The next turn reopens it.
            control.send('Reply with only the word again.', output=lambda event: None)
            result['reopened'] = bool(viewer_processes(control.store.root))
            if not result['reopened']:
                result['problems'].append('the next turn did not reopen the viewer')
    except Exception as exc:
        result['problems'].append('error: ' + type(exc).__name__ + ': ' + str(exc)[:400])
    finally:
        stopped = control.off()
        result['shutdownComplete'] = stopped['shutdownComplete']
        if with_viewer:
            viewer.save(control.store.root, 'off')
            time.sleep(3)  # The window polls its settings about once a second.
            left = viewer_processes(control.store.root)
            result['viewersAfterStop'] = len(left)
            if left:
                result['problems'].append('viewer process left after stop: ' + str(left))
                for pid in left:
                    subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
    (folder / 'result.json').write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agents', nargs='+', required=True, choices=adapters.implemented())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--viewer', action='store_true', help='Run the turn with /cli view on (7c).')
    parser.add_argument('--host', choices=host.HOSTS, default=host.CODEX)
    parser.add_argument('--capture', nargs=2, metavar=('EVENTS', 'FOLDER'), help='Only render an existing events file.')
    parser.add_argument('--shell', help='Open the viewer with this PowerShell (L5). Hiding pwsh from PATH does not '
                                        'work: bind reloads PATH from the registry.')
    args = parser.parse_args()
    host.select(args.host)
    if args.shell:
        viewer.shell = lambda: args.shell
    if args.capture:
        print(json.dumps(capture(args.capture[0], args.capture[1], args.agents[0]), ensure_ascii=False))
        return 0
    failed = False
    for agent in args.agents:
        result = run(agent, args.output / agent, args.viewer)
        failed |= bool(result['problems'])
        print(json.dumps({key: value for key, value in result.items() if key != 'capture'} |
                         dict(answer=(result.get('capture') or {}).get('answer'),
                              events=(result.get('capture') or {}).get('events')), ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
