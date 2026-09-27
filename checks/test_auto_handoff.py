"""AUTO handoffs (Claude Code): Claude writes a task file, runs `handoff`, follows it in the background, and is woken
to read the result with `relay --for-host`. The result never reaches the user's own relays."""
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

from test_claude_hook import ClaudeHook, SESSION, claude
from controller import Controller
import auto_mode
import relay_view
from dispatch import READS, approval_policy

TASK = 'Goal: add a docstring to parse().\nContext: app/parser.py.\nDo not: commit or push.\nDone when: it reads well.\n' \
       'Report: what changed.'


def agy(model='gemini-3.8-flash-high'):
    return {'agent': 'agy', 'model': model, 'effort': None, 'access': 'allow'}


class Handoffs(ClaudeHook):
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

    def test_a_handoff_end_to_end(self):
        state = self.auto()
        lead = state['auto']['agent']
        path = self.write_task()
        written = self.pre('Write', file_path=str(path), content=TASK)
        self.assertEqual(written.get('permissionDecision'), 'allow')  # The task file: approved by CLI-MODE.
        result = self.ctl('handoff', '--task', 't1')
        request = result['requestId']
        record = self.store().read()['requests'][request]
        self.assertEqual((record['routingMode'], record['handoff']), ('auto', {'task': 't1', 'readOnly': False}))
        self.assertEqual(record['session'], lead)
        follow = re.search(r'`([^`]+)`', result['next']).group(1)
        self.assertEqual(follow, self.command('follow', '--request', request))
        approved = self.pre('Bash', command=follow, description='x')
        self.assertEqual(approved['permissionDecision'], 'allow')
        self.assertTrue(approved['updatedInput']['run_in_background'])
        self.assertEqual(approved['updatedInput']['description'], self.name() + ' · add a docstring to parse().')
        self.drain(lead)
        self.assertEqual(self.store().read()['requests'][request]['status'], 'completed')
        sent = self.backend.sent[-1]
        self.assertIn('Goal: add a docstring to parse().', sent)
        self.assertNotIn('/d', sent.split('\n', 1)[0])
        with self.store().edit() as saved:  # The tool_use_id the follow's approval remembered.
            saved['followTasks'] = {'toolu_follow': request}
        wake = self.context(self.prompt('<task-notification>\n<tool-use-id>toolu_follow</tool-use-id>\n'
                                        '<status>completed</status>\n</task-notification>'))
        relay = self.command('relay', '--request', request, '--for-host')
        self.assertIn('`' + relay + '`', wake)
        self.assertIn('not the user', wake)
        self.assertEqual(self.store().read()['autoWake'], request)
        self.assertIn('you have not read its result', self.context(self.stop_hook()))  # The turn is held.
        text = self.ctl('relay', '--request', request, '--for-host')['text']
        self.assertTrue(text.startswith('HANDOFF ' + request + ': ' + self.name() + ' finished.'), text)
        self.assertIn('TASK: ' + path.as_posix(), text)
        self.assertIn('ANSWER', text)
        saved = self.store().read()
        self.assertTrue(saved['requests'][request]['hostRead'])
        self.assertNotIn('autoWake', saved)
        self.assertEqual(self.stop_hook(), {})
        self.assertNotIn(request, claude.unrelayed(saved))  # Never chained into a user's relay.
        self.assertIn('already read', self.context(self.prompt(
            '<task-notification>\n<tool-use-id>toolu_follow</tool-use-id>\n</task-notification>')))

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

    def test_one_writer_at_a_time(self):
        state = self.auto(backup=True)
        self.write_task()
        first = self.ctl('handoff', '--task', 't1')  # Not drained: still with the AUTO agent.
        with self.assertRaisesRegex(RuntimeError, 'one writer at a time'):
            self.ctl('handoff', '--task', 't1', '--agent', 'backup')
        review = self.ctl('handoff', '--task', 't1', '--agent', 'backup', '--read-only')
        self.assertTrue(self.store().read()['requests'][review['requestId']]['handoff']['readOnly'])
        again = self.ctl('handoff', '--task', 't1')  # The same agent: queued behind the first, not a second writer.
        self.assertNotEqual(again['requestId'], first['requestId'])
        self.assertEqual(self.store().read()['requests'][again['requestId']]['session'], state['auto']['agent'])

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
        self.assertEqual(record['handoff'], {'task': 't1', 'readOnly': False, 'continues': stopped})
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
        quiet = relay_view.host_text('r2', 'A', 'canceled', [], {})
        self.assertIn('was canceled', quiet)
        self.assertIn('CHANGES: not measured', quiet)
        self.assertIn('ANSWER: none.', quiet)


if __name__ == '__main__':
    import unittest
    unittest.main()
