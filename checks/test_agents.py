"""Several agents in one conversation: each named, each with its own queue, closed one at a time."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from test_controller import FakeBackend, hook
from controller import Controller
import names
from state import Store, agent_label, route


class Agents(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.workspace = root / 'work'
        self.workspace.mkdir()
        self.store = Store('agents', self.workspace, root / 'state')
        self.backend = FakeBackend()
        self.control = Controller(self.store, self.backend)

    def spawn(self, agent='agy', name=None):
        """Start one more agent, as the activation page or /cli spawn does."""
        self.control.use(agent)
        self.control.frontend(agent)
        if name:
            with self.store.edit() as state:
                state['pending']['name'] = name
        defaults = self.control.adapter.DEFAULTS
        result = self.control.activate(defaults['model'], defaults['access'], effort=defaults.get('effort'),
                                       agent=agent)
        return next(item for item in result['owned'] if item['name'] == result['activated'])

    def prompt(self, text):
        return hook.handle(dict(session_id='agents', cwd=str(self.workspace), hook_event_name='UserPromptSubmit',
                                prompt=text), self.store.root)

    def request(self):
        return self.store.read()['turnRoute']['requestId']

    def test_each_new_agent_gets_its_own_name_and_becomes_current(self):
        first = self.spawn('agy')
        second = self.spawn('agy')
        third = self.spawn('codex', 'ELON')
        state = self.store.read()
        self.assertEqual(len(state['owned']), 3)
        self.assertRegex(first['alias'], '^AGY-..$')
        self.assertRegex(second['alias'], '^AGY-..$')
        self.assertNotEqual(first['alias'], second['alias'])
        self.assertEqual(third['alias'], 'ELON')
        self.assertEqual(state['main'], third['name'])
        self.assertEqual((state['backend'], state['settings']), ('codex', third['settings']))
        self.assertEqual(state['usedNames'], [first['alias'], second['alias']])  # Custom names are not generated.
        self.assertEqual(agent_label(state), 'Codex ELON')
        self.assertTrue(all(item['ready'] for item in state['owned']))

    def test_the_limit_and_a_running_name_are_refused(self):
        self.spawn('agy', 'ONE')
        with self.assertRaisesRegex(RuntimeError, 'already running'):
            self.spawn('agy', 'ONE')
        with self.store.edit() as state:
            state['pending'] = None
            state['agentLimit'] = 1
        with self.assertRaisesRegex(RuntimeError, 'the limit'):
            self.spawn('agy')
        self.assertEqual(len(self.store.read()['owned']), 1)

    def test_a_named_prompt_goes_to_that_agent_without_its_name(self):
        agy = self.spawn('agy', 'FIRST')
        self.spawn('agy', 'SECOND')
        self.prompt('/d first  keep  spacing')
        request = self.request()
        record = self.store.read()['requests'][request]
        self.assertEqual((record['session'], record['name'], record['agent'], record['named']),
                         (agy['name'], 'FIRST', 'agy', True))
        output = []
        self.control.send_request(request, output=output.append)
        self.assertEqual(''.join(event['text'] for event in output if event['type'] == 'message'), ' keep  spacing')
        self.assertIn(['-s', agy['name'], '--file'], [args[:3] for args in self.backend.calls])

    def test_an_unnamed_prompt_goes_to_the_current_agent(self):
        self.spawn('agy', 'FIRST')
        second = self.spawn('agy', 'SECOND')
        self.prompt('/d first word is not a name here')  # FIRST is a name: this one is named.
        self.assertTrue(self.store.read()['requests'][self.request()]['named'])
        self.prompt('/d please do it')
        record = self.store.read()['requests'][self.request()]
        self.assertEqual(record['session'], second['name'])
        self.assertNotIn('named', record)

    def test_one_agent_works_while_another_is_busy(self):
        first = self.spawn('agy', 'FIRST')
        self.spawn('agy', 'SECOND')
        self.prompt('/d first long task')
        busy = self.request()
        with self.store.edit() as state:
            # FIRST's turn is running in another process.
            state['requests'][busy].update(status='submitting', operation='op1', settings=first['settings'])
            state['inflight']['op1'] = dict(session=first['name'], kind='prompt', requestId=busy,
                                            submitterPid=os.getpid(), running=True)
        self.prompt('/d second quick task')
        output = []
        result = self.control.send_request(self.request(), output=output.append)
        self.assertEqual(self.store.read()['requests'][result['requestId']]['status'], 'completed')
        # FIRST's queue waits behind its own running turn.
        self.prompt('/d first follow-up')
        with self.assertRaisesRegex(RuntimeError, 'pending/uncertain'):
            self.control.send_request(self.request(), output=output.append)

    def test_spawning_never_waits_for_another_agents_work(self):
        first = self.spawn('agy', 'FIRST')
        self.prompt('/d first long task')
        with self.store.edit() as state:
            state['requests'][self.request()].update(status='submitting', operation='op1')
            state['inflight']['op1'] = dict(session=first['name'], kind='prompt', requestId=self.request(),
                                            submitterPid=os.getpid(), running=True)
        second = self.spawn('agy', 'SECOND')
        self.assertTrue(second['ready'])
        self.assertEqual(self.store.read()['inflight']['op1']['session'], first['name'])

    def test_changing_one_agents_settings_leaves_the_others_and_the_current_agent(self):
        first = self.spawn('agy', 'FIRST')
        second = self.spawn('agy', 'SECOND')
        self.control.tune('access', first['name'])
        state = self.control.activate(first['settings']['model'], 'prompt', agent='agy')
        items = {item['alias']: item for item in state['owned']}
        self.assertEqual(items['FIRST']['settings']['access'], 'prompt')
        self.assertEqual(items['SECOND']['settings'], second['settings'])
        self.assertEqual(state['main'], second['name'])
        self.assertEqual(state['settings'], second['settings'])
        self.assertEqual(len(state['owned']), 2)

    def test_closing_one_agent_keeps_the_others(self):
        first = self.spawn('agy', 'FIRST')
        second = self.spawn('agy', 'SECOND')
        self.prompt('/d second queued work')
        queued = self.request()
        result = self.control.close('second')
        state = self.store.read()
        self.assertEqual([item['alias'] for item in state['owned']], ['FIRST'])
        self.assertEqual(state['requests'][queued]['status'], 'superseded')
        self.assertIn(second['name'], self.backend.closed)
        self.assertTrue(state['active'])
        self.assertEqual(state['main'], first['name'])  # The current agent was closed: FIRST takes over.
        self.assertIn('Antigravity SECOND is closed.', result['message'])
        self.assertIn('Antigravity FIRST is the current agent.', result['message'])

    def test_closing_the_last_agent_is_off(self):
        self.spawn('agy', 'ONLY')
        result = self.control.close('only')
        self.assertTrue(result['shutdownComplete'])
        self.assertFalse(self.store.read()['active'])

    def test_bare_close_asks_which_with_several(self):
        self.spawn('agy', 'FIRST')
        self.spawn('agy', 'SECOND')
        output = self.prompt('/cli close')
        self.assertEqual(self.store.read()['turnRoute']['route'], 'close-menu')
        result = self.control.close()
        self.assertIn('1. Antigravity SECOND (current)', result['activationMenu'])
        self.assertIn('2. Antigravity FIRST', result['activationMenu'])
        self.assertIn('A. All agents', result['activationMenu'])
        self.assertEqual(route('2', self.store.read())['name'], 'FIRST')
        self.prompt('hello')  # Any other message closes the chooser.
        self.assertNotIn('closeMenu', self.store.read())
        self.assertIn('close', output['hookSpecificOutput']['additionalContext'])

    def test_use_makes_an_agent_current(self):
        first = self.spawn('agy', 'FIRST')
        self.spawn('agy', 'SECOND')
        result = self.control.make_current('first')
        self.assertEqual(result['main'], first['name'])
        self.assertIn('Antigravity FIRST is the current agent', result['message'])
        self.prompt('/d unnamed')
        self.assertEqual(self.store.read()['requests'][self.request()]['session'], first['name'])

    def test_the_list_shows_every_agent(self):
        self.spawn('agy', 'FIRST')
        self.spawn('codex', 'SECOND')
        self.prompt('/d first queued')
        message = self.control.agents()['message']
        self.assertIn('Agents: 2 of 4', message)
        self.assertIn('Codex SECOND (current): idle', message)
        self.assertIn('Antigravity FIRST: idle, 1 queued', message)
        self.assertIn('Up to 6 agents', self.control.agents(6)['message'])
        with self.assertRaises(ValueError):
            self.control.agents(9)

    def test_relays_name_the_agent_that_answered(self):
        self.spawn('agy', 'FIRST')
        self.spawn('codex', 'SECOND')
        self.prompt('/d first hello')
        request = self.request()
        self.control.send_request(request, output=lambda event: None)
        self.control.close('first')  # The answer is still labelled after its agent is gone.
        relay = self.control.relay_text(request, wait=0)
        self.assertIn('Antigravity FIRST says...', relay['text'])
        self.assertEqual(relay['agent'], 'Antigravity FIRST')

    def test_workers_are_per_agent(self):
        first = self.spawn('agy', 'FIRST')
        second = self.spawn('agy', 'SECOND')
        with self.store.edit() as state:
            state['runners'] = {first['name']: dict(pid=os.getpid(), token='a' * 32)}
        self.prompt('/d second work')
        os.environ['CLI_MODE_TEST_DISABLE_AUTORUN'] = '0'
        started = []
        try:
            import queue_worker
            original = queue_worker.subprocess.Popen

            class Fake:
                pid = 424242

                def __init__(self, command, **options):
                    started.append(command)

                def poll(self):
                    return None
            queue_worker.subprocess.Popen = Fake
            try:
                result = self.control.ensure_pump(second['name'])
            finally:
                queue_worker.subprocess.Popen = original
        finally:
            os.environ['CLI_MODE_TEST_DISABLE_AUTORUN'] = '1'
        self.assertEqual(result['worker'], 'started')
        self.assertEqual(started[0][-2:], ['--session', second['name']])
        runners = self.store.read()['runners']
        self.assertEqual(set(runners), {first['name'], second['name']})

    def test_timeouts_default_to_an_hour_and_can_be_changed(self):
        import acpx
        first = self.spawn('agy', 'FIRST')
        self.assertEqual(first['timeout'], 60)
        self.assertEqual(acpx.AcpxBackend().ttl(first), 3600)
        self.assertEqual(acpx.AcpxBackend().ttl({}), 3600)  # Saved before agents had a timeout.
        self.assertEqual(route('/cli timeout 2h', self.store.read()), {'route': 'timeout', 'minutes': 120})
        self.assertEqual(route('/cli timeout first 90m', self.store.read())['session'], first['name'])
        self.assertEqual(route('/cli timeout 2', self.store.read())['route'], 'hint')  # Under 5 minutes.
        result = self.control.timeout(120)
        self.assertIn('Agents now stop after 2 hours without a turn, in every conversation.', result['message'])
        second = self.spawn('agy', 'SECOND')
        self.assertEqual(second['timeout'], 120)  # The saved default applies to new agents everywhere.
        self.control.timeout(30, first['name'])
        items = {item['alias']: item['timeout'] for item in self.store.read()['owned']}
        self.assertEqual(items, {'FIRST': 30, 'SECOND': 120})
        self.assertIn('Antigravity FIRST: 30 minutes', self.control.timeout()['message'])

    def other(self, thread='earlier'):
        """Another conversation in the same folder, with its own controller."""
        return Controller(Store(thread, self.workspace, self.store.root), self.backend)

    def test_an_open_agent_from_an_earlier_session_can_be_attached(self):
        earlier = self.other()
        earlier.frontend('agy')
        with earlier.store.edit() as state:
            state['pending']['name'] = 'OLD'
        earlier.activate('gemini-3.8-flash-high', 'allow', agent='agy')
        hook.handle(dict(session_id='earlier', cwd=str(self.workspace), hook_event_name='UserPromptSubmit',
                         prompt='/d first task'), self.store.root)
        request = earlier.store.read()['turnRoute']['requestId']
        earlier.send_request(request, output=lambda event: None)
        listing = self.control.attach()['message']
        self.assertIn('1. Antigravity OLD', listing)
        self.assertIn('1 turn', listing)
        result = self.control.attach('old')
        self.assertIn('Antigravity OLD is attached and is the current agent.', result['message'])
        mine, theirs = self.store.read(), earlier.store.read()
        self.assertEqual([item['alias'] for item in mine['owned']], ['OLD'])
        self.assertEqual(mine['main'], mine['owned'][0]['name'])
        self.assertTrue(mine['active'])
        self.assertEqual((theirs['owned'], theirs['active'], theirs['main']), ([], False, None))
        self.prompt('/d continue')  # It now answers here, in the same ACPX session.
        output = []
        self.control.send_request(self.request(), output=output.append)
        self.assertIn(['-s', mine['owned'][0]['name'], '--file'], [args[:3] for args in self.backend.calls])
        self.assertIn('No agent from an earlier session', self.control.attach()['message'])

    def test_busy_closed_and_clashing_agents_are_not_attached(self):
        earlier = self.other()
        earlier.frontend('agy')
        earlier.activate('gemini-3.8-flash-high', 'allow', agent='agy')
        name = earlier.store.read()['owned'][0]['alias']
        hook.handle(dict(session_id='earlier', cwd=str(self.workspace), hook_event_name='UserPromptSubmit',
                         prompt='/d queued'), self.store.root)
        self.assertIn('busy', self.control.attach()['message'])
        with self.assertRaisesRegex(RuntimeError, 'still working'):
            self.control.attach('1')
        earlier.off()
        self.assertIn('No agent', self.control.attach()['message'])  # Closed: not attachable.
        with self.assertRaisesRegex(RuntimeError, 'No open agent'):
            self.control.attach(name)

    def test_one_prompt_to_several_agents(self):
        first = self.spawn('agy', 'FIRST')
        second = self.spawn('codex', 'SECOND')
        for text, words in (('/d first,second  review it', 1), ('/d first, second  review it', 2)):
            self.prompt(text)
            turn = self.store.read()['turnRoute']
            self.assertEqual(len(turn['requestIds']), 2)
            records = [self.store.read()['requests'][each] for each in turn['requestIds']]
            self.assertEqual([record['session'] for record in records], [first['name'], second['name']])
            self.assertEqual({record['named'] for record in records}, {words})
            for each in turn['requestIds']:
                output = []
                self.control.send_request(each, output=output.append)
                self.assertEqual(''.join(event['text'] for event in output if event['type'] == 'message'),
                                 ' review it')

    def test_a_tag_names_the_only_agent_of_its_kind(self):
        agy = self.spawn('agy', 'FIRST')
        codex = self.spawn('codex', 'SECOND')
        self.assertEqual(route('/d agy go', self.store.read())['session'], agy['name'])
        self.assertEqual(route('/d cod,agy go', self.store.read())['targets'],
                         [dict(session=codex['name'], name='SECOND'), dict(session=agy['name'], name='FIRST')])
        self.assertIn('No Grok agent is running', route('/d gro go', self.store.read())['text'])
        self.spawn('agy', 'THIRD')
        self.assertIn('could mean FIRST or THIRD', route('/d agy go', self.store.read())['text'])
        self.assertEqual(route('/cli close cod', self.store.read())['session'], codex['name'])

    def test_agents_that_finish_together_come_back_in_one_relay(self):
        # Live, 2026-09-25: both agents finished within a second, Claude ran one relay each in the same turn and
        # posted only the last ("as the last message of the turn"): the first answer was lost.
        import importlib.util
        import host
        from presentation import plain_strong
        from test_claude_hook import HOOK
        self.spawn('agy', 'FIRST')
        self.spawn('codex', 'SECOND')
        spec = importlib.util.spec_from_file_location('claude_hook_together', HOOK)
        claude = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(claude)
        self.addCleanup(host.select, host.CODEX)
        base = dict(session_id='agents', cwd=str(self.workspace))
        with unittest.mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.workspace)}):
            claude.handle(dict(base, hook_event_name='UserPromptSubmit', prompt='/d first,second review it'),
                          self.store.root)
            first, second = self.store.read()['turnRoute']['requestIds']
            for request, tool_use in ((first, 'toolu_first'), (second, 'toolu_second')):
                follow = claude.command(dict(base, hook_event_name='PreToolUse'), self.store.root,
                                        'follow', '--request', request)
                claude.handle(dict(base, hook_event_name='PreToolUse', tool_name='Bash', tool_use_id=tool_use,
                                   tool_input={'command': follow, 'timeout': 30000}), self.store.root)
            self.control.send_request(first, output=lambda event: None)

            def wake(tool_use):
                prompt = ('<task-notification>\n<tool-use-id>' + tool_use + '</tool-use-id>\n<status>completed'
                          '</status>\n</task-notification>')
                return claude.handle(dict(base, hook_event_name='UserPromptSubmit', prompt=prompt),
                                     self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('relay --request ' + first + '`', wake('toolu_first'))  # SECOND still works: not waited on.
            self.control.send_request(second, output=lambda event: None)
            text = wake('toolu_first')  # Both have finished: one relay for both, oldest first.
            self.assertIn('relay --request ' + first + ' --request ' + second + '`', text)
            self.assertIn('together with what Codex SECOND finished at the same time', text)
            host.select(host.CLAUDE)
            answer = plain_strong(self.control.relay_chain([first, second], 0, wait=1)['text'])
            self.assertLess(answer.index('Antigravity FIRST says...'), answer.index('Codex SECOND says...'))
            self.assertIn('relay has already run', wake('toolu_second'))  # Nothing is posted twice.

    def test_both_hosts_relay_each_agent(self):
        import importlib.util
        import host
        from test_claude_hook import HOOK
        self.spawn('agy', 'FIRST')
        self.spawn('codex', 'SECOND')
        codex = self.prompt('/d first,second review it')['hookSpecificOutput']['additionalContext']
        requests = self.store.read()['turnRoute']['requestIds']
        self.assertIn('Antigravity FIRST as request ' + requests[0], codex)
        self.assertLess(codex.index('relay --request ' + requests[0]), codex.index('relay --request ' + requests[1]))
        spec = importlib.util.spec_from_file_location('claude_hook_agents', HOOK)
        claude = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(claude)
        self.addCleanup(host.select, host.CODEX)
        event = dict(session_id='agents', cwd=str(self.workspace), hook_event_name='UserPromptSubmit',
                     prompt='/d first,second review it')
        with unittest.mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.workspace)}):
            text = claude.handle(event, self.store.root)['hookSpecificOutput']['additionalContext']
            requests = self.store.read()['turnRoute']['requestIds']
            for each in requests:
                self.assertIn(' follow --request ' + each, text)
            from presentation import plain_strong
            self.assertIn('**Passing to Antigravity FIRST and Codex SECOND...**', plain_strong(text))
            labels = self.store.read()['followLabels']
            self.assertEqual(labels[requests[0]], 'Antigravity FIRST · review it')
            # The turn ended without starting either follow: the guard asks for the first, then the second.
            stop = dict(event, hook_event_name='Stop', stop_hook_active=False, background_tasks=[])
            self.assertIn(' follow --request ' + requests[0], claude.handle(stop, self.store.root)
                          ['hookSpecificOutput']['additionalContext'])
            with self.store.edit() as state:
                state['relayProgress'] = {requests[0]: dict(cursor=0, done=True)}
            self.assertIn(' follow --request ' + requests[1], claude.handle(stop, self.store.root)
                          ['hookSpecificOutput']['additionalContext'])

    def test_a_saved_conversation_migrates(self):
        agy = self.spawn('agy')
        raw = json.loads(self.store.path.read_text(encoding='utf-8'))
        for item in raw['owned']:
            item.pop('alias')
        raw.pop('usedNames')  # Saved before names existed.
        raw['runner'] = dict(pid=1, token='t')
        self.store.path.write_text(json.dumps(raw), encoding='utf-8')
        state = self.store.read()
        self.assertEqual(state['runners'], {agy['name']: dict(pid=1, token='t')})
        self.assertNotIn('runner', state)
        self.assertEqual(state['owned'][0]['alias'], names.generate('agy', agy['name']))
        self.assertEqual(self.store.read()['owned'][0]['alias'], state['owned'][0]['alias'])  # Stable.


if __name__ == '__main__':
    unittest.main()
