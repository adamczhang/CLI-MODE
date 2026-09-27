"""Live check of Claude Code's DIRECT and AUTO modes (phase 1): one real session, the way the desktop app runs it.

This spends quota: Claude Code's model posts each of CLI-MODE's replies (the default chat display), and the
agents use their own: each start sends one readiness prompt, and one /d task runs in DIRECT. It uses your
Claude Code sign-in, but loads the unpacked build (dist/claude-dev/cli-mode) for this session only; it installs
and enables nothing. Your saved AUTO choice for that build is set aside for the run and put back after it.
Run one Claude-using check at a time: two processes refreshing the same sign-in can sign the CLI out.

One headless session with stream-json input stays open for every step, as the desktop app keeps a
conversation open, in a small git project:

 1. /cli mode: the Mode page, Now: AUTO (the default).   2. B: /cli's agent list, marked for AUTO (none saved).
 3. <the AUTO agent's number>: its page.   4. 1: it starts in the hook (no command, no pane row); the card
    says AUTO is on, and the choice is saved.
 5. /d alone: asks for a question (in AUTO, /d asks Claude itself); nothing captured.   6. /cli spawn <backup>:
    refused.
 7. An ordinary question: Claude answers it; nothing reaches an agent.
 8. /cli: the Mode page, Now: AUTO.   9-12. 3, 2, <number>, 1: the backup starts and waits; the AUTO agent
    stays current.
13. /cli mode direct, then /d <task>: the agent's answer comes back through the background follow, as before.
14. /cli mode auto: both agents already run; nothing starts again.
15. /cli mode backup none: the backup closes.
16. /cli off: AUTO's agents close; the mode stays AUTO.   17. /cli, 1: the saved AUTO agent (offered first) starts
    again, in the hook.
18. /cli off.

    python scripts/package_plugin.py
    python checks/claude_auto_mode_live.py [--agent codex] [--backup grok-build] [--model <id>] [--keep]

--complex runs the light delegation's live check instead: a multi-part feature, a self-contained simulation and a
mixed small request, in one AUTO conversation, with Claude's own use measured per prompt (wake-ups included).
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
# P10's task: a real piece of work (code and tests), the same for Claude alone and for AUTO.
COMPARE_TASK = ('In this project: fix the failing parser test (the tests are right), then add parse_pairs(text) to '
                'app/parser.py, returning a dict of key=value words (values may be quoted), and parse_flags(text), '
                'returning (words, flags) where flags are the words starting with --. Add tests for both to '
                'tests/test_parser.py. All tests must pass. Do not commit.')
BUG = ('The parser drops the last word of its input, and tests/test_parser.py fails because of it. Please get it '
       'fixed; the tests are right.')
# Light delegation (--complex): a multi-part feature, a self-contained computation, and a mixed request whose small
# edit and question Claude keeps. Each is one prompt in the same AUTO conversation.
COMPLEX = (
    ('feature', True, 'Build a small command-line front end for this notes app, as one piece of work: fix the failing '
                      'parser test (the tests are right); add parse_pairs(text) to app/parser.py, returning a dict of '
                      'key=value words (values may be quoted), and parse_flags(text), returning (words, flags) where '
                      'flags are the words starting with --; add app/cli.py with main(argv), which prints the words, '
                      'pairs and flags of its arguments as JSON; add tests for all of it in tests/; and add a short '
                      '"Command line" section to README.md. All tests must pass. Do not commit.'),
    ('simulation', True, 'A guild economy question. Our 150 siege golems run on Mana Crystals: 45 gold each, 8 crystals '
                         'per dungeon run, 4 runs a day, for a 90-day season. Should we retrofit them to Steam Cores or '
                         'Alchemical Biomass? Steam Cores lose 0.5% heat efficiency every run and need new gaskets at '
                         'run 120, and add 800 lb, which cuts the loot a golem can carry out. Biomass injectors corrode, '
                         'using 4% more fuel every 20 runs; the tanks add 250 lb and fail 5% of the time in boss fights. '
                         'Write and run a simulation script (standard library only, in the agent\'s working folder, not '
                         'the project) with a 500-round price-spike roll: the gold threshold where Mana stays cheaper, '
                         'and which setup risks bankruptcy if material prices rise 30%. Choose and state any missing '
                         'numbers.'),
    ('mixed', False, 'Two quick things: in README.md, change "short notes" to "short notes and tags" (only that); and '
                     'tell me in one sentence what parse() does with a quoted phrase.'),
)
WORKING = ('captured', 'submitting')


def project(buggy=False):
    """A small git project, like the ones CLI-MODE is used in. `buggy`: the parser drops its last word, and its
    tests say so (the handoff scenario's task)."""
    workspace = Path(tempfile.mkdtemp(prefix='cli-mode-auto-')).resolve()
    (workspace / 'app').mkdir()
    (workspace / 'README.md').write_text('# Notes app\n\nThe project codename is ' + MARKER + '. It keeps short '
                                         'notes and a settings page.\n', encoding='utf-8')
    (workspace / 'app' / 'parser.py').write_text(
        '"""Split a command line into words, keeping quoted phrases together."""\n\n\n'
        'def parse(text):\n    words, current, quoted = [], \'\', False\n    for char in text:\n'
        '        if char == \'"\':\n            quoted = not quoted\n        elif char == \' \' and not quoted:\n'
        '            if current:\n                words.append(current)\n            current = \'\'\n'
        '        else:\n            current += char\n' +
        ('    return words\n' if buggy else '    return words + ([current] if current else [])\n'),
        encoding='utf-8')
    if buggy:
        (workspace / 'app' / '__init__.py').write_text('', encoding='utf-8')
        (workspace / 'conftest.py').write_text('', encoding='utf-8')
        (workspace / 'tests').mkdir()
        (workspace / 'tests' / 'test_parser.py').write_text(
            'from app.parser import parse\n\n\ndef test_last_word_is_kept():\n'
            '    assert parse(\'tag urgent\') == [\'tag\', \'urgent\']\n\n\ndef test_quoted_phrase():\n'
            '    assert parse(\'open "my notes" now\') == [\'open\', \'my notes\', \'now\']\n', encoding='utf-8')
        (workspace / '.gitignore').write_text('__pycache__/\nAgent_Working_Folder/\n', encoding='utf-8')
    for command in (['init', '-q'], ['add', '-A'],
                    ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Notes app']):
        subprocess.run(['git', *command], cwd=workspace, check=True)
    return workspace


class Run:
    def __init__(self, agent, backup, model, buggy=False, extra=()):
        self.agent, self.backup = agent, backup
        self.workspace = project(buggy)
        self.host = Session(self.workspace, model, extra)
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
        self.step('mode page', '/cli mode', ('You drive the agents with', 'Now: AUTO', '2. AUTO (default)'))
        self.check('mode page', (self.state().get('pending') or {}).get('phase') == 'mode', 'no Mode page open')
        self.step('agent list without a choice', 'b', ('Select AUTO Agent', 'M. Mode: AUTO'))
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
        self.step('/d without a task', '/d', ('Add a question after /d',))
        self.check('/d without a task', not self.state().get('requests'), 'a request was captured')
        self.step('spawn refused', '/cli spawn ' + self.backup, ('In AUTO, Claude and CLI-MODE run the agents',))
        _, answer = self.step('ordinary question', QUESTION, tools=True)
        self.check('ordinary question', 'word' in answer.lower() or 'quot' in answer.lower(),
                   'the answer does not describe the parser: ' + answer[:200])
        self.check('ordinary question', not self.state().get('requests'), 'it reached an agent')
        self.step('/cli in AUTO', '/cli', ('Now: AUTO', agent_label))
        self.step('AUTO settings', '3', ('1. AUTO agent', '2. Backup agent', 'Delegation: Strong'))
        self.step('backup picker', '2', ('Select Backup Agent', 'Choose your backup agent'))
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
        self.check('off closes AUTO agents', not state.get('active') and state.get('routingMode') == 'auto'
                   and 'auto' not in state, json.dumps(dict(active=state.get('active'), mode=state.get('routingMode'))))
        self.step('saved agent first', '/cli', ('1 starts your saved AUTO agent.', 'Select AUTO Agent'))
        self.step('auto from the saved choice', '1', ('CLI-MODE Activated', 'AUTO is on: Claude hands work to'),
                  timeout=400)
        state = self.state()
        self.check('started from the saved choice', state.get('active') and len(state['owned']) == 1
                   and state['owned'][0]['backend'] == self.agent, json.dumps([o['backend'] for o in state['owned']]))
        self.step('final off', '/cli off', ('CLI-MODE is off. The agent session was closed.',))

    def handoff_main(self):
        """Phase 2-3, end to end: AUTO on from the saved choice, then a bug report in plain words. Claude should hand
        it off (a task file, handoff, its follow) without editing the project, be woken when the agent finishes, read
        the result with relay --for-host, and report; the tests then pass."""
        import auto_mode
        auto_mode.save(claude_data(), {'agent': {'agent': self.agent, 'model': None, 'effort': None, 'access': None},
                                       'backup': None, 'strength': 'strong'})
        self.use_defaults()
        self.step('auto on', '/cli mode auto', ('AUTO is on: Claude hands work to',), timeout=400)
        count, mark = len(self.host.results()), self.host.mark()
        started = time.monotonic()
        self.host.send(BUG)
        if not self.host.wait(count + 1, 600):
            raise RuntimeError('the bug report: no result')

        def read():
            requests = (self.state().get('requests') or {}).values()
            return any(record.get('routingMode') == 'auto' and record.get('hostRead') for record in requests)
        if not self.host.wait(None, 1500, done=read):
            raise RuntimeError('the handoff result was never read (relay --for-host)')
        if not self.host.wait(None, 300, done=lambda: len(self.host.results()) >= count + 2):
            raise RuntimeError('the wake-up turn never ended')
        time.sleep(3)  # Let the wake-up turn's last events arrive.
        with self.host.lock:
            events = self.host.events[mark:]
        tools = [(block.get('name'), block.get('input') or {}) for event in events if event.get('type') == 'assistant'
                 for block in (event.get('message') or {}).get('content') or [] if block.get('type') == 'tool_use']
        commands = [str(data.get('command') or '') for name, data in tools if name in ('Bash', 'PowerShell')]
        writes = [str(data.get('file_path') or '') for name, data in tools if name in ('Write', 'Edit', 'MultiEdit')]
        rows = [event.get('description') for event in events if event.get('type') == 'system'
                and event.get('subtype') == 'task_started']
        texts = [self.plain(event.get('result') or '') for event in events if event.get('type') == 'result']
        passing = any('Passing to' in self.plain(block.get('text') or '') for event in events
                      if event.get('type') == 'assistant' for block in (event.get('message') or {}).get('content') or []
                      if block.get('type') == 'text')
        record = next(record for record in (self.state().get('requests') or {}).values()
                      if record.get('routingMode') == 'auto')
        self.steps.append(dict(name='handoff', prompt=BUG, seconds=round(time.monotonic() - started, 1),
                               tools=[(name, str(data.get('command') or data.get('file_path') or '')[-100:])
                                      for name, data in tools], rows=rows, reply=(texts[-1] if texts else '')[:900]))
        self.check('passing line', passing, 'no "Passing to" line')
        self.check('task file', any('/.cli-mode/tasks/' in path.replace('\\', '/') for path in writes),
                   'no task file written: ' + json.dumps(writes))
        self.check('no own project edits', all('Agent_Working_Folder' in path for path in writes),
                   'Claude edited the project: ' + json.dumps(writes))
        self.check('handoff command', any(' handoff --task ' in command for command in commands), json.dumps(commands))
        self.check('follow in the background', any(' follow --request ' in command for command in commands) and
                   len(rows) == 1, json.dumps(rows))
        self.check('read with --for-host', any(' --for-host' in command for command in commands), json.dumps(commands))
        self.check('no polling', not any(name in ('Monitor', 'ScheduleWakeup', 'CronCreate') for name, _ in tools),
                   json.dumps([name for name, _ in tools]))
        self.check('handoff record', (record.get('handoff') or {}).get('task') and record.get('status') == 'completed',
                   json.dumps(dict(handoff=record.get('handoff'), status=record.get('status'))))
        tests = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'], cwd=self.workspace,
                               capture_output=True, text=True, timeout=120)
        self.check('the fix works', tests.returncode == 0, tests.stdout[-300:])
        self.check('report', texts and len(texts[-1]) > 40, 'no report after the wake-up')
        self.step('off', '/cli off', ('CLI-MODE is off.',))

    def claude_use(self, since):
        """Claude's own token use in this session's results after event `since` (P10: host usage, not the agent's)."""
        with self.host.lock:
            results = [event for event in self.host.events[since:] if event.get('type') == 'result']
        total = dict(input=0, output=0, cacheWrite=0, cacheRead=0, turns=0)
        for event in results:
            usage = event.get('usage') or {}
            total['input'] += usage.get('input_tokens') or 0
            total['output'] += usage.get('output_tokens') or 0
            total['cacheWrite'] += usage.get('cache_creation_input_tokens') or 0
            total['cacheRead'] += usage.get('cache_read_input_tokens') or 0
            total['turns'] += event.get('num_turns') or 0
        with self.host.lock:
            before = [event.get('total_cost_usd') or 0 for event in self.host.events[:since]
                      if event.get('type') == 'result']
        costs = [event.get('total_cost_usd') or 0 for event in results]
        # A relative measure only (an API list-price equivalent, not a charge). Whether each result's figure is the
        # session's running total or its own turn's, the raw values are kept to tell.
        total['listCostPerResult'] = costs
        total['listCostDelta'] = round((costs[-1] if costs else 0) - (before[-1] if before else 0), 4)
        return total

    def wait_read(self, count, timeout=1500):
        """Until an AUTO result has been read (relay --for-host) and the turn that read it has ended."""
        def read():
            return any(record.get('routingMode') == 'auto' and record.get('hostRead')
                       for record in (self.state().get('requests') or {}).values())
        if not self.host.wait(None, timeout, done=read):
            raise RuntimeError('the handoff result was never read')
        if not self.host.wait(None, 300, done=lambda: len(self.host.results()) >= count + 2):
            raise RuntimeError('the wake-up turn never ended')
        time.sleep(3)

    def interrupt_main(self):
        """P7: a handoff's result still comes to Claude after /cli mode direct; /cli off stops a running handoff."""
        import auto_mode
        auto_mode.save(claude_data(), {'agent': None, 'backup': None, 'strength': 'strong'})
        self.use_defaults()
        self.step('auto on', '/cli mode auto', ('AUTO is on',), timeout=400)
        count = len(self.host.results())
        self.host.send(BUG)
        if not self.host.wait(count + 1, 600):
            raise RuntimeError('the bug report: no result')
        working = [key for key, record in (self.state().get('requests') or {}).items()
                   if record.get('routingMode') == 'auto']
        self.check('handed off', bool(working), 'no handoff was captured')
        self.step('ledger while working', '/cli list', ('AUTO handoffs:',), tools=False)
        self.step('direct mid-handoff', '/cli mode direct', ('DIRECT is on',))
        self.wait_read(count + 2)  # Results so far: the task turn, /cli list and /cli mode direct.
        with self.host.lock:
            commands = [str((block.get('input') or {}).get('command') or '') for event in self.host.events
                        if event.get('type') == 'assistant' for block in (event.get('message') or {}).get('content') or []
                        if block.get('type') == 'tool_use']
        self.check('read in DIRECT', any(' --for-host' in command for command in commands), 'no relay --for-host')
        self.check('no verbatim relay', not any(' relay --request ' in command and '--for-host' not in command
                                                for command in commands), 'a user relay ran for the handoff')
        self.step('auto again', '/cli mode auto', ('AUTO is on',))
        count = len(self.host.results())
        self.host.send('Add a --reverse option to the parser module: a function parse_reversed(text) returning the '
                       'words in reverse order, with tests. Hand it to the agent.')
        if not self.host.wait(count + 1, 600):
            raise RuntimeError('the second task: no result')
        self.step('emergency off', '/cli off', ('CLI-MODE is off',))
        state = self.state()
        self.check('off stops everything', not state.get('active') and not state.get('owned') and
                   not any(record.get('status') in ('captured', 'submitting')
                           for record in (state.get('requests') or {}).values()),
                   json.dumps(dict(active=state.get('active'), owned=len(state.get('owned') or []))))

    def compare_main(self, task):
        """P10: one task done by Claude alone (DIRECT, no agent) and handed off in AUTO, in separate projects.
        Returns Claude's own use for each; the agent's work is on its own plan."""
        mark = self.host.mark()
        count = len(self.host.results())
        self.host.send(task)
        if not self.host.wait(count + 1, 1200):
            raise RuntimeError('Claude alone: no result')
        alone = self.claude_use(mark)
        tests = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'], cwd=self.workspace,
                               capture_output=True, text=True, timeout=120)
        return alone, tests.returncode == 0

    def compare_auto(self, task):
        """P10's AUTO side: the same task, handed off; Claude's use counts the task turn and the wake-up turn."""
        import auto_mode
        auto_mode.save(claude_data(), {'agent': None, 'backup': None, 'strength': 'strong'})
        self.use_defaults()
        self.step('auto on', '/cli mode auto', ('AUTO is on',), timeout=400)
        mark, count = self.host.mark(), len(self.host.results())
        self.host.send(task)
        if not self.host.wait(count + 1, 600):
            raise RuntimeError('AUTO: no result')
        self.wait_read(count)
        use = self.claude_use(mark)
        tests = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'], cwd=self.workspace,
                               capture_output=True, text=True, timeout=120)
        self.step('off', '/cli off', ('CLI-MODE is off',))
        return use, tests.returncode == 0

    def auto_requests(self):
        return {key: record for key, record in (self.state().get('requests') or {}).items()
                if record.get('routingMode') == 'auto'}

    def idle(self):
        """True between turns: the last result comes after the last assistant or tool event."""
        with self.host.lock:
            events = list(self.host.events)
        last_result = max((index for index, event in enumerate(events) if event.get('type') == 'result'), default=-1)
        last_turn = max((index for index, event in enumerate(events) if event.get('type') in ('assistant', 'user')),
                        default=-1)
        return last_result > last_turn

    def settle(self, name, before, timeout=2400):
        """Until the handoffs this prompt made have finished and been read, and Claude's last turn has ended (a
        wake-up may hand off a follow-up, so it must stay so for a while)."""
        def done():
            records = [record for key, record in self.auto_requests().items() if key not in before]
            return self.idle() and not any(record.get('status') in WORKING or not record.get('hostRead')
                                           for record in records)
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if not self.host.wait(None, max(1, until - time.monotonic()), done=done):
                break
            time.sleep(20)
            if done():
                return
        raise RuntimeError(name + ': a handoff never finished, or its result was never read')

    def tool_calls(self, since):
        with self.host.lock:
            return [(block.get('name'), str((block.get('input') or {}).get('command') or
                                            (block.get('input') or {}).get('file_path') or ''))
                    for event in self.host.events[since:] if event.get('type') == 'assistant'
                    for block in (event.get('message') or {}).get('content') or [] if block.get('type') == 'tool_use']

    def replies(self, since):
        with self.host.lock:
            return [self.plain(event.get('result') or '') for event in self.host.events[since:]
                    if event.get('type') == 'result']

    def complex_main(self):
        """Light delegation, live (L1-L8): complex prompts in one AUTO conversation. A handoff is one call (Claude
        types no follow and no relay), each result comes with its wake-up and is read there, the work holds up, and
        the small mixed request stays with Claude. Claude's own use is measured per prompt, wake-ups included."""
        import auto_mode
        auto_mode.save(claude_data(), {'agent': {'agent': self.agent, 'model': None, 'effort': None, 'access': None},
                                       'backup': None, 'strength': 'strong'})
        self.use_defaults()
        self.step('auto on', '/cli mode auto', ('AUTO is on: Claude hands work to',), timeout=400)
        self.measures, said_by = [], {}  # The report keeps each reply's end; the checks read all of it.
        for name, hands_off, prompt in COMPLEX:
            before, mark, count = set(self.auto_requests()), self.host.mark(), len(self.host.results())
            started = time.monotonic()
            self.host.send(prompt)
            if not self.host.wait(count + 1, 900):
                raise RuntimeError(name + ': no result')
            self.settle(name, before)
            made = [key for key in self.auto_requests() if key not in before]
            tools = self.tool_calls(mark)
            said = said_by[name] = '\n'.join(self.replies(mark))
            typed = [command for tool, command in tools if tool in ('Bash', 'PowerShell')]
            measure = dict(name=name, minutes=round((time.monotonic() - started) / 60, 1), handoffs=len(made),
                           claude=self.claude_use(mark), toolCalls=len(tools),
                           handoffCalls=sum(' handoff ' in command for command in typed),
                           followOrRelayTyped=sum(' follow --request ' in command or ' relay --request ' in command
                                                  for command in typed),
                           otherTools=[(tool, command[-80:]) for tool, command in tools
                                       if ' handoff ' not in command and '/.cli-mode/tasks/' not in
                                       command.replace('\\', '/')],
                           reply=said[-900:])
            self.measures.append(measure)
            self.check(name + ': handed off' if hands_off else name + ': kept by Claude',
                       bool(made) == hands_off, str(len(made)) + ' handoffs')
            self.check(name + ': one call per handoff', not measure['followOrRelayTyped'],
                       'Claude typed a follow or relay command')
            self.check(name + ': read in the wake-up', 'autoWake' not in self.state(), 'a result waits to be read')
        tests = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'], cwd=self.workspace,
                               capture_output=True, text=True, timeout=120)
        self.check('feature: tests pass', tests.returncode == 0, (tests.stdout or tests.stderr)[-300:])
        self.check('feature: cli.py', (self.workspace / 'app' / 'cli.py').is_file(), 'no app/cli.py')
        self.check('simulation: the baseline', any(figure in said_by['simulation'] for figure in ('19,440,000', '19.44')),
                   'the report lacks the 19,440,000-gold baseline')
        readme = (self.workspace / 'README.md').read_text(encoding='utf-8')
        self.check('mixed: the small edit', 'short notes and tags' in readme, 'README.md unchanged')
        self.step('off', '/cli off', ('CLI-MODE is off',))

    def use_defaults(self):
        """Fill the saved AUTO choice with the agent's own defaults (as the picker's "Yes" would)."""
        sys.path.insert(0, str(DEV / 'scripts'))
        import adapters
        import auto_mode
        config = auto_mode.load(claude_data())
        adapter = adapters.module(self.agent)
        selected = adapter.selection(claude_data(), **adapter.DEFAULTS)
        config['agent'] = auto_mode.entry_of(self.agent, selected)
        auto_mode.save(claude_data(), config)

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
    parser.add_argument('--handoff', action='store_true', help='The end-to-end handoff scenario instead.')
    parser.add_argument('--interrupt', action='store_true', help='P7: a mode switch and /cli off during handoffs.')
    parser.add_argument('--compare', choices=['alone', 'auto'], help='P10: one side of Claude alone versus AUTO.')
    parser.add_argument('--complex', action='store_true', help='Light delegation: complex prompts in one conversation.')
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
    # P10: both sides may edit files and run commands without asking, so they differ only in who does the work.
    # --complex: Claude makes the mixed request's small edit itself.
    extra = (['--permission-mode', 'acceptEdits', '--allowedTools', 'Bash', 'PowerShell']
             if args.compare or args.complex else [])
    run = Run(args.agent, args.backup, args.model,
              buggy=args.handoff or args.interrupt or bool(args.compare) or args.complex, extra=extra)
    report = dict(agent=args.agent, backup=args.backup, workspace=str(run.workspace))
    started = time.monotonic()
    try:
        if args.compare:
            use, passed = (run.compare_main if args.compare == 'alone' else run.compare_auto)(COMPARE_TASK)
            report.update(claudeUse=use, testsPass=passed)
            run.check('tests pass', passed, 'the task left failing tests')
        elif args.interrupt:
            run.interrupt_main()
        elif args.complex:
            run.complex_main()
        else:
            run.handoff_main() if args.handoff else run.main()
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
    report.update(session=run.session, steps=run.steps, measures=getattr(run, 'measures', None),
                  minutes=round((time.monotonic() - started) / 60, 1), fiveHourPercent=usage(events),
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
