"""AUTO handoffs (Claude Code): Claude writes a task file and runs `handoff` once; the hook hands it over and turns that
call into the background follow, and its end wakes Claude with the result already read (`relay --for-host` is the
fallback). The result never reaches the user's own relays."""
import os
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from test_claude_hook import ClaudeHook, SESSION, claude
from test_controller import runtime_process, runtime_result
from controller import Controller
import auto_mode
import relay_view
from dispatch import READS, approval_policy

TASK = 'Goal: add a docstring to parse().\nContext: app/parser.py.\nDo not: commit or push.\nDone when: it reads well.\n' \
       'Report: what changed.'


def agy(model='gemini-3.8-flash-high'):
    return {'agent': 'agy', 'model': model, 'effort': None, 'access': 'allow'}


class AutoBase(ClaudeHook):
    """AUTO on with a fake agent, and helpers; no tests of its own."""

    def setUp(self):
        super().setUp()
        for target in ('confirmation.usage', 'frontends.confirmed'):
            patcher = patch(target, return_value={'status': 'unavailable', 'reason': 'test'}
                            if target == 'confirmation.usage' else True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def auto(self, backup=False):
        auto_mode.save(self.data, {'agent': agy(), 'backup': agy() if backup else None, 'strength': 'strong'})
        self.prompt('/cli mode auto')
        state = self.store().read()
        self.assertEqual(state['routingMode'], 'auto')
        return state

    def write_task(self, task='t1', text=TASK):
        path = auto_mode.task_file(self.project, task)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def ctl(self, *words):
        return claude.controller(dict(session_id=SESSION, cwd=str(self.cwd)), self.data, *words)

    def pre(self, tool, **tool_input):
        event = dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse', tool_name=tool,
                     tool_use_id='toolu_' + tool.lower() + str(len(tool_input)), tool_input=tool_input)
        return claude.handle(event, self.data).get('hookSpecificOutput') or {}

    def drain(self, session):
        with self.store().edit() as state:
            state.setdefault('runners', {})[session] = dict(pid=os.getpid(), token='t', started=time.time())
        control = Controller(self.store(), self.backend)
        control.use('agy')
        with patch.object(Controller, 'PUMP_LINGER', 0):
            return control.pump('t', session)

    def stop_hook(self):
        return claude.handle(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='Stop'), self.data)

class Handoffs(AutoBase):
    def test_a_handoff_end_to_end(self):
        """The light handoff: one call hands over and becomes the follow (L2); the wake-up brings the result, read
        and checked (L3, L4); nothing holds the turn."""
        state = self.auto()
        lead = state['auto']['agent']
        path = self.write_task()
        written = self.pre('Write', file_path=str(path), content=TASK)
        self.assertEqual(written.get('permissionDecision'), 'allow')  # The task file: approved by CLI-MODE.
        short = claude.handoff_command({}, self.data).replace('<id>', 't1')  # `python <controller> handoff --task t1`
        approved = self.pre('Bash', command=short, description='x')
        state = self.store().read()
        request = next(key for key, item in state['requests'].items() if item.get('routingMode') == 'auto')
        record = state['requests'][request]
        # No Files line: the task may change any file (auto_mode.task_files).
        self.assertEqual((record['routingMode'], record['handoff']),
                         ('auto', {'task': 't1', 'readOnly': False, 'files': [auto_mode.WHOLE]}))
        self.assertEqual(record['session'], lead)
        self.assertEqual(approved['permissionDecision'], 'allow')
        self.assertEqual(approved['updatedInput']['command'], self.command('follow', '--request', request))
        self.assertTrue(approved['updatedInput']['run_in_background'])
        self.assertEqual(approved['updatedInput']['description'], self.name() + ' · add a docstring to parse().')
        self.assertEqual(state['followTasks'], {'toolu_bash2': request})  # This call's end wakes Claude.
        self.drain(lead)
        self.assertEqual(self.store().read()['requests'][request]['status'], 'completed')
        sent = self.backend.sent[-1]
        self.assertIn('Goal: add a docstring to parse().', sent)
        self.assertNotIn('/d', sent.split('\n', 1)[0])
        wake = self.context(self.prompt('<task-notification>\n<tool-use-id>toolu_bash2</tool-use-id>\n'
                                        '<status>completed</status>\n</task-notification>'))
        import presentation
        for part in (presentation.strong(self.name() + ' finished.', True), 'no relay to run', 'not the user',
                     'HANDOFF ' + request + ': ' + self.name() + ' finished.\nCHECK: ok\nTASK: ' + path.as_posix(),
                     'Goal: add a docstring to parse().'):  # The agent's answer (the fake echoes the task).
            self.assertIn(part, wake)
        self.assertNotIn('relay --request', wake)
        saved = self.store().read()
        self.assertTrue(saved['requests'][request]['hostRead'])
        self.assertNotIn('autoWake', saved)
        self.assertEqual(self.stop_hook(), {})  # Nothing left to read: the turn may end.
        self.assertNotIn(request, claude.unrelayed(saved))  # Never chained into a user's relay.
        self.assertIn('already read', self.context(self.prompt(
            '<task-notification>\n<tool-use-id>toolu_bash2</tool-use-id>\n</task-notification>')))

    def test_the_handoff_is_refused_at_once_and_the_long_form_still_works(self):
        self.auto()
        refused = self.pre('Bash', command=claude.handoff_command({}, self.data).replace('<id>', 'nope'),
                           description='x')
        self.assertEqual(refused['permissionDecision'], 'deny')  # No task file: nothing captured, said at once.
        self.assertIn('No task file', refused['permissionDecisionReason'])
        self.assertFalse(self.store().read().get('requests'))
        self.write_task()
        result = self.ctl('handoff', '--task', 't1')  # Outside the hook: prints the follow to run, as before.
        self.assertEqual(re.search(r'`([^`]+)`', result['next']).group(1),
                         self.command('follow', '--request', result['requestId']))

    def test_results_finishing_together_share_one_wake_up(self):
        self.auto()
        long_task = TASK + '\nFiles: app/one.py\n' + 'Context that runs on and on. ' * 400
        self.write_task('t1', long_task)
        first = self.ctl('handoff', '--task', 't1')['requestId']
        self.drain(self.store().read()['auto']['agent'])
        self.write_task('t2', long_task.replace('app/one.py', 'app/two.py'))
        second = self.ctl('handoff', '--task', 't2')['requestId']
        self.drain(self.store().read()['auto']['agent'])
        with self.store().edit() as saved:
            saved['followTasks'] = {'toolu_a': first, 'toolu_b': second}
        wake = self.context(self.prompt('<task-notification>\n<tool-use-id>toolu_a</tool-use-id>\n</task-notification>'
                                        '\n<task-notification>\n<tool-use-id>toolu_b</tool-use-id>\n'
                                        '</task-notification>'))
        self.assertIn('Their results are below', wake)
        self.assertIn('HANDOFF ' + first, wake)
        self.assertIn('HANDOFF ' + second, wake)
        self.assertLess(len(wake), 10000)  # Claude Code's cap: the answers share the room.
        self.assertTrue(all(record['hostRead'] for record in self.store().read()['requests'].values()))

    def test_a_result_the_wake_up_cannot_read_falls_back_to_the_relay(self):
        state = self.auto()
        self.write_task()
        request = self.ctl('handoff', '--task', 't1')['requestId']
        self.drain(state['auto']['agent'])
        with self.store().edit() as saved:
            saved['followTasks'] = {'toolu_follow': request}
        with patch.object(auto_mode.AutoMixin, 'relay_for_host', side_effect=RuntimeError('busy')):
            wake = self.context(self.prompt('<task-notification>\n<tool-use-id>toolu_follow</tool-use-id>\n'
                                            '</task-notification>'))
        self.assertIn('`' + self.command('relay', '--request', request, '--for-host') + '`', wake)
        self.assertEqual(self.store().read()['autoWake'], request)
        self.assertIn('you have not read its result', self.context(self.stop_hook()))  # The turn is held.
        self.ctl('relay', '--request', request, '--for-host')
        self.assertEqual(self.stop_hook(), {})

    def test_alongside_counts_only_work_running_at_the_same_time(self):
        now = time.time()
        state = {'requests': {
            'mine': dict(submittedAt=now - 100, endedAt=now - 10),
            'running': dict(submittedAt=now - 50, handoff=dict(files=['app/two.py'])),
            'queued': dict(capturedAt=now - 40, handoff=dict(files=['app/three.py'])),  # Never started.
            'before': dict(submittedAt=now - 500, endedAt=now - 200, touched=['app/four.py']),
            'direct': dict(submittedAt=now - 60, endedAt=now - 20, touched=['App/Five.py'],
                           handoff=dict(files=[auto_mode.WHOLE])),  # Its edits count, not a whole-project claim.
        }}
        self.assertEqual(auto_mode.alongside(state, 'mine'), ['app/five.py', 'app/two.py'])

    def test_a_neighbours_files_are_expected_not_a_check(self):
        """Two writers on separate claims (live matrix run, 2026-09-27): the other agent's files changed meanwhile
        are named as work alongside, and the verdict stays ok, so Claude spends no call on git to confirm it."""
        for name in ('app/one.py', 'app/two.py', 'app/three.py'):
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text('original\n', encoding='utf-8')
        for words in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm',
                                                      'base']):
            subprocess.run(['git'] + words, cwd=self.project, check=True, capture_output=True)
        state = self.auto()
        one = self.project / 'app/one.py'
        original = self.backend.start

        def start(owned, args, timeout=60):
            process = original(owned, args, timeout)
            if '--file' not in args:
                return process
            one.write_text('changed by this agent\n', encoding='utf-8')
            (self.project / 'app/two.py').write_text('changed by the agent alongside\n', encoding='utf-8')
            (self.project / 'app/three.py').write_text('changed by nobody known\n', encoding='utf-8')
            return runtime_process([dict(type='touched', toolCallId='call-1', kind='edit',
                                         locations=[dict(path=str(one))]),
                                    dict(type='message', text='Done: one.py.'), runtime_result()])
        self.backend.start = start
        self.write_task('t1', 'Goal: fix one.\nFiles: app/one.py')
        mine = self.ctl('handoff', '--task', 't1')['requestId']
        with self.store().edit() as saved:  # Another agent's task on app/two.py, running at the same time.
            saved['requests']['e' * 32] = dict(routingMode='auto', status='submitting', session='elsewhere',
                                               capturedAt=time.time(), submittedAt=time.time(),
                                               handoff=dict(task='t2', readOnly=False, files=['app/two.py']))
        self.drain(state['auto']['agent'])
        text = self.ctl('relay', '--request', mine, '--for-host')['text']
        self.assertIn('ALONGSIDE: app/two.py changed by other work running at the same time', text)
        self.assertIn('NOT ITS OWN EDITS: app/three.py changed', text)  # Nobody's claim: still worth a look.
        self.assertEqual(text.split('\n')[1], 'CHECK: look: files it did not edit changed while it worked.')
        self.assertNotIn('app/two.py changed while it worked', text)

    def test_a_new_agent_that_does_not_start_says_so_when_it_wakes_claude(self):
        self.auto()
        self.write_task('t2', 'Goal: fix the command line.\nFiles: app/cli.py')
        chosen = 'f' * 32
        with patch.object(auto_mode.AutoMixin, 'start_extra', side_effect=RuntimeError('The new agent did not '
                                                                                         'start.')):
            with self.assertRaisesRegex(RuntimeError, 'did not start'):
                self.ctl('handoff', '--task', 't2', '--agent', 'new', '--request', chosen, '--follow')
        with self.store().edit() as saved:
            saved['followTasks'] = {'toolu_new': chosen}
        wake = self.context(self.prompt('<task-notification>\n<tool-use-id>toolu_new</tool-use-id>\n'
                                        '</task-notification>'))
        self.assertIn('did not go through', wake)
        self.assertIn('The new agent did not start.', wake)

    def test_the_task_does_not_name_the_brief_in_auto(self):
        state = self.auto()
        import agent_folder
        brief = agent_folder.brief_path(self.project)
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text('# Project brief\n', encoding='utf-8')
        self.write_task()
        self.ctl('handoff', '--task', 't1')
        self.drain(state['auto']['agent'])
        sent = self.backend.sent[-1]
        self.assertNotIn('BRIEF', sent)
        self.assertIn('Agent_Working_Folder/', sent)  # Its working folder is still named.

    def test_a_task_file_elsewhere_is_refused_with_the_right_path(self):
        self.auto()
        wrong = self.project / '.cli-mode' / 'tasks' / 't1.md'  # Where one P0 run wrote it.
        refused = self.pre('Write', file_path=str(wrong), content=TASK)
        self.assertEqual(refused['permissionDecision'], 'deny')
        self.assertIn(auto_mode.tasks_dir(self.project).as_posix(), refused['permissionDecisionReason'])
        bad = self.pre('Write', file_path=str(auto_mode.tasks_dir(self.project) / 'no good.md'), content=TASK)
        self.assertEqual(bad['permissionDecision'], 'deny')

    def test_handoff_errors_say_what_to_do(self):
        with self.assertRaisesRegex(RuntimeError, 'AUTO is off'):
            self.ctl('handoff', '--task', 't1')
        self.auto()
        with self.assertRaisesRegex(RuntimeError, 'No task file at'):
            self.ctl('handoff', '--task', 't9')
        with self.assertRaisesRegex(ValueError, 'task id'):
            self.ctl('handoff', '--task', '../escape')
        self.write_task('empty', '   ')
        with self.assertRaisesRegex(RuntimeError, 'empty'):
            self.ctl('handoff', '--task', 'empty')
        self.write_task()
        with self.assertRaisesRegex(RuntimeError, 'No backup agent'):
            self.ctl('handoff', '--task', 't1', '--agent', 'backup')

    def test_one_writer_per_file(self):
        state = self.auto(backup=True)
        self.write_task()
        first = self.ctl('handoff', '--task', 't1')  # Not drained: still with the AUTO agent.
        with self.assertRaisesRegex(RuntimeError, 'no Files line, so it claims the whole project'):
            self.ctl('handoff', '--task', 't1', '--agent', 'backup')
        review = self.ctl('handoff', '--task', 't1', '--agent', 'backup', '--read-only')
        self.assertTrue(self.store().read()['requests'][review['requestId']]['handoff']['readOnly'])
        again = self.ctl('handoff', '--task', 't1')  # The same agent: queued behind the first, not a second writer.
        self.assertNotEqual(again['requestId'], first['requestId'])
        self.assertEqual(self.store().read()['requests'][again['requestId']]['session'], state['auto']['agent'])

    def test_writers_run_in_parallel_on_other_files(self):
        state = self.auto(backup=True)
        backup = state['auto']['backup']
        self.write_task('t1', TASK + '\nFiles: app/parser.py, tests/ (new tests)')
        first = self.ctl('handoff', '--task', 't1')['requestId']
        self.assertEqual(self.store().read()['requests'][first]['handoff']['files'], ['app/parser.py', 'tests'])
        self.write_task('t2', 'Goal: tidy the command line.\nFiles: `app/cli.py` (the entry point)\nDone when: it runs.')
        second = self.ctl('handoff', '--task', 't2', '--agent', 'backup')  # Other files: alongside the first.
        self.assertEqual(self.store().read()['requests'][second['requestId']]['session'], backup)
        self.write_task('t3', 'Goal: more tests.\nFiles: tests/test_cli.py')
        with self.assertRaisesRegex(RuntimeError, r'may be changing tests \(task t1\).*list other files'):
            self.ctl('handoff', '--task', 't3', '--agent', 'backup')
        self.ctl('handoff', '--task', 't3')  # The agent that has tests/: it waits its turn there.
        # Claude's own edits: not of a file a running task may change; any other small fix is still Claude's.
        edit = dict(old_string='x', new_string='y')
        refused = self.pre('Edit', file_path=str(self.project / 'app' / 'cli.py'), **edit)
        self.assertEqual(refused['permissionDecision'], 'deny')
        self.assertIn('may be changing app/cli.py right now (one writer per file)', refused['permissionDecisionReason'])
        self.assertEqual(self.pre('Edit', file_path=str(self.project / 'tests' / 'unit' / 'a.py'), **edit)
                         ['permissionDecision'], 'deny')  # Inside a claimed folder.
        self.assertEqual(self.pre('Edit', file_path=str(self.project / 'app' / 'other.py'), **edit), {})

    def test_new_starts_another_agent_like_the_auto_agent(self):
        from state import agent_entry
        state = self.auto()
        lead = state['auto']['agent']
        self.write_task('t1', 'Goal: fix parse().\nFiles: app/parser.py')
        self.ctl('handoff', '--task', 't1')
        self.write_task('t2', 'Goal: fix the command line.\nFiles: app/cli.py')
        command = self.command('handoff', '--task', 't2', '--agent', 'new')
        approved = self.pre('Bash', command=command, description='x')
        self.assertEqual(approved['permissionDecision'], 'allow')
        self.assertEqual(approved['updatedInput']['description'], 'Antigravity (new) · fix the command line.')
        # A start takes 15-40 s, too long for the hook: it only checks, and the background task starts and follows.
        self.assertTrue(approved['updatedInput']['run_in_background'])
        self.assertEqual(len(self.store().read()['owned']), 1)
        started = approved['updatedInput']['command']
        chosen = re.search(r'--request ([0-9a-f]{32}) --follow', started).group(1)
        self.assertEqual(started, self.command('handoff', '--task', 't2', '--agent', 'new', '--request', chosen,
                                               '--follow'))
        self.assertEqual(self.store().read()['followTasks']['toolu_bash2'], chosen)  # Its end wakes Claude.
        result = self.ctl('handoff', '--task', 't2', '--agent', 'new', '--request', chosen)  # That task, unfollowed.
        self.assertEqual((result['agent'], result['requestId']), ('Antigravity-02', chosen))
        state = self.store().read()
        extra = state['requests'][result['requestId']]['session']
        self.assertEqual(state['auto']['extras'], [extra])
        self.assertEqual(state['main'], lead)  # The AUTO agent stays current.
        self.assertEqual(agent_entry(state, extra)['settings']['model'], agent_entry(state, lead)['settings']['model'])
        self.assertEqual(agent_entry(state, extra)['timeout'], auto_mode.AUTO_TIMEOUT)
        self.assertIn('Agents now: Antigravity-01 (AUTO agent): t1 (changing app/parser.py); Antigravity-02: t2 '
                      '(changing app/cli.py).', self.context(self.prompt('how is it going?')))
        self.write_task('t3', 'Goal: restructure.\nFiles: app/')
        with self.assertRaisesRegex(RuntimeError, 'No agent was started'):  # Checked before the slow start.
            self.ctl('handoff', '--task', 't3', '--agent', 'new')
        self.assertEqual(len(self.store().read()['owned']), 2)
        with self.store().edit() as saved:
            saved['agentLimit'] = 2
        self.write_task('t4', 'Goal: write the docs.\nFiles: docs/')
        with self.assertRaisesRegex(RuntimeError, 'the limit, so no agent was started'):
            self.ctl('handoff', '--task', 't4', '--agent', 'new')
        self.prompt('/cli off')  # The extras close with the rest.
        self.assertEqual(self.store().read()['owned'], [])

    def test_the_files_line_says_what_a_task_claims(self):
        (self.project / 'Makefile').write_text('all:\n', encoding='utf-8')
        cases = {
            'Goal: x': ['*'], 'Files: none': ['*'], 'Files: the parser module': ['*'], 'Files: everything': ['*'],
            'Files: ../outside.py': ['*'],
            'Files: app/parser.py, tests/ (new tests)': ['app/parser.py', 'tests'],
            'files: `src/*.py`; README.md.': ['src', 'readme.md'],
            'Files: ./app/../lib/x.py and Makefile': ['lib/x.py', 'makefile'],
        }
        for text, expected in cases.items():
            self.assertEqual(auto_mode.task_files(text, self.project), expected, text)
        self.assertTrue(auto_mode.overlaps('src', 'src/a.py'))
        self.assertTrue(auto_mode.overlaps(auto_mode.WHOLE, 'docs'))
        self.assertFalse(auto_mode.overlaps('src/a.py', 'src/ab.py'))

    def test_read_only_handoffs_refuse_writes_and_need_an_agent_that_asks(self):
        record = {'handoff': {'readOnly': True}}
        self.assertEqual(approval_policy(record, {'settings': {'access': 'allow'}}),
                         {'autoApprove': READS, 'defaultAction': 'deny'})
        self.assertEqual(approval_policy(dict(record, approve=['execute']), {'settings': {'access': 'prompt'}}),
                         {'autoApprove': ['execute'] + READS, 'defaultAction': 'deny'})
        self.assertIsNone(approval_policy({'handoff': {'readOnly': False}}, {'settings': {'access': 'allow'}}))
        state = self.auto()
        with self.store().edit() as saved:
            for item in saved['owned']:
                item['actsWithoutAsking'] = True
        self.write_task()
        with self.assertRaisesRegex(RuntimeError, 'cannot take read-only work'):
            self.ctl('handoff', '--task', 't1', '--read-only')

    def test_claude_runs_no_agent_commands_in_its_own_turns(self):
        self.auto()
        self.prompt('please look at the parser')  # An ordinary turn: Claude's own.
        refused = self.pre('Bash', command=self.command('bind', '--agent', 'agy'), description='x')
        self.assertEqual(refused['permissionDecision'], 'deny')
        self.assertIn('handoff', refused['permissionDecisionReason'])
        self.assertEqual(self.pre('Bash', command=self.command('queue'), description='x')['permissionDecision'],
                         'allow')  # Reading stays open.
        with self.store().edit() as saved:
            saved['turnRoute'] = {'route': 'tune', 'id': 'x'}  # A turn the user started with /cli access.
        tune = self.pre('Bash', command=self.command('tune', '--phase', 'access', '--apply'), description='x')
        self.assertNotEqual(tune.get('permissionDecision'), 'deny')

    def test_an_answer_to_a_stopped_handoff_goes_on_and_comes_back_to_claude(self):
        state = self.auto()
        lead = state['auto']['agent']
        self.write_task()
        stopped = self.ctl('handoff', '--task', 't1')['requestId']
        with self.store().edit() as saved:
            saved['requests'][stopped]['status'] = 'completed'
            for item in saved['owned']:
                if item['name'] == lead:
                    item['approval'] = dict(kind='execute', title='npm install', requestId=stopped, at=time.time())
        reply = self.context(self.prompt('/cli approve'))
        saved = self.store().read()
        request = saved['turnRoute']['requestId']
        self.assertIn('goes on with your handoff (task t1)', reply)
        self.assertIn('`' + self.command('follow', '--request', request) + '`', reply)
        record = saved['requests'][request]
        self.assertEqual(record['routingMode'], 'auto')
        self.assertEqual(record['handoff'], {'task': 't1', 'readOnly': False, 'files': [auto_mode.WHOLE],
                                             'continues': stopped})  # Its files stay claimed.
        self.drain(lead)
        self.assertTrue(self.backend.sent[-1].startswith('Approved: you may'))  # Without the /d trigger.

    def test_the_plain_result_names_what_claude_must_check(self):
        batch = [dict(type='message', text='x' * (relay_view.HOST_ANSWER_MAX + 50)),
                 dict(type='error', message='Tool   failed')]
        receipt = dict(changes=dict(files=2, added=10, removed=1, paths=[dict(path='a.py'), dict(path='b.py')]),
                       tests=dict(passed=False, command='pytest -q', summary='1 failed', seconds=3),
                       refs=dict(answer='Agent_Working_Folder/AGY-1/answers/001.md'),
                       overlaps=[dict(agent='Codex COD-1', path='a.py')])
        text = relay_view.host_text('r1', 'Antigravity AGY-1', 'completed', batch, receipt, read_only=True,
                                    stopped=dict(kind='execute', title='npm install'), access={'access': 'prompt'})
        for line in ('HANDOFF r1: Antigravity AGY-1 finished (read-only).', 'CHANGES: 2 files, +10 -1: a.py, b.py',
                     'TESTS: ✗ Tests failed (pytest -q) · 1 failed · 3 s', 'OVERLAP: ⚠ Also edited by Codex COD-1',
                     'STOPPED: Antigravity AGY-1 asks to', 'ERROR: Tool failed',
                     'the whole answer is in Agent_Working_Folder/AGY-1/answers/001.md'):
            self.assertIn(line, text)
        self.assertNotIn('x' * (relay_view.HOST_ANSWER_MAX + 1), text)
        # L4: CLI-MODE's verdict, second line: only what needs a look.
        self.assertEqual(text.split('\n')[1], 'CHECK: look: tests failed; it stopped to ask permission; its turn '
                                              'reported errors; another agent edited the same files; a read-only task '
                                              'changed files.')
        self.assertNotIn('NOT ITS OWN EDITS', text)  # Not known which were its own: nothing said.
        # Several writers: the receipt holds the others' files too, named apart from its own tools' edits.
        own = relay_view.host_text('r1', 'A', 'completed', [], receipt, touched=['A.py'])
        self.assertIn('NOT ITS OWN EDITS: b.py changed while it worked, but not by its own edit tools', own)
        self.assertIn('files it did not edit changed while it worked', own.split('\n')[1])
        clean = relay_view.host_text('r1', 'A', 'completed', [dict(type='message', text='Done: parse() keeps it.')],
                                     dict(changes=dict(files=1, added=2, removed=1, paths=[dict(path='a.py')]),
                                          tests=dict(passed=True, command='pytest -q', summary='6 passed', seconds=1)),
                                     touched=['a.py'])
        self.assertEqual(clean.split('\n')[1], 'CHECK: ok')
        self.assertIn('ANSWER', relay_view.host_text('r1', 'A', 'completed', [dict(type='message', text='y' * 900)],
                                                     {}, answer_max=500))
        self.assertNotIn('y' * 501, relay_view.host_text('r1', 'A', 'completed',
                                                         [dict(type='message', text='y' * 900)], {}, answer_max=500))
        self.assertNotIn('NOT ITS OWN', relay_view.host_text('r1', 'A', 'completed', [], receipt,
                                                             touched=['a.py', 'b.py']))
        quiet = relay_view.host_text('r2', 'A', 'canceled', [], {})
        self.assertIn('was canceled', quiet)
        self.assertIn('CHANGES: not measured', quiet)
        self.assertIn('ANSWER: none.', quiet)


class Levers(AutoBase):
    """What makes Claude delegate in AUTO: the rule and ledger each turn (A), refusing its own larger edits (B), its
    own coding subagents (C), and never waiting or polling for a handoff."""

    def strength(self, value):
        config = auto_mode.load(self.data)
        config['strength'] = value
        auto_mode.save(self.data, config)

    def edit(self, path, lines=1):
        body = '\n'.join('line ' + str(index) for index in range(lines))
        return self.pre('Edit', file_path=str(path), old_string='x', new_string=body)

    def test_the_rule_comes_once_then_only_what_changed(self):
        self.assertEqual(self.prompt('hello'), {})  # No agent running yet: nothing added.
        self.auto()
        text = self.context(self.prompt('please make the parser keep the last word'))
        for part in ('CLI-MODE AUTO is on. The user turned it on', self.name(), 'Delegation is Strong',
                     '`' + claude.handoff_command({}, self.data) + '`', auto_mode.TEMPLATE,
                     auto_mode.tasks_dir(self.project).as_posix() + '/<id>.md', 'Do not guess', 'One writer per file',
                     '`--agent new`', 'one or two tool calls', 'Goal and Done when are enough',
                     'there is nothing else to run', 'CHECK: ok', 'Agents now: ' + self.name() + ' (AUTO agent): idle.'):
            self.assertIn(part, text)
        self.assertNotIn('AUTO ledger', text)  # Nothing working or unread (L5).
        self.assertLess(len(text), 10000)  # Claude Code's cap for a hook's added context.
        import presentation  # Attribution lines as DIRECT's "Passing to" line: green bold, or plain bold with color off.
        self.assertIn(presentation.strong('Passing to ' + self.name() + ':', True), text)
        self.assertIn(presentation.strong(self.name() + ' is working.', True), text)
        # L1: the rule stays in the conversation, so a turn where nothing changed gets nothing.
        self.assertEqual(self.prompt('and the tests?'), {})
        self.write_task()
        request = self.ctl('handoff', '--task', 't1')['requestId']
        working = self.context(self.prompt('is it going?'))
        self.assertTrue(working.startswith('CLI-MODE AUTO: the rule given earlier in this conversation still applies.'))
        self.assertIn('add a docstring to parse().: working.', working)
        self.assertNotIn('To hand off', working)
        self.drain(self.store().read()['auto']['agent'])
        # The wake-up normally delivers the result; if it was missed, the ledger names the relay (the fallback).
        unread = self.context(self.prompt('how did it go?'))
        self.assertIn('NOT READ YET: `' + self.command('relay', '--request', request, '--for-host') + '`', unread)
        self.ctl('relay', '--request', request, '--for-host')
        done = self.context(self.prompt('thanks'))
        self.assertNotIn('AUTO ledger', done)  # What Claude has read is already in the conversation (L5).
        self.assertIn('(AUTO agent): idle.', done)
        self.assertEqual(self.prompt('ok'), {})
        # The whole rule again when it changes, after a compaction, and every AUTO_RULE_REFRESH turns.
        self.strength('max')
        self.assertIn('Delegation is Max', self.context(self.prompt('go on')))
        self.event('SessionStart', source='compact')
        self.assertIn('To hand off', self.context(self.prompt('where were we?')))
        for _ in range(claude.AUTO_RULE_REFRESH - 1):
            self.assertEqual(self.prompt('next'), {})
        self.assertIn('To hand off', self.context(self.prompt('next')))
        self.prompt('/cli mode direct')
        self.prompt('/cli mode auto')
        self.assertIn('To hand off', self.context(self.prompt('back again')))  # Back in AUTO: the rule again.

    def test_d_in_auto_asks_claude_itself_and_nothing_goes_to_an_agent(self):
        self.auto()
        text = self.context(self.prompt('/d what does the parser do with quotes?'))
        self.assertIn('the user is asking you, not the agent', text)
        self.assertNotIn('To hand off', text)  # No delegation rule this turn.
        self.assertFalse(self.store().read().get('requests'))
        self.write_task()
        refused = self.pre('Bash', command=self.command('handoff', '--task', 't1'), description='x')
        self.assertEqual(refused['permissionDecision'], 'deny')  # Enforced, not just asked.
        self.assertEqual(self.edit(self.project / 'src' / 'a.py', lines=200), {})  # No strength limit this turn.
        self.assertEqual(self.pre('Agent', subagent_type='general-purpose', prompt='x'), {})
        self.prompt('now improve it')  # An ordinary turn again: the handoff is Claude's to choose.
        self.assertEqual(self.pre('Bash', command=self.command('handoff', '--task', 't1'),
                                  description='x')['permissionDecision'], 'allow')

    def test_strong_lets_claude_make_small_fixes_only(self):
        self.auto()
        self.prompt('fix the typo')
        source = self.project / 'src' / 'a.py'
        self.assertEqual(self.edit(source), {})
        refused = self.edit(source, lines=auto_mode.SMALL_EDIT + 5)
        self.assertEqual(refused['permissionDecision'], 'deny')
        self.assertIn('more than a small fix', refused['permissionDecisionReason'])
        self.assertEqual(self.edit(self.project / 'src' / 'b.py'), {})  # A second file.
        self.assertEqual(self.edit(self.project / 'src' / 'c.py')['permissionDecision'], 'deny')  # A third.
        self.prompt('and another small one')  # A new turn: a new count.
        self.assertEqual(self.edit(self.project / 'src' / 'c.py'), {})
        outside = self.root / 'notes.md'  # Claude's own files outside the project stay Claude's.
        self.assertEqual(self.edit(outside, lines=200), {})
        folder = self.project / 'Agent_Working_Folder' / 'notes.md'
        self.assertEqual(self.edit(folder, lines=200), {})

    def test_max_and_normal(self):
        self.auto()
        self.strength('max')
        self.assertEqual(self.edit(self.project / 'src' / 'a.py')['permissionDecision'], 'deny')
        self.strength('normal')
        self.assertEqual(self.edit(self.project / 'src' / 'a.py', lines=500), {})

    def test_no_edit_of_claudes_while_an_agent_writes(self):
        self.auto()
        self.strength('normal')
        self.write_task()
        self.ctl('handoff', '--task', 't1')  # With the agent until drained.
        refused = self.edit(self.project / 'src' / 'a.py')
        self.assertEqual(refused['permissionDecision'], 'deny')
        self.assertIn('one writer per file', refused['permissionDecisionReason'])

    def test_coding_subagents_go_to_the_agent_at_strong(self):
        self.auto()
        refused = self.pre('Agent', subagent_type='general-purpose', prompt='implement it')
        self.assertEqual(refused['permissionDecision'], 'deny')
        self.assertEqual(self.pre('Agent', subagent_type='Explore', prompt='find the parser'), {})
        self.strength('normal')
        self.assertEqual(self.pre('Agent', subagent_type='general-purpose', prompt='implement it'), {})

    def test_nothing_waits_or_polls_for_a_handoff(self):
        state = self.auto()
        self.assertEqual(self.pre('Monitor', command='x'), {})
        self.write_task()
        self.ctl('handoff', '--task', 't1')
        for tool in ('Monitor', 'ScheduleWakeup', 'CronCreate'):
            self.assertEqual(self.pre(tool, command='x')['permissionDecision'], 'deny', tool)
        self.drain(state['auto']['agent'])
        self.assertEqual(self.pre('Monitor', command='x'), {})

    def test_a_turn_that_leaves_a_handoff_unfollowed_is_told_to_follow_it(self):
        self.auto()
        self.write_task()
        request = self.ctl('handoff', '--task', 't1')['requestId']
        follow = self.command('follow', '--request', request)
        nudge = self.context(self.stop_hook())
        self.assertIn('nothing follows it', nudge)
        self.assertIn('`' + follow + '`', nudge)
        running = claude.handle(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='Stop', background_tasks=[
            dict(id='b1', type='shell', status='running', description='x', command=follow)]), self.data)
        self.assertEqual(running, {})

    def test_the_fast_path_leaves_edits_early_outside_auto(self):
        import json
        env = {'CLI_MODE_DATA': str(self.data)}

        def quick(tool, path):
            raw = json.dumps(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse', tool_name=tool,
                                  tool_input={'file_path': str(path)}))
            with patch.dict(os.environ, env):
                return claude.nothing_to_do(raw)
        self.assertTrue(quick('Edit', self.project / 'a.py'))  # CLI-MODE never used here.
        self.activate()
        self.assertTrue(quick('Write', self.project / 'a.py'))  # DIRECT.
        self.assertFalse(quick('Edit', self.project / 'Agent_Working_Folder' / 'BRIEF.md'))  # The host's note.
        self.auto()
        self.assertFalse(quick('Edit', self.project / 'a.py'))
        self.assertFalse(quick('NotebookEdit', self.project / 'a.ipynb'))


class Ledger(AutoBase):
    def test_cli_list_shows_the_handoffs_in_auto(self):
        self.activate()
        self.assertNotIn('AUTO handoffs', self.prompt('/cli list')['reason'])  # DIRECT: the agents alone.
        self.prompt('/cli off')
        state = self.auto()
        self.assertIn('AUTO handoffs:\n- No handoffs yet.', self.prompt('/cli list')['reason'])
        self.write_task()
        request = self.ctl('handoff', '--task', 't1')['requestId']
        label = self.name() + ' · add a docstring to parse().'
        self.assertIn(label + ': working.', self.prompt('/cli list')['reason'])
        self.drain(state['auto']['agent'])
        self.assertIn(label + ': finished, Claude has not read it yet.', self.prompt('/cli list')['reason'])
        self.ctl('relay', '--request', request, '--for-host')
        self.assertIn(label + ': finished, read.', self.prompt('/cli list')['reason'])


if __name__ == '__main__':
    import unittest
    unittest.main()
