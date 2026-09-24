"""The host layer: Codex stays the default, and Claude Code gets its own state,
workspace, wording and text-only results (P2 of the Claude Code port)."""
import contextlib
import io
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_controller import FakeBackend
from test_relay import ScriptedBackend, activity
import controller as controller_module
from controller import Controller, build_parser, run
import frontends
import help_view
import host
from presentation import plain_strong, queue_text, result_text, shutdown_text, strong, unfence
from state import INACTIVE_HINT, Store, direct_payload, route

CLAUDE = {'CLI_MODE_HOST': host.CLAUDE}


class Selection(unittest.TestCase):
    def test_codex_is_the_default_and_unknown_hosts_fall_back_to_it(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            self.assertEqual(host.current(), host.CODEX)
            os.environ['CLI_MODE_HOST'] = 'something-else'
            self.assertEqual(host.current(), host.CODEX)
            self.assertTrue(host.views())

    def test_select_exports_the_host_for_child_processes(self):
        with patch.dict(os.environ, {}, clear=False):
            host.select(host.CLAUDE)
            self.assertEqual(os.environ['CLI_MODE_HOST'], 'claude-code')
            self.assertEqual(host.name(), 'Claude Code')
            self.assertFalse(host.views())
            with self.assertRaises(ValueError):
                host.select('claude')  # The agent's id, not a host.


class DataAndWorkspace(unittest.TestCase):
    def test_codex_keeps_its_data_root_and_cli_mode_data_wins_everywhere(self):
        with patch.dict(os.environ, {'CODEX_HOME': 'C:/codex-home'}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            os.environ.pop('CLI_MODE_DATA', None)
            self.assertEqual(host.data_root(), Path('C:/codex-home/plugin-data/cli-mode'))
            os.environ['CLI_MODE_DATA'] = 'C:/override'
            self.assertEqual(host.data_root(host.CLAUDE), Path('C:/override'))

    def test_claude_uses_plugin_data_and_never_guesses_a_folder(self):
        with patch.dict(os.environ, CLAUDE, clear=False):
            os.environ.pop('CLI_MODE_DATA', None)
            os.environ.pop('CLAUDE_PLUGIN_DATA', None)
            with self.assertRaises(ValueError):
                host.data_root()
            os.environ['CLAUDE_PLUGIN_DATA'] = 'C:/claude-data'
            self.assertEqual(host.data_root(), Path('C:/claude-data'))

    def test_claude_workspace_stays_on_the_project_after_cd(self):
        event = {'cwd': 'C:/project/src/deep'}  # Claude's cwd follows `cd`.
        with patch.dict(os.environ, dict(CLAUDE, CLAUDE_PROJECT_DIR='C:/project'), clear=False):
            self.assertEqual(host.workspace(event), 'C:/project')
        with patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': 'C:/project'}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            self.assertEqual(host.workspace(event), 'C:/project/src/deep')  # Codex: the task folder.


class Prompts(unittest.TestCase):
    PASTED = ('Please review this:\n<pasted_content id="p1">\nline one\r\nline two\n'
              '</pasted_content id="p1">\nThanks')

    def test_claude_paste_markers_are_removed_and_the_pasted_text_kept(self):
        with patch.dict(os.environ, CLAUDE, clear=False):
            self.assertEqual(host.unwrap_prompt(self.PASTED), 'Please review this:\nline one\r\nline two\nThanks')
            text = 'Explain <pasted_content id="x"> inside a sentence'
            self.assertEqual(host.unwrap_prompt(text), text)  # Only whole marker lines.

    def test_codex_prompts_are_never_changed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            self.assertEqual(host.unwrap_prompt(self.PASTED), self.PASTED)


class Routing(unittest.TestCase):
    STATE = dict(active=True, routingMode='direct', pending=None)

    def test_claude_accepts_namespaced_commands_and_cli_help(self):
        with patch.dict(os.environ, CLAUDE, clear=False):
            self.assertEqual(route('/cli-mode:cli', dict(self.STATE))['route'], 'home')
            self.assertEqual(route('/cli-mode:cli queue', dict(self.STATE))['route'], 'queue')
            self.assertEqual(route('/cli-mode:d fix the parser', dict(self.STATE))['route'], 'direct')
            self.assertEqual(direct_payload('/cli-mode:d fix the parser'), 'fix the parser')
            help_route = route('/cli help', dict(self.STATE))
            self.assertEqual(help_route['route'], 'help')
            self.assertIn('/cli help', help_route['text'])
            self.assertEqual(route('/cli typo', dict(self.STATE, active=False))['text'],
                             '/cli to activate.  Say /cli help to see options')

    def test_codex_routing_is_unchanged(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            self.assertEqual(route('/cli-mode:cli', dict(self.STATE))['route'], 'host')
            self.assertIsNone(direct_payload('/cli-mode:d fix the parser'))
            self.assertEqual(route('/cli help', dict(self.STATE, active=False))['text'], INACTIVE_HINT)
            self.assertIn('/help', help_view.text())
            self.assertNotIn('/cli help', help_view.text())


class Wording(unittest.TestCase):
    def test_claude_readiness_names_claude_code_steps_and_skips_full_access(self):
        with patch.dict(os.environ, CLAUDE, clear=False):
            missing = frontends.routing_readiness({})
            self.assertFalse(missing['ready'])
            self.assertIn('workspace trust', missing['message'])
            self.assertNotIn('Codex', missing['message'])
            stale_copy = frontends.routing_readiness({'hookSeen': {'plugin': 'elsewhere', 'time': 0}})
            self.assertIn('/reload-plugins', stale_copy['message'])
            access = frontends.access_readiness()
            self.assertTrue(access['ready'])
            menu = frontends.setup_menu(dict(confirmed=True, checks=[], backend='agy'), missing, access)
            self.assertNotIn('Full Access', menu)
            self.assertNotIn('Desktop: Plugins', menu)
            self.assertIn('check /hooks', menu)

    def test_codex_readiness_wording_is_unchanged(self):
        with patch.dict(os.environ, {'CODEX_PERMISSION_PROFILE': ':danger-full-access'}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            menu = frontends.setup_menu(dict(confirmed=True, checks=[], backend='agy'),
                                        frontends.routing_readiness({}), frontends.access_readiness())
            self.assertIn('Full Access: On', menu)
            self.assertIn('Desktop: Plugins > CLI-MODE >', menu)


class ClaudeControl(unittest.TestCase):
    """An active Antigravity session driven through the Claude Code host."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = patch.dict(os.environ, CLAUDE, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = ScriptedBackend()
        self.store = Store('claude-session', self.root, self.root / 'data')
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')
        self.control.mode('passthrough')

    def args(self, *words):
        return build_parser().parse_args(['--host', host.CLAUDE, '--thread', 'claude-session',
                                          '--workspace', str(self.root), '--data-root', str(self.store.root), *words])

    def send(self, events):
        self.backend.events = events
        return self.control.send('task', output=lambda event: None)['requestId']


class Controllers(ClaudeControl):
    """The controller on Claude Code: text confirmations, text relay, no views."""
    def test_menus_come_back_as_text_without_views_or_references(self):
        result = run(self.args('settings'), self.control)
        self.assertNotIn('menuView', result)
        self.assertIn('Agent Settings', result_text(result))
        self.assertTrue(result_text(result).startswith('```text\n'))
        self.assertFalse(result_text(result, fenced=False).startswith('```'))
        commands = run(self.args('commands'), self.control)
        self.assertIn('/cli help', commands['text'])

    def test_activation_confirmation_is_text_only(self):
        unavailable = {'status': 'unavailable'}
        with patch('confirmation.usage', return_value=unavailable):
            message = self.control.activation_message(None)
        self.assertIn(strong('CLI-MODE Activated', True), message['text'])
        self.assertIn('`/cli help`', message['text'])  # Claude Code's own /help is built in.
        self.assertNotIn('messageView', message)
        with patch('confirmation.usage', return_value=unavailable):
            result = run(self.args('activation-message'), self.control)
        self.assertEqual(result['text'], message['text'])
        self.assertFalse(list(self.root.rglob('*.html')))

    def test_final_relay_is_text_with_the_whole_turn(self):
        request = self.send([
            dict(type='message', text='I will look at the parser.\n'),
            dict(type='plan', entries=[dict(content='Inspect', status='completed')]),
            activity('a'), dict(type='message', text='Found the bug in the parser.\n')])
        result = run(self.args('relay', '--request', request, '--wait', '1'), self.control)
        self.assertTrue(result['done'])
        self.assertEqual(result['markdown'], '')
        self.assertIsNone(result.get('messageView'))
        self.assertIsNone(result.get('reference'))
        self.assertEqual(result['text'].count(strong('Antigravity says...', True)), 1)
        self.assertLess(result['text'].index('I will look at the parser.'),
                        result['text'].index('Found the bug in the parser.'))  # Every word of the turn, in order.
        self.assertIn('Antigravity work: 1 done', result['text'])
        self.assertNotIn('Passing to', plain_strong(result['text']))  # Claude's own first words, before any call.
        self.assertFalse(list(self.root.rglob('*.html')))
        progress = self.store.read()['relayProgress'][request]
        self.assertEqual(progress, dict(cursor=result['cursor'], done=True))

    def test_nothing_is_posted_until_the_agent_finishes(self):
        # The desktop app folds text between tool calls out of view (the user found "Passing to Grok..." in a
        # collapsed group), so a running turn's calls carry nothing to post and keep their cursor.
        request = self.send([dict(type='message', text='Done.\n')])
        said = [dict(type='message', text='I will look.\n'), activity('a'), dict(type='message', text='Done.\n')]
        status = ['captured']

        def observe(request_id, position, limit=100, ends=False):
            events = said[:limit] if position == 0 else []
            return json.loads(json.dumps(dict(events=events, cursor=position + 100 * len(events),
                                              ends=[position + 100 * (index + 1) for index in range(len(events))],
                                              receipt=dict(status=status[0]), withoutPublicUpdateSeconds=0)))
        with patch.object(self.control, 'observe', side_effect=observe):
            for status[0] in ('captured', 'submitting'):
                waiting = self.control.relay_chain([request], 0, wait=.3)
                self.assertEqual((waiting['done'], waiting['cursor'], waiting['markdown'], waiting['text']),
                                 (False, 0, '', ''))
                self.assertEqual(self.store.read()['relayProgress'][request], dict(cursor=0, done=False))
            status[0] = 'completed'
            final = self.control.relay_chain([request], 0, wait=1)
        self.assertTrue(final['done'])
        self.assertLess(final['text'].index('I will look.'), final['text'].index('Done.'))
        self.assertEqual(final['cursor'], 300)

    def test_long_output_is_paged_under_the_shell_output_limit(self):
        chunks = [dict(type='message', text=('word ' * 300) + '\n') for _ in range(14)]
        request = self.send(chunks)
        results, cursor = [], 0
        with patch.object(self.control, 'RELAY_TEXT_MAX', 8000):
            for _ in range(10):
                result = self.control.relay_chain([request], cursor, wait=1)
                results.append(result)
                cursor = result['cursor']
                if result['done']:
                    break
        self.assertTrue(results[-1]['done'])
        self.assertGreaterEqual(len(results), 3)
        for result in results[:-1]:  # Earlier parts: posted, then the next call.
            self.assertFalse(result['done'])
            self.assertEqual(result['markdown'], result['text'])
        for result in results:
            self.assertLessEqual(len(result['markdown'] or result['text']), 8000 + 500)
        posted = ''.join(result['markdown'] or result['text'] for result in results)
        self.assertEqual(posted.count('word '), 300 * 14)  # Every word once, none twice.

    def test_codex_relay_does_not_record_progress(self):
        request = self.send([dict(type='message', text='Done.\n')])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            self.control.relay(request, 0, wait=1, view_dir=self.root / 'views')
        self.assertNotIn('relayProgress', self.store.read())


class UsageOverlap(ClaudeControl):
    """The activation card's usage lookup (up to 40 s for Antigravity) runs alongside the provider work."""
    def slow_activation(self, prefetching):
        original = Controller.provision
        lookups = []

        def provision(control, *args):
            time.sleep(.5)  # The provider starting and applying settings.
            return original(control, *args)

        def usage(agent, settings, workspace):
            lookups.append(settings['model'])
            time.sleep(.5)
            return {'status': 'unavailable', 'reason': 'test'}
        self.control.frontend()  # Back to this agent's page: activating again reconfigures the same session.
        with patch.object(Controller, 'provision', provision), patch('confirmation.usage', side_effect=usage):
            began = time.monotonic()
            self.control.prefetching = prefetching
            self.control.activate('gemini-3.8-flash-high', 'allow')
            self.control.prefetching = False
            card = self.control.activation_message(None)
            return time.monotonic() - began, lookups, card

    def test_a_setting_change_looks_up_usage_while_the_agent_reactivates(self):
        overlapped, lookups, card = self.slow_activation(prefetching=True)
        self.assertLess(overlapped, .85)  # About one wait, not both.
        self.assertEqual(lookups, ['gemini-3.8-flash-high'])  # Once, for the model activated.
        self.assertIn('CLI-MODE Activated', plain_strong(card['text']))
        self.assertIsNone(self.control.prefetched)  # Used once, never carried into a later card.
        sequential, lookups, _ = self.slow_activation(prefetching=False)
        self.assertGreaterEqual(sequential, .95)
        self.assertEqual(len(lookups), 1)

    def test_run_prefetches_only_for_the_command_it_is_running(self):
        with patch.object(self.control, 'choose', side_effect=RuntimeError('menu changed')):
            with self.assertRaises(RuntimeError):
                run(self.args('choose', '1'), self.control)
        self.assertFalse(self.control.prefetching)


class Pacing(ClaudeControl):
    """How soon a relay call returns. On Claude Code every call costs a model turn (about 3 s), and nothing is
    posted until the agent finishes, so a running turn's call always takes the full wait."""
    def test_a_running_turn_takes_the_full_wait_and_a_finished_one_returns_at_once(self):
        request = self.send([dict(type='message', text='Done.\n')])
        said = [dict(type='message', text='I will look at the parser.\n'),
                dict(type='activity', toolCallId='a', kind='read', status='completed', title='Read notes')]
        status = ['submitting']

        def observe(request_id, position, limit=100, **_):
            events = said[:limit] if position == 10 else []
            return dict(events=events, cursor=position + 100 * len(events),
                        ends=[position + 100 * (index + 1) for index in range(len(events))],
                        receipt=dict(status=status[0]), withoutPublicUpdateSeconds=0)
        timings = {}
        with patch.object(self.control, 'RELAY_IDLE', .1), patch.object(self.control, 'observe', side_effect=observe):
            for label, host_views in (('claude running', False), ('codex running', True), ('claude done', False)):
                status[0] = 'completed' if label == 'claude done' else 'submitting'
                began = time.monotonic()
                if host_views:
                    self.control.relay(request, 10, wait=1.5, view_dir=self.root / 'views')
                else:
                    result = self.control.relay_chain([request], 10, wait=1.5)
                timings[label] = time.monotonic() - began
        self.assertGreaterEqual(timings['claude running'], 1.4)  # The agent's words wait for the final text.
        self.assertLess(timings['codex running'], .8)  # Codex shows updates without a model turn: unchanged.
        self.assertLess(timings['claude done'], .5)
        self.assertIn('I will look at the parser.', result['text'])

    def test_claude_waits_longer_per_call_by_default_and_codex_keeps_its_wait(self):
        request = self.send([dict(type='message', text='Done.\n')])
        with patch.object(self.control, 'relay_chain', return_value=dict(done=True)) as chain:
            run(self.args('relay', '--request', request), self.control)
            run(self.args('relay', '--request', request, '--wait', '99'), self.control)
        self.assertEqual([call.args[2] for call in chain.call_args_list], [25.0, 30])  # Under the 30 s tool timeout.
        codex = build_parser().parse_args(['--host', host.CODEX, '--thread', 'claude-session', '--workspace',
                                           str(self.root), '--data-root', str(self.store.root), 'relay', '--request',
                                           request])
        with patch.object(self.control, 'relay', return_value=dict(done=True)) as relay:
            run(codex, self.control)
        self.assertEqual(relay.call_args.args[2], 8.0)


class Chains(ClaudeControl):
    """One relay command for a turn's earlier, cut-off requests and its own (P7b run 2, turn 25: given one
    command each, Claude posted only the last one's final text)."""
    def relay(self, requests, cursor=0):
        words = [word for request in requests for word in ('--request', request)]
        return run(self.args('relay', *words, '--cursor', str(cursor), '--wait', '1'), self.control)

    def test_an_earlier_finished_request_comes_first_in_the_one_final_text(self):
        earlier = self.send([activity('a'), dict(type='message', text='First answer.\n')])
        latest = self.send([dict(type='message', text='Second answer.\n')])
        result = self.relay([earlier, latest])
        self.assertTrue(result['done'])
        self.assertEqual(result['markdown'], '')
        self.assertLess(result['text'].index('First answer.'), result['text'].index('Second answer.'))
        self.assertEqual(result['text'].count(strong('Antigravity says...', True)), 2)  # Each under its own heading.
        progress = self.store.read()['relayProgress']
        self.assertTrue(progress[earlier]['done'] and progress[latest]['done'])
        self.assertNotIn('chain', progress[latest])

    def test_a_running_earlier_request_waits_and_both_answers_come_at_the_end(self):
        earlier = self.send([activity('a'), dict(type='message', text='First answer.\n')])
        latest = self.send([dict(type='message', text='Second answer.\n')])
        with self.store.edit() as state:
            state['requests'][earlier]['status'] = 'submitting'  # Still running when the turn starts.
        update = self.relay([earlier, latest])
        self.assertEqual((update['done'], update['markdown'], update['text']), (False, '', ''))
        progress = self.store.read()['relayProgress']
        self.assertEqual(progress[latest]['chain'], dict(requests=[earlier, latest], cursor=update['cursor']))
        self.assertFalse(progress[earlier]['done'])
        with self.store.edit() as state:
            state['requests'][earlier]['status'] = 'completed'
        final = self.relay([earlier, latest], update['cursor'])
        self.assertTrue(final['done'])
        text = plain_strong(final['text'])
        self.assertLess(text.index('First answer.'), text.index('Second answer.'))
        self.assertEqual(text.count('**Antigravity says...**'), 2)  # Each under its own heading.
        self.assertNotIn('Passing to', text)  # Claude posts it once, before its first call.
        progress = self.store.read()['relayProgress']
        self.assertTrue(progress[earlier]['done'] and progress[latest]['done'])
        self.assertNotIn('chain', progress[latest])

    def test_calls_while_the_latest_runs_keep_the_earlier_answer_for_the_end(self):
        earlier = self.send([dict(type='message', text='First answer.\n')])
        latest = self.send([dict(type='message', text='Second answer.\n')])
        with self.store.edit() as state:
            state['requests'][latest]['status'] = 'submitting'
        update = self.relay([earlier, latest])
        again = self.relay([earlier, latest], update['cursor'])
        for result in (update, again):  # The earlier answer waits; the latest is still running.
            self.assertFalse(result['done'])
            self.assertNotIn('First answer.', result['markdown'])
        self.assertEqual(again['cursor'], update['cursor'])
        with self.store.edit() as state:
            state['requests'][latest]['status'] = 'completed'
        final = self.relay([earlier, latest], again['cursor'])
        self.assertTrue(final['done'])
        self.assertEqual(final['text'].count('First answer.'), 1)
        self.assertLess(final['text'].index('First answer.'), final['text'].index('Second answer.'))

    def test_combined_words_stay_under_the_shell_output_limit(self):
        earlier = self.send([dict(type='message', text=('alpha ' * 1300) + '\n')])
        latest = self.send([dict(type='message', text=('bravo ' * 1300) + '\n')])
        results, cursor = [], 0
        with patch.object(self.control, 'RELAY_TEXT_MAX', 10000):  # Each fits; both together do not.
            for _ in range(5):
                result = self.relay([earlier, latest], cursor)
                results.append(result)
                cursor = result['cursor']
                if result['done']:
                    break
        self.assertEqual([result['done'] for result in results], [False, True])
        posted = [result['markdown'] or result['text'] for result in results]
        for text in posted:
            self.assertLessEqual(len(text), 10000 + 500)
        self.assertIn('alpha', posted[0])
        self.assertIn('bravo', results[-1]['text'])  # The turn's own answer still ends it.
        joined = ''.join(posted)
        self.assertEqual((joined.count('alpha '), joined.count('bravo ')), (1300, 1300))  # Once each, in order.

    def test_a_host_with_views_relays_one_request_at_a_time(self):
        first = self.send([dict(type='message', text='One.\n')])
        second = self.send([dict(type='message', text='Two.\n')])
        args = build_parser().parse_args(['--host', host.CODEX, '--thread', 'claude-session', '--workspace',
                                          str(self.root), '--data-root', str(self.store.root),
                                          'relay', '--request', first, '--request', second])
        with self.assertRaises(ValueError):
            run(args, self.control)


class FinalWords(unittest.TestCase):
    """A text host's final `text` carries all of the agent's words (P7b: Claude skipped an update; the desktop app
    folds mid-turn text out of view)."""
    def test_relay_holds_every_word_until_done_then_ends_with_them(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, CLAUDE, clear=False):
            root = Path(temp)
            store = Store('final-words', root, root / 'data')
            control = Controller(store, ScriptedBackend())
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            control.mode('passthrough')
            control.backend.events = [dict(type='message', text='x\n')]
            request = control.send('task', output=lambda event: None)['requestId']
            events = [dict(type='activity', toolCallId='a', kind='read', status='completed', title='Read notes'),
                      dict(type='message', text='The answer is 42.\n')]

            status = ['submitting']

            def observe(request_id, position, limit=100, ends=False):
                shown = events[position // 100:][:limit]
                return dict(events=shown, cursor=position + 100 * len(shown),
                            ends=[position + 100 * (index + 1) for index in range(len(shown))],
                            receipt=dict(status=status[0]), withoutPublicUpdateSeconds=0)
            with patch.object(control, 'observe', side_effect=observe):
                update = control.relay_chain([request], 0, wait=1)
                self.assertEqual((update['markdown'], update['cursor']), ('', 0))  # Nothing yet, not even the work.
                status[0] = 'completed'
                final = control.relay_chain([request], update['cursor'], wait=1)
            self.assertTrue(final['done'])
            self.assertIn('The answer is 42.', final['text'])


class ReturningUserSetup(unittest.TestCase):
    def test_choosing_an_agent_that_needs_setup_opens_its_page_not_the_list(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store('returning', root, root / 'data')
            control = Controller(store, FakeBackend())
            for agent in ('copilot',):  # Another agent is already set up.
                receipt = frontends.receipt_path(store.root, agent)
                receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt.write_text(json.dumps(dict(schema=1, confirmed=True)), encoding='utf-8')
            with store.edit() as state:
                state['hookSeen'] = dict(plugin=str(frontends.PLUGIN), time=time.time())

            def check(root_path, agent, check=None):
                path = frontends.receipt_path(root_path, agent)
                path.write_text(json.dumps(dict(schema=1, confirmed=True)), encoding='utf-8')
                return dict(confirmed=True, checks=[], backend=agent)
            self.assertIn('Select CLI Agent', control.frontend('home')['activationMenu'])
            number = [choice['value'] for choice in store.read()['pending']['choices']].index('codex') + 1
            with patch('frontends.check_and_save', side_effect=check):
                result = control.choose(number)
            self.assertIn('Agent Settings', result['activationMenu'])
            self.assertIn('Codex CLI', result['activationMenu'])
            pending = store.read()['pending']
            self.assertEqual((pending['backend'], pending['phase']), ('codex', 'activation'))


class RunAdoptsItsHost(unittest.TestCase):
    def test_run_with_claude_arguments_uses_claude_wording_even_from_a_codex_process(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            root = Path(temp)
            args = build_parser().parse_args(['--host', host.CLAUDE, '--thread', 't', '--workspace', str(root),
                                              '--data-root', str(root / 'data'), 'commands'])
            result = run(args)
            self.assertIn('/cli help', result['text'])
            self.assertEqual(host.current(), host.CLAUDE)


class Text(unittest.TestCase):
    def test_queue_and_shutdown_text_report_exactly_what_happened(self):
        queue = dict(workerState='running', workerError=None, requests=[
            dict(requestId='0123456789abcdef', status='completed', statusAgeSeconds=None),
            dict(requestId='fedcba9876543210', status='submitting', statusAgeSeconds=42.7)], inflight={})
        self.assertEqual(queue_text(queue), 'CLI-MODE queue\nWorker: running\n  01234567  completed\n'
                                            '  fedcba98  submitting (42 s)')
        self.assertIn('No requests', queue_text(dict(workerState='idle', requests=[], inflight={})))
        self.assertEqual(shutdown_text(dict(shutdownComplete=True, failures=[], inflight={})),
                         'CLI-MODE is off. The agent session was closed.')
        partial = shutdown_text(dict(shutdownComplete=False, failures=[dict(session='s1', error='busy')],
                                     inflight={'op': {}}))
        self.assertIn('not complete', partial)
        self.assertIn('Could not close s1: busy', partial)
        self.assertEqual(unfence('```text\nX\n```'), 'X')


class PlainRelay(ClaudeControl):
    """The relay as Claude Code prints it: plain words, then exactly what to post. Claude reads it, and anyone
    who opens the tool call sees it (the desktop app user found JSON with escaped quotes and newlines)."""
    def main(self, *words):
        argv = ['controller.py', '--host', host.CLAUDE, '--thread', 'claude-session', '--workspace', str(self.root),
                '--data-root', str(self.store.root), *words]
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp1252')  # Git Bash's code page; main() switches it to UTF-8.
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdout', stream):
            controller_module.main()
            stream.flush()
        return raw.getvalue().decode('utf-8').replace('\r\n', '\n')  # Windows text streams write CRLF.

    def test_a_finished_turn_prints_one_plain_line_then_the_message(self):
        request = self.send([dict(type='message', text='All tests pass — it’s done.\n')])
        lead, post = self.main('relay', '--request', request, '--wait', '1').split('\n\n', 1)
        self.assertEqual(lead, 'Antigravity has finished. Post everything below this line exactly, as the last '
                               'message of the turn.')
        self.assertIn(strong('Antigravity says...', True), post)
        self.assertIn('All tests pass — it’s done.', post)  # Real characters, not \u escapes.
        for machine in ('"cursor"', '"done"', '\\n', '\\u'):
            self.assertNotIn(machine, lead + post)

    def test_a_running_turn_says_what_to_run_next(self):
        from presentation import relay_plain
        running = dict(agent='Codex', done=False, cursor=1303, status='submitting', markdown='', text='',
                       idleSeconds=75.4)
        self.assertEqual(relay_plain(running), 'Codex is still working (quiet for 75 s). Run the same command again '
                         'with --cursor 1303.')
        self.assertEqual(relay_plain(dict(running, idleSeconds=0)), 'Codex is still working. Run the same command '
                         'again with --cursor 1303.')
        self.assertEqual(relay_plain(dict(running, status='captured')), 'Codex is finishing an earlier turn; this '
                         'request is queued. Run the same command again with --cursor 1303.')
        part = dict(running, status='completed', markdown='**Codex says...**\n\nLooking.')
        self.assertEqual(relay_plain(part), 'Codex has finished, and its answer is long, so it comes in parts. Post '
                         'everything below this line exactly, then run the same command again with --cursor 1303.'
                         '\n\n**Codex says...**\n\nLooking.')

    def test_menus_keep_the_phone_width_on_claude_code(self):
        # The same message shows on the desktop and in the mobile app, whose code blocks fit about 40
        # monospace characters: a wider frame would overflow on the phone.
        import help_view
        for block in (help_view.render(), run(self.args('settings'), self.control)['activationMenu']):
            self.assertEqual({len(row) for row in block.splitlines()[1:-1]}, {40})  # Inside the code fence.


class Colour(ClaudeControl):
    """Green, bold, sans-serif titles and names in Claude Code's chat (its LaTeX), unless /cli color off.
    Codex keeps its own green views; its output is pinned by the golden record."""
    def colour(self, choice):
        (self.store.root / 'display.json').write_text(json.dumps({'color': choice}), encoding='utf-8')

    def test_relay_names_are_green_unless_colour_is_off(self):
        text = self.control.relay_chain([self.send([dict(type='message', text='Done.\n')])], 0, wait=1)['text']
        # \small: the desktop draws LaTeX at 1.21 times the text size; this keeps it just above it.
        self.assertTrue(text.startswith('$\\color{228b22}\\small\\textsf{\\textbf{Antigravity~says...}}$'))
        self.colour('off')
        text = self.control.relay_chain([self.send([dict(type='message', text='Done.\n')])], 0, wait=1)['text']
        self.assertTrue(text.startswith('**Antigravity says...**'))
        self.assertNotIn('\\color', text)

    def test_the_activation_title_is_dark_green_unless_colour_is_off(self):
        unavailable = {'status': 'unavailable'}
        with patch('confirmation.usage', return_value=unavailable):
            self.assertTrue(self.control.activation_message(None)['text'].startswith(strong('CLI-MODE Activated', True)))
            self.colour('off')
            self.assertTrue(self.control.activation_message(None)['text'].startswith('**CLI-MODE Activated**'))

    def test_a_menu_title_band_is_green_in_its_box_only_where_claude_posts_it(self):
        inside = run(self.args('settings'), self.control)['activationMenu']  # In process: instant notices use it.
        self.assertTrue(inside.startswith('```text\n+---'))
        self.assertIn('| Agent Settings', inside)
        self.control.settings_menu(dismiss=True)  # Reopened below by the command Claude would run.
        argv = ['controller.py', '--host', host.CLAUDE, '--thread', 'claude-session', '--workspace', str(self.root),
                '--data-root', str(self.store.root), 'settings']
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='utf-8')
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdout', stream):
            controller_module.main()
            stream.flush()
        posted = json.loads(raw.getvalue().decode('utf-8'))['activationMenu']  # What Claude posts in chat.
        # A diff block colours lines starting with "+": only the title rows do, in the same box. The user saw
        # every border green when they kept their "+" corners.
        rows, box = posted.split('\n'), inside.split('\n')
        rule = '-' * 38
        self.assertEqual(rows[:6], ['```diff', '.' + rule + '.', '+ CLI-MODE'.ljust(39) + '+',
                                    '+ Agent Settings'.ljust(39) + '+', '|' + rule + '|', box[5]])
        self.assertEqual(rows[5:-2], box[5:-2])  # The menu itself is untouched.
        self.assertEqual(rows[-2:], ["'" + rule + "'", '```'])
        self.assertEqual({len(row) for row in rows[1:-1]}, {40})  # Phone width.
        self.assertEqual([row for row in rows[1:-1] if row[0] == '+'], rows[2:4])  # Green: the title only.
        self.assertFalse([row for row in rows[1:-1] if row[0] in '-@' or row != row.rstrip()])  # Nothing red.

    def test_every_green_line_passes_the_desktops_inline_maths_guard(self):
        # The desktop app shows $...$ as maths only with at most 60 characters inside and no "#", "@", '"', "$" or
        # "`" (its inline guard); otherwise the LaTeX shows as raw text (the user's test: \small made 63 and 70).
        import adapters
        from presentation import LATEX_MAX
        texts = ['CLI-MODE Activated'] + [text for agent in ('agy', 'claude', 'grok-build', 'cursor', 'copilot', 'codex')
                                          for text in (adapters.module(agent).PASSING,
                                                       adapters.module(agent).LABEL + ' says...')]
        for text in texts:
            spans = re.findall(r'\$([^$]+)\$', strong(text, True))
            self.assertTrue(spans, text)
            for span in spans:
                self.assertLessEqual(len(span), LATEX_MAX, text)
                self.assertIsNone(re.search(r'[#@"`]|\\[|.+*?0-9]', span), text)
            self.assertEqual(plain_strong(strong(text, True)), '**' + text + '**')
        self.assertEqual(len(re.findall(r'\$', strong('Passing to Antigravity...', True))), 4)  # Two spans.
        self.assertEqual(strong('Issue #12 says...', True), '**Issue #12 says...**')  # The guard would refuse it.

    def test_titles_escape_latex_and_read_back_as_the_same_words(self):
        from presentation import chat_menu, plain_strong
        for words in ('Codex says...', 'CLI-MODE: Help', '50% & a_b {x} ~^' + chr(92)):
            self.assertEqual(plain_strong(strong(words, True)), '**' + words + '**')
        self.assertIn('~', strong('Codex says...', True))  # A phone's math renderer drops plain spaces.
        self.assertEqual(chat_menu('Plain words.', True), 'Plain words.')
        block = help_view.render()
        self.assertEqual(chat_menu(block, False), block)


class Follow(ClaudeControl):
    """`follow`: a background task whose row in Claude Code shows the agent's work, one line per step, and
    whose end wakes Claude for one relay (the P1-P4 probes, 2026-09-24)."""
    def follow(self, request, **options):
        lines = []
        result = self.control.follow(request, lines.append, poll=.05, **options)
        return result, lines

    def test_one_line_per_step_then_how_the_turn_ended(self):
        request = self.send([
            dict(type='message', text='I will look.\n'),
            dict(type='plan', entries=[dict(content='Inspect', status='completed'), dict(content='Fix', status='pending')]),
            activity('a'), activity('a', status='completed'), activity('b', kind='edit', status='failed', title='Edit'),
            dict(type='message', text='Secret answer words.\n')])
        result, lines = self.follow(request)
        self.assertEqual(result, dict(requestId=request, status='completed', done=True))
        self.assertEqual(lines, [
            'Antigravity is writing.', 'Antigravity: plan 1 of 2 steps done',
            'Antigravity: Read source — src/app.py:3', 'Antigravity: Edit — src/app.py:3',
            'Antigravity: failed: Edit — src/app.py:3', 'Antigravity is writing.', 'Antigravity finished.'])
        self.assertNotIn('Secret answer', ''.join(lines))  # The agent's words come only through the relay.
        self.assertNotIn('relayProgress', self.store.read())  # Following never moves the relay.

    def test_it_waits_for_a_queued_then_running_turn_and_keeps_a_process_file_meanwhile(self):
        from operations import follow_path, following
        request = self.send([dict(type='message', text='Done.\n')])
        statuses = iter(['captured', 'captured', 'submitting', 'submitting', 'completed'])
        seen = []

        def observe(request_id, position, limit=100, **_):
            seen.append(following(self.store, request))
            status = next(statuses)
            events = ([activity('a'), dict(type='error', message='Rate\x1b[31m limited')]
                      if status == 'submitting' and position == 0 else [])
            return dict(events=events, cursor=position + 100 * len(events), receipt=dict(status=status))
        with patch.object(self.control, 'observe', side_effect=observe):
            result, lines = self.follow(request)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(lines, ['Antigravity is finishing an earlier turn; this one is queued.',
                                 'Antigravity: Read source — src/app.py:3', 'Antigravity: error: Rate limited',
                                 'Antigravity finished.'])
        self.assertTrue(all(seen))  # The hook can tell that a follow runs, so it never starts a second one.
        self.assertFalse(follow_path(self.store, request).exists())
        self.assertFalse(following(self.store, request))

    def test_a_dead_follows_process_file_does_not_count_and_long_lines_are_cut(self):
        from operations import follow_path, following
        request = self.send([activity('a', title='x' * 400), dict(type='message', text='Done.\n')])
        follow_path(self.store, request).write_text('999999999', encoding='ascii')  # No such process.
        self.assertFalse(following(self.store, request))
        _, lines = self.follow(request)
        self.assertEqual(len(lines[0]), self.control.FOLLOW_LINE)
        self.assertTrue(lines[0].endswith('…'))

    def test_the_exit_code_says_whether_the_turn_completed(self):
        request = self.send([dict(type='message', text='Done.\n')])
        argv = ['controller.py', '--host', host.CLAUDE, '--thread', 'claude-session', '--workspace', str(self.root),
                '--data-root', str(self.store.root), 'follow', '--request', request]
        for status, code in (('completed', 0), ('canceled', 1), ('rejected', 1), ('uncertain', 1)):
            with self.subTest(status=status), patch.object(sys, 'argv', argv), \
                    patch.object(controller_module, 'run', return_value=dict(status=status)), \
                    self.assertRaises(SystemExit) as ended:
                controller_module.main()
            self.assertEqual(ended.exception.code, code)

    def test_its_lines_survive_a_legacy_code_page(self):
        # A background task's output went through cp1252 in the P1 probe ("·" became "?").
        request = self.send([activity('a', title='Read café · notes'), dict(type='message', text='Done.\n')])
        argv = ['controller.py', '--host', host.CLAUDE, '--thread', 'claude-session', '--workspace', str(self.root),
                '--data-root', str(self.store.root), 'follow', '--request', request]
        raw = io.BytesIO()
        legacy = io.TextIOWrapper(raw, encoding='cp1252')
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdout', legacy), \
                patch.object(controller_module, 'Controller', lambda store: self.control), \
                self.assertRaises(SystemExit) as ended:
            controller_module.main()
        legacy.flush()
        self.assertEqual(ended.exception.code, 0)
        self.assertIn('Read café · notes', raw.getvalue().decode('utf-8'))

    def test_codex_never_follows(self):
        request = self.send([dict(type='message', text='Done.\n')])
        codex = build_parser().parse_args(['--host', host.CODEX, '--thread', 'claude-session', '--workspace',
                                           str(self.root), '--data-root', str(self.store.root), 'follow',
                                           '--request', request])
        with self.assertRaises(ValueError):
            run(codex, self.control)


class OverlappingRelays(ClaudeControl):
    """Background turns overlap: a relay can post a request after another relay command naming it was given."""
    relay = Chains.relay

    def test_a_request_posted_since_is_skipped_even_at_the_start_of_the_stream(self):
        earlier = self.send([dict(type='message', text='First answer.\n')])
        latest = self.send([dict(type='message', text='Second answer.\n')])
        self.relay([earlier])  # The earlier follow's wake-up posted it.
        final = self.relay([earlier, latest])  # The command given before that, run now.
        self.assertTrue(final['done'])
        self.assertNotIn('First answer.', final['text'])
        self.assertIn('Second answer.', final['text'])

    def test_a_relay_whose_requests_are_all_posted_posts_nothing(self):
        from presentation import relay_plain
        earlier = self.send([dict(type='message', text='First answer.\n')])
        latest = self.send([dict(type='message', text='Second answer.\n')])
        self.relay([earlier, latest])
        again = self.relay([latest])  # A late wake-up for a request the chain already posted.
        self.assertEqual((again['done'], again['text'], again['markdown']), (True, '', ''))
        self.assertEqual(relay_plain(again), 'Antigravity\'s answer is already posted above, so there is nothing '
                                             'more to post.')


class CommandLine(unittest.TestCase):
    def test_claude_output_survives_a_legacy_code_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            argv = ['controller.py', '--host', host.CLAUDE, '--thread', 'claude-cli', '--workspace', str(root),
                    '--data-root', str(root / 'data'), 'commands']
            raw = io.BytesIO()
            legacy = io.TextIOWrapper(raw, encoding='cp1252')
            with patch.dict(os.environ, {}, clear=False), patch.object(sys, 'argv', argv), \
                    patch.object(sys, 'stdout', legacy):
                controller_module.main()
                legacy.flush()
            result = json.loads(raw.getvalue().decode('utf-8'))
            self.assertIn('/cli help', result['text'])
            self.assertNotIn('menuView', result)


if __name__ == '__main__':
    unittest.main()
