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
        import presentation
        self.assertIn(presentation.strong(self.name() + ' finished.', True), wake)
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

    def test_each_auto_turn_carries_the_rule_and_the_ledger(self):
        self.assertEqual(self.prompt('hello'), {})  # DIRECT: nothing added.
        self.auto()
        text = self.context(self.prompt('please make the parser keep the last word'))
        for part in ('CLI-MODE AUTO is on. The user turned it on', self.name(), 'Delegation is Strong',
                     '`' + self.command('handoff', '--task') + ' <id>`', auto_mode.TEMPLATE,
                     auto_mode.tasks_dir(self.project).as_posix() + '/<id>.md', 'Do not guess', 'No handoffs yet.'):
            self.assertIn(part, text)
        self.assertLess(len(text), 10000)  # Claude Code's cap for a hook's added context.
        import presentation  # Attribution lines as DIRECT's "Passing to" line: green bold, or plain bold with color off.
        self.assertIn(presentation.strong('Passing to ' + self.name() + ':', True), text)
        self.assertIn(presentation.strong(self.name() + ' is working.', True), text)
        self.write_task()
        request = self.ctl('handoff', '--task', 't1')['requestId']
        self.drain(self.store().read()['auto']['agent'])
        ledger = self.context(self.prompt('how did it go?'))
        self.assertIn('NOT READ YET: `' + self.command('relay', '--request', request, '--for-host') + '`', ledger)
        self.ctl('relay', '--request', request, '--for-host')
        self.assertIn('add a docstring to parse().: finished, read.', self.context(self.prompt('thanks')))

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
        self.assertIn('one writer at a time', refused['permissionDecisionReason'])

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
