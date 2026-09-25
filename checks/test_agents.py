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
