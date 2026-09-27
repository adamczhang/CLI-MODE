"""Live check of Claude Code's DIRECT and AUTO modes (phase 1): one real session, the way the desktop app runs it.

This spends quota: Claude Code's model posts each of CLI-MODE's replies (the default chat display), and the
agents use their own: each start sends one readiness prompt, and one /d task runs in DIRECT. It uses your
Claude Code sign-in, but loads the unpacked build (dist/claude-dev/cli-mode) for this session only; it installs
and enables nothing. Your saved AUTO choice for that build is set aside for the run and put back after it.
Run one Claude-using check at a time: two processes refreshing the same sign-in can sign the CLI out.

One headless session with stream-json input stays open for every step, as the desktop app keeps a
conversation open, in a small git project:

 1. /cli mode: the Mode page, Now: DIRECT.
 2. 2 (AUTO) with no AUTO agent saved: the agent picker, marked for AUTO.
 3. <the AUTO agent's number>: its page.   4. 1: it starts in the hook (no command, no pane row); the card
    says AUTO is on, and the choice is saved.
 5. /d <task>: refused in AUTO, nothing captured.   6. /cli spawn <backup>: refused.
 7. An ordinary question: Claude answers it; nothing reaches an agent.
 8. /cli: the Mode page, Now: AUTO.   9-12. 3, 2, <number>, 1: the backup starts and waits; the AUTO agent
    stays current.
13. /cli mode direct, then /d <task>: the agent's answer comes back through the background follow, as before.
14. /cli mode auto: both agents already run; nothing starts again.
15. /cli mode backup none: the backup closes.
16. /cli off: AUTO ends with its agents.   17. /cli mode auto: the saved AUTO agent starts again, in the hook.
18. /cli off.

    python scripts/package_plugin.py
    python checks/claude_auto_mode_live.py [--agent codex] [--backup grok-build] [--model <id>] [--keep]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from claude_background_live import LABELS, Session, relayed, saved_state, summary
from claude_live_relay import DEV, MARKER, claude_data

QUESTION = 'In one sentence: what does app/parser.py do?'
TASK = 'Read README.md in this folder and reply with the project codename only.'


def project():
    """A small git project, like the ones CLI-MODE is used in."""
    workspace = Path(tempfile.mkdtemp(prefix='cli-mode-auto-')).resolve()
    (workspace / 'app').mkdir()
    (workspace / 'README.md').write_text('# Notes app\n\nThe project codename is ' + MARKER + '. It keeps short '
                                         'notes and a settings page.\n', encoding='utf-8')
    (workspace / 'app' / 'parser.py').write_text(
        '"""Split a command line into words, keeping quoted phrases together."""\n\n\n'
        'def parse(text):\n    words, current, quoted = [], \'\', False\n    for char in text:\n'
        '        if char == \'"\':\n            quoted = not quoted\n        elif char == \' \' and not quoted:\n'
        '            if current:\n                words.append(current)\n            current = \'\'\n'
        '        else:\n            current += char\n    return words + ([current] if current else [])\n',
        encoding='utf-8')
    for command in (['init', '-q'], ['add', '-A'],
                    ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Notes app']):
        subprocess.run(['git', *command], cwd=workspace, check=True)
    return workspace


class Run:
    def __init__(self, agent, backup, model):
        self.agent, self.backup = agent, backup
        self.workspace = project()
        self.host = Session(self.workspace, model)
        self.session, self.steps, self.problems = None, [], []
        sys.path.insert(0, str(DEV / 'scripts'))
        from presentation import plain_strong
        self.plain = plain_strong

    def state(self):
        return saved_state(self.session, self.workspace)

    def step(self, name, prompt, expect=(), timeout=150, tools=False):
        """Send one prompt, wait for its turn to end, and check it: expected words, and no tools or pane rows
        unless `tools` (CLI-MODE's own replies need neither: they are posted as given)."""
        mark, count = self.host.mark(), len(self.host.results())
        started = time.monotonic()
        self.host.send(prompt)
        if not self.host.wait(count + 1, timeout):
            raise RuntimeError(name + ': no result within ' + str(timeout) + ' s')
        with self.host.lock:
            events = self.host.events[mark:]
            if self.session is None:
                self.session = next(event.get('session_id') for event in self.host.events if event.get('session_id'))
        turn = summary(events)
        text = self.plain(turn['result'])
        record = dict(name=name, prompt=prompt, seconds=round(time.monotonic() - started, 1), turns=turn['turns'],
                      tools=[(tool['name'], (tool['command'] or tool['path'] or '')[-90:]) for tool in turn['tools']],
                      rows=[task['description'] for task in turn['tasks'] if task['subtype'] == 'task_started'],
                      reply=text[:700])
        self.steps.append(record)
        for phrase in expect:
            if phrase not in text:
                self.problems.append(name + ': the reply lacks "' + phrase + '"')
        if turn['error']:
            self.problems.append(name + ': the turn ended with an error')
        if not tools and (record['tools'] or record['rows']):
            self.problems.append(name + ': Claude ran ' + json.dumps(record['tools'] + record['rows'])[:200])
        return record, text

    def check(self, name, condition, detail=''):
        if not condition:
            self.problems.append(name + (': ' + detail if detail else ''))

    def pick(self, agent):
        """The picker's number for `agent` (its saved choices)."""
        choices = (self.state().get('pending') or {}).get('choices') or []
        return str([choice['value'] for choice in choices].index(agent) + 1)

    def label(self, agent):
        return LABELS.get(agent, agent)

    def main(self):
        agent_label = self.label(self.agent)
        self.step('mode page', '/cli mode', ('You drive the agents with', 'Now: DIRECT', '2. AUTO'))
        self.check('mode page', (self.state().get('pending') or {}).get('phase') == 'mode', 'no Mode page open')
        self.step('auto without a choice', '2', ('Select CLI Agent', 'Choose your AUTO agent', 'M. Mode: DIRECT'))
        self.check('picker', (self.state().get('pending') or {}).get('purpose') == 'auto-on', 'not marked for AUTO')
        self.step('agent page', self.pick(self.agent), ('1. Yes',))
        _, card = self.step('start the AUTO agent', '1', ('CLI-MODE Activated', '**Agent:** ' + agent_label,
                                                          'AUTO is on: Claude hands work to'),
                            timeout=400)
        state = self.state()
        roster = state.get('auto') or {}
        self.check('AUTO on', state.get('routingMode') == 'auto' and state.get('active'), json.dumps(
            dict(mode=state.get('routingMode'), active=state.get('active'))))
        self.check('AUTO agent current', roster.get('agent') == state.get('main'), json.dumps(roster))
        import auto_mode
        self.check('choice saved', (auto_mode.load(claude_data())['agent'] or {}).get('agent') == self.agent)
        self.step('/d refused', '/d ' + TASK, ('AUTO is on: tell Claude what you want',))
        self.check('/d refused', not self.state().get('requests'), 'a request was captured')
        self.step('spawn refused', '/cli spawn ' + self.backup, ('AUTO is on, so Claude and CLI-MODE run the agents',))
        _, answer = self.step('ordinary question', QUESTION, tools=True)
        self.check('ordinary question', 'word' in answer.lower() or 'quot' in answer.lower(),
                   'the answer does not describe the parser: ' + answer[:200])
        self.check('ordinary question', not self.state().get('requests'), 'it reached an agent')
        self.step('/cli in AUTO', '/cli', ('Now: AUTO', agent_label))
        self.step('AUTO settings', '3', ('1. AUTO agent', '2. Backup agent', 'Delegation: Strong'))
        self.step('backup picker', '2', ('Select CLI Agent', 'Choose your backup agent'))
        self.step('backup page', self.pick(self.backup), ('1. Yes',))
        # Live run 1: this card described the AUTO agent (the current one), not the backup it started.
        self.step('start the backup', '1', ('CLI-MODE Activated', '**Agent:** ' + self.label(self.backup),
                                            'is now your backup agent: it waits for work'),
                  timeout=400)
        state = self.state()
        roster = state.get('auto') or {}
        self.check('two agents', len(state['owned']) == 2, str(len(state['owned'])))
        self.check('AUTO agent stays current', state.get('main') == roster.get('agent'), json.dumps(roster))
        self.check('backup in the roster', roster.get('backup') not in (None, roster.get('agent')), json.dumps(roster))
        self.step('direct', '/cli mode direct', ('DIRECT is on', 'Your agents keep running'))
        self.direct_task()
        _, back = self.step('auto again', '/cli mode auto', ('AUTO is on: Claude hands work to', 'backup'))
        self.check('nothing restarted', len(self.state()['owned']) == 2, str(len(self.state()['owned'])))
        self.step('backup none', '/cli mode backup none', ('Backup removed.', 'was closed'))
        self.check('backup closed', len(self.state()['owned']) == 1, str(len(self.state()['owned'])))
        self.step('off', '/cli off', ('CLI-MODE is off. The agent session was closed.',))
        state = self.state()
        self.check('off ends AUTO', not state.get('active') and state.get('routingMode') == 'direct'
                   and 'auto' not in state, json.dumps(dict(active=state.get('active'), mode=state.get('routingMode'))))
        self.step('auto from the saved choice', '/cli mode auto', ('AUTO is on: Claude hands work to',), timeout=400)
        state = self.state()
        self.check('started from the saved choice', state.get('active') and len(state['owned']) == 1
                   and state['owned'][0]['backend'] == self.agent, json.dumps([o['backend'] for o in state['owned']]))
        self.step('final off', '/cli off', ('CLI-MODE is off. The agent session was closed.',))

    def direct_task(self):
        """/d in DIRECT after AUTO: the background follow and the wake-up relay, as before."""
        count, mark = len(self.host.results()), self.host.mark()
        self.host.send('/d ' + TASK)
        started = time.monotonic()
        if not self.host.wait(count + 1, 300):
            raise RuntimeError('/d in DIRECT: no result')
        if not self.host.wait(None, 900, done=lambda: relayed(self.session, self.workspace)):
            raise RuntimeError('/d in DIRECT: the answer was never relayed')
        if not self.host.wait(None, 150, done=self.host.relay_turn_ended):
            raise RuntimeError('/d in DIRECT: the turn that relayed the answer never ended')
        with self.host.lock:
            events = self.host.events[mark:]
        posted = [self.plain(event.get('result') or '') for event in events if event.get('type') == 'result']
        self.steps.append(dict(name='/d in DIRECT', prompt='/d ' + TASK, seconds=round(time.monotonic() - started, 1),
                               reply=posted[-1][:500] if posted else ''))
        self.check('/d in DIRECT', any(MARKER in text for text in posted), 'the codename did not come back')
        rows = [event.get('description') for event in events if event.get('type') == 'system'
                and event.get('subtype') == 'task_started']
        self.check('/d in DIRECT', len(rows) == 1, 'pane rows: ' + json.dumps(rows))


def usage(events):
    """The 5-hour window's use (percent) at the first and the last rate-limit event of the session."""
    values = []
    for event in events:
        windows = ((event.get('rate_limit_info') or {}).get('unifiedWindows') or {}) if event.get(
            'type') == 'rate_limit_event' else {}
        value = (windows.get('five_hour') or {}).get('utilization')
        if value is not None:
            values.append(round(value * 100 if value <= 1 else value, 1))
    return (values[0], values[-1]) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', default='codex')
    parser.add_argument('--backup', default='grok-build')
    parser.add_argument('--model')
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    if 'claude' in (args.agent, args.backup):
        raise SystemExit('Use agents other than Claude Code: it shares the sign-in this session uses.')
    sys.path.insert(0, str(DEV / 'scripts'))
    import host as host_module
    host_module.select(host_module.CLAUDE)
    import auto_mode
    saved = auto_mode.config_path(claude_data())
    aside = saved.with_suffix('.before-live-check')
    if saved.exists():
        saved.replace(aside)  # A first run: no AUTO agent chosen yet.
    run = Run(args.agent, args.backup, args.model)
    report = dict(agent=args.agent, backup=args.backup, workspace=str(run.workspace))
    started = time.monotonic()
    try:
        run.main()
    except (RuntimeError, ValueError, KeyError, StopIteration) as exc:
        run.problems.append('stopped: ' + str(exc))
    finally:
        run.host.close()
        if aside.exists():
            aside.replace(saved)
        elif saved.exists() and not args.keep:
            saved.unlink()
    with run.host.lock:
        events = list(run.host.events)
    report.update(session=run.session, steps=run.steps, minutes=round((time.monotonic() - started) / 60, 1), fiveHourPercent=usage(events),
                  model=next((event.get('model') for event in events if event.get('subtype') == 'init'), None),
                  problems=run.problems, passed=not run.problems)
    if run.session:
        from controller import Controller
        from state import Store
        store = Store(run.session, run.workspace, claude_data())
        if store.path.exists() and store.read().get('owned'):
            report['cleanup'] = Controller(store).off().get('shutdownComplete')
    (run.workspace / 'host-events.jsonl').write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
    (run.workspace / 'host-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not args.keep:
        shutil.rmtree(run.workspace, ignore_errors=True)
        if run.session:
            key = hashlib.sha256(run.session.encode()).hexdigest()
            for path in (claude_data() / 'sessions').glob(key + '*'):
                path.unlink(missing_ok=True)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
