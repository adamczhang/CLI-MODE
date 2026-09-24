"""The Claude Code hook (P3): instant controls, relay context, command approval,
the stop guard, /cli reset, and the fast paths that keep it cheap."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend
from controller import Controller
from state import Store

HOOK = PLUGIN / 'hooks/claude.py'
if HOOK.is_file():  # Not in the Codex package, which checks/package_smoke.py tests.
    spec = importlib.util.spec_from_file_location('claude_hook', HOOK)
    claude = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(claude)
SESSION = 'claude-session-1'
# Claude Code without background tasks: the relay command waits in the turn itself, as before `follow`.
no_background = patch.dict(os.environ, {'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'})


@unittest.skipUnless(HOOK.is_file(), 'The Claude Code hook is not in the Codex package')
class ClaudeHook(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / 'my project & co'
        (self.project / 'src' / 'deep').mkdir(parents=True)
        self.data = self.root / 'data'
        # Most tests read replies as the hook's own notice; the Display tests cover the default (chat).
        environment = patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.project),
                                              'CLI_MODE_CLAUDE_INSTANT': 'block'}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        # Claude's cwd follows `cd`; the workspace must not.
        self.cwd = self.project / 'src' / 'deep'
        self.backend = FakeBackend()
        original = Controller.use

        def use(control, agent):
            adapter = original(control, agent)
            control.backend = self.backend  # Never reach a real agent from tests.
            return adapter
        patcher = patch.object(Controller, 'use', use)
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, name, **fields):
        return claude.handle(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name=name, **fields), self.data)

    def prompt(self, text):
        return self.event('UserPromptSubmit', prompt=text)

    def store(self):
        return Store(SESSION, self.project, self.data)

    def activate(self, mode='direct'):
        control = Controller(self.store(), self.backend)
        control.frontend()
        control.activate('gemini-3.8-flash-high', 'allow')
        control.mode(mode)

    def command(self, *words):
        event = dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse')
        return claude.command(event, self.data, *words)

    def context(self, output):
        return output['hookSpecificOutput']['additionalContext']


class InstantControls(ClaudeHook):
    def test_menus_answer_without_a_model_turn_as_plain_frames(self):
        home = self.prompt('/cli')
        self.assertEqual(home['decision'], 'block')
        self.assertIn('Setup CLI Agent.', home['reason'])
        self.assertTrue(home['reason'].startswith('+---'))
        self.assertNotIn('```', home['reason'])
        help_card = self.prompt('/cli help')['reason']
        self.assertIn('/cli reset', help_card)
        self.assertIn('/cli help', help_card)
        self.assertEqual(self.prompt('$cli queue')['reason'], '/cli to activate.  Say /cli help to see options')

    def test_a_completed_install_shows_the_menu_setup_continues_with(self):
        self.prompt('/cli')
        with self.store().edit() as state:
            state['pending'] = dict(id='setup-1', onboarding='installing', installerRun='run-1', backend='agy')
        done = {'status': 'complete', 'message': 'Setup complete. Return to Claude Code for Agent Settings.'}
        with patch('menus.installer.call', return_value=done), patch('menus.installer.refresh_paths'), \
                patch.object(Controller, 'first_time_check', return_value={'activationMenu': 'NEXT MENU'}):
            shown = self.prompt('done?')['reason']
        self.assertIn('NEXT MENU', shown)
        self.assertNotIn('Return to Claude Code', shown)  # The user is already in Claude Code.

    def test_namespaced_commands_work(self):
        self.assertIn('Setup CLI Agent.', self.prompt('/cli-mode:cli')['reason'])

    def test_display_style_can_be_stop_or_model(self):
        with patch.dict(os.environ, {'CLI_MODE_CLAUDE_INSTANT': 'stop'}):
            stopped = self.prompt('/cli help')
        self.assertIs(stopped['continue'], False)
        self.assertTrue(stopped['stopReason'].startswith('\n+---'))
        with patch.dict(os.environ, {'CLI_MODE_CLAUDE_INSTANT': 'model'}):
            modeled = self.context(self.prompt('/cli help'))
        self.assertIn('```diff\n.---', modeled)  # A chat menu: its title in green (Colour).
        self.assertIn('exactly as given', modeled)

    def test_chat_is_the_default_and_the_choice_is_saved_for_later_replies(self):
        del os.environ['CLI_MODE_CLAUDE_INSTANT']  # The real default, not the test setting.
        card = self.context(self.prompt('/cli help'))  # Claude posts it as a normal message.
        self.assertIn('```diff\n.---', card)
        self.assertIn('no command needs to run', card)
        self.assertIn('chat replies', self.context(self.prompt('/cli display')))
        instant = self.prompt('/cli display instant')
        self.assertEqual(instant['decision'], 'block')  # Its own confirmation is already instant.
        self.assertIn('instant replies', instant['reason'])
        self.assertTrue((self.data / 'display.json').is_file())
        self.assertEqual(self.prompt('/cli help')['decision'], 'block')
        self.assertIn('Use /cli display chat', self.prompt('/cli display sideways')['reason'])
        self.assertEqual(self.prompt('/cli reset')['decision'], 'block')  # Reset reads the choice too.
        chat = self.prompt('/cli display chat')
        self.assertIn('normal chat messages', self.context(chat))
        self.assertIn('```diff\n.---', self.context(self.prompt('/cli help')))

    def test_help_never_holds_the_next_message(self):
        self.prompt('/cli help')
        self.assertEqual(self.prompt('read that, what does it mean?'), {})  # Reaches Claude as usual.
        self.assertIsNone(self.store().read()['helpMenu'])
        self.prompt('/cli help')
        self.assertEqual(self.prompt('x')['reason'], 'Help closed.')  # X still closes it explicitly.

    def test_active_controls_and_stop_report_plainly(self):
        self.activate()
        settings = self.prompt('/cli menu')['reason']
        self.assertIn('Agent Settings', settings)
        self.assertIn('Model: Gemini 3.8 Flash', settings)
        self.assertIn('Worker:', self.prompt('/cli queue')['reason'])
        self.assertIn('No agent turn was running', self.prompt('/cli cancel')['reason'])
        stopped = self.prompt('/cli stop')['reason']
        self.assertIn('CLI-MODE is off', stopped)
        self.assertFalse(self.store().read()['active'])

    def test_every_local_control_answers_in_plain_text_and_never_as_a_codex_view(self):
        replies = [self.prompt(text) for text in ('/cli agy', '/cli claude', '/cli grok', '/cli codex', '/help')]
        self.activate()
        for text in ('/cli mode', '/cli mode passthrough', '/cli progress quiet', '/cli progress',
                     '/cli menu', '1', 'x', '/cli menu', '4', 'x', '/cli menu', '5', '/cli model', '/cli queue'):
            with self.subTest(prompt=text):
                reply = self.prompt(text)
                self.assertEqual(reply.get('decision'), 'block', reply)
                self.assertTrue(reply['reason'].strip())
                replies.append(reply)
        for reply in replies:
            for codex_only in ('visualize', 'menuView', 'messageView', '```', 'Full access in codex'):
                self.assertNotIn(codex_only, json.dumps(reply))

    def test_controller_errors_are_shown_not_raised(self):
        self.activate()
        with patch('controller.run', side_effect=RuntimeError('Settle current work first.')):
            self.assertEqual(self.prompt('/cli menu')['reason'], 'CLI-MODE: Settle current work first.')


class SlowControls(ClaudeHook):
    def test_bind_gives_one_exact_command_and_how_to_show_its_result(self):
        text = self.context(self.prompt('/cli bind claude'))
        self.assertIn('`' + self.command('bind', '--agent', 'claude') + '`', text)
        self.assertIn('activation.text', text)
        self.assertIn('180000 ms', text)
        self.assertNotIn('messageView', text)
        # A failed bind's error is CLI-MODE's reply, not Claude's retelling (P7b run 3 added its own advice).
        self.assertIn('`CLI-MODE: ` followed by that error', text)
        self.assertIn('no advice or alternatives added', text)

    def test_every_command_that_can_fail_says_how_its_error_is_shown(self):
        with self.store().edit() as state:
            state['pending'] = dict(id='p', stage='menu', phase='activation', entrypoint='agy', backend='agy',
                                    onboarding='check', draft={}, choices=[])
        for reply in ('I', 'something else'):  # Install, and the setup menu's other controls.
            self.assertIn('`CLI-MODE: ` followed by that error', self.context(self.prompt(reply)))

    def test_activation_menu_replies_map_to_exact_controls(self):
        self.prompt('/cli agy')
        control = Controller(self.store(), self.backend)
        control.frontend('agy')
        with patch('frontends.confirmed', return_value=True):
            control.frontend('agy')  # A configured agent's activation page.
            yes = self.context(self.prompt('1'))
            self.assertIn('`' + self.command('activate', '--agent', 'agy') + '`', yes)
            defaults = self.prompt('2')
        self.assertEqual(defaults['decision'], 'block')

    def test_install_needs_the_users_explicit_yes(self):
        with self.store().edit() as state:
            state['pending'] = dict(id='p', stage='menu', phase='activation', entrypoint='agy', backend='agy',
                                    onboarding='check', draft={}, choices=[])
        text = self.context(self.prompt('I'))
        self.assertIn('`' + self.command('setup-start', '--approved', '--agent', 'agy') + '`', text)
        self.assertIn('explicit yes', text)


class Relay(ClaudeHook):
    def test_direct_turns_get_factual_relay_context_with_the_exact_command(self):
        self.activate('direct')
        text = self.context(self.prompt('/d Explain the parser'))
        request = self.store().read()['turnRoute']['requestId']
        self.assertIn('`' + self.command('relay', '--request', request) + '`', text)
        for phrase in ('exactly as printed', 'prints plain text', 'nothing added', 'Agent tool stays unused', '30000 ms',
                       'did not receive them'):  # The hook cannot see attachments; Claude can.
            self.assertIn(phrase, text)
        for codex_only in ('visualize', 'reference', 'messageView', 'Codex'):
            self.assertNotIn(codex_only, text)
        self.assertLess(len(text), 10000)  # Claude Code's cap on hook context.

    @no_background
    def test_the_turn_opens_with_the_passing_line_before_any_command(self):
        # The desktop app folds text between tool calls into a collapsed group, where the user found
        # "Passing to Grok...": the line comes first, the agent's output last, nothing in between.
        import presentation
        self.activate('direct')
        text = self.context(self.prompt('/d Explain the parser'))
        line = presentation.strong('Passing to Antigravity...', True)
        # A zero-width space after the last "$": the app leaves a still-streaming block's final "$" as plain text.
        self.assertIn('posted before any command:\n' + line + '​\n', text)
        self.assertLess(text.index(line), text.index('relay --request'))
        for phrase in ('no text between calls', 'last message of the turn', 'up to 25 seconds'):
            self.assertIn(phrase, text)
        compacted = self.context(self.event('SessionStart', source='compact'))
        self.assertIn('relay --request', compacted)
        self.assertNotIn('Passing', compacted)  # Only a new request is passed.
        resumed = self.prompt('/cli resume')
        self.assertIn('relay --request', json.dumps(resumed))
        self.assertNotIn('Passing', json.dumps(resumed))
        (self.data / 'display.json').write_text(json.dumps({'color': 'off'}), encoding='utf-8')
        self.assertIn('\n**Passing to Antigravity...**\n', self.context(self.prompt('/d Next')))

    def test_pasted_text_is_forwarded_without_claudes_markers(self):
        self.activate('passthrough')
        self.prompt('Review this:\n<pasted_content id="p1">\ndef f(): pass\n</pasted_content id="p1">')
        request = self.store().read()['turnRoute']['requestId']
        self.assertEqual(self.store().request_path(request).read_text(encoding='utf-8'), 'Review this:\ndef f(): pass')

    def test_a_cd_inside_the_project_never_breaks_the_session(self):
        self.activate('passthrough')
        for folder in (self.project, self.project / 'src', self.cwd):
            self.cwd = folder
            self.assertIn('relay --request', self.context(self.prompt('Next step')))
        self.assertEqual(Path(self.store().read()['workspace']), self.project)

    def test_resume_relays_existing_requests_without_sending_them_again(self):
        self.activate('passthrough')
        self.prompt('Queued task')
        request = self.store().read()['turnRoute']['requestId']
        text = self.context(self.prompt('/cli resume'))
        self.assertIn(self.command('relay', '--request', request), text)
        self.assertIn('not sent to the agent again', text)

    def test_a_new_request_first_relays_earlier_output_the_user_never_saw(self):
        self.activate('direct')
        self.prompt('/d first task')  # Its relay was interrupted: it never finished.
        first = self.store().read()['turnRoute']['requestId']
        text = self.context(self.prompt('/d second task'))
        second = self.store().read()['turnRoute']['requestId']
        relay = text[text.index(' relay --request'):]
        self.assertLess(relay.index(first), relay.index(second))
        self.assertIn('not sent again', text)
        # One command for both: given one each, Claude posted only the last one's final text (P7b run 2).
        self.assertIn('`' + self.command('relay', '--request', first, '--request', second) + '`', text)
        self.assertIn('`' + self.command('follow', '--request', second) + '`', text)  # FIFO: the latest ends last.
        self.assertEqual(text.count('--request'), 3)
        self.assertIn('oldest first', text)
        with self.store().edit() as state:
            state['relayProgress'] = {first: dict(cursor=10, done=True), second: dict(cursor=10, done=True)}
        self.assertNotIn(first, self.context(self.prompt('/d third task')))  # Shown already: not repeated.

    def test_answers_already_relayed_are_not_relayed_again_after_many_requests(self):
        self.activate('direct')
        control = Controller(self.store(), self.backend)
        requests = []
        for number in range(control.RELAY_PROGRESS_KEPT + 5):
            self.prompt('/d task ' + str(number))
            requests.append(self.store().read()['turnRoute']['requestId'])
            with self.store().edit() as state:
                state['requests'][requests[-1]]['status'] = 'completed'
            control._record_relay([(requests[-1], 10, True)])  # The user saw its whole answer.
        self.assertEqual(claude.unrelayed(self.store().read()), [])
        self.assertLessEqual(len(self.store().read()['relayProgress']), control.RELAY_PROGRESS_KEPT)

    def test_resume_includes_finished_work_whose_output_was_never_shown(self):
        self.activate('passthrough')
        self.prompt('Task whose relay was cut off')
        request = self.store().read()['turnRoute']['requestId']
        with self.store().edit() as state:
            state['requests'][request]['status'] = 'completed'
        self.assertIn(self.command('relay', '--request', request), self.context(self.prompt('/cli resume')))

    def test_resume_covers_every_pending_request_with_one_command(self):
        self.activate('passthrough')
        self.prompt('Task whose relay was cut off')
        first = self.store().read()['turnRoute']['requestId']
        with self.store().edit() as state:
            state['requests'][first]['status'] = 'completed'
        self.prompt('Task queued behind it')
        second = self.store().read()['turnRoute']['requestId']
        text = self.context(self.prompt('/cli resume'))
        self.assertIn('`' + self.command('relay', '--request', first, '--request', second) + '`', text)
        self.assertEqual(text.count('--request'), 3)  # Both in the one relay, and the follow of the queued one.

    def test_compaction_resumes_a_chain_at_its_stream_cursor(self):
        self.activate('direct')
        self.prompt('/d First task')
        first = self.store().read()['turnRoute']['requestId']
        self.prompt('/d Second task')
        second = self.store().read()['turnRoute']['requestId']
        with self.store().edit() as state:
            state['relayProgress'] = {first: dict(cursor=0, done=False), second: dict(
                cursor=12, done=False, chain=dict(requests=[first, second], cursor=900))}
        text = self.context(self.event('SessionStart', source='compact'))
        self.assertIn(self.command('relay', '--request', first, '--request', second, '--cursor', '900'), text)

    def test_compaction_resumes_the_relay_from_its_saved_cursor(self):
        self.activate('direct')
        self.prompt('/d Long task')
        request = self.store().read()['turnRoute']['requestId']
        with self.store().edit() as state:
            state['relayProgress'] = {request: dict(cursor=4096, done=False)}
        text = self.context(self.event('SessionStart', source='compact'))
        self.assertIn(self.command('relay', '--request', request, '--cursor', '4096'), text)
        self.assertIn('not sent again', text)
        self.assertNotIn('forwarded this message', text)

    def task_through(self, *menu):
        """Open a settings menu, then send a /d task: it closes the menu and reaches the agent."""
        self.activate('direct')
        for prompt in menu:
            self.prompt(prompt)
        self.assertTrue(self.store().read()['pending'])
        text = self.context(self.prompt('/d who are you'))
        request = self.store().read()['turnRoute']['requestId']
        self.assertIn(self.command('relay', '--request', request), text)
        self.assertIsNone(self.store().read()['pending'])

    def test_a_d_task_goes_through_an_open_settings_page(self):
        self.task_through('/cli menu')  # From the phone: it was taken as a menu reply.

    def test_a_d_task_goes_through_an_open_setting_list(self):
        self.task_through('/cli menu', '1')  # The model list reached from the settings page.

    def test_a_message_the_open_settings_page_does_not_take_is_reported_not_dropped(self):
        """After a routing change Agent Settings stays open; a task typed then was silently lost (validation)."""
        self.activate('passthrough')
        self.prompt('/cli menu')
        reply = self.prompt('Reply with only the word early.')['reason']
        self.assertIn('Agent Settings', reply)
        self.assertIn('Agent Settings is open, so that message was not sent to Antigravity', reply)
        self.assertNotIn('not sent', self.prompt('/cli menu')['reason'])  # Its own controls get no note.

    def test_settings_replies_still_answer_the_menu(self):
        self.activate('direct')
        self.prompt('/cli menu')
        self.assertIn('Agent Settings', self.prompt('/d')['reason'])  # No task: still a reply to the menu.
        self.assertEqual(self.prompt('x')['reason'], 'Settings closed. CLI remains active.')

    def test_subagents_are_denied_only_for_delegated_turns(self):
        self.activate('direct')
        self.prompt('host question')
        self.assertEqual(self.event('PreToolUse', tool_name='Agent', tool_input={}), {})
        self.prompt('/d agent task')
        denied = self.event('PreToolUse', tool_name='Agent', tool_input={})
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')


class StopGuard(ClaudeHook):
    @no_background
    def test_an_early_stop_gets_the_next_relay_command_a_few_times(self):
        self.activate('direct')
        self.prompt('/d Long task')
        request = self.store().read()['turnRoute']['requestId']
        first = self.context(self.event('Stop', stop_hook_active=False))
        self.assertIn(self.command('relay', '--request', request, '--cursor', '0'), first)
        with self.store().edit() as state:
            state['relayProgress'] = {request: dict(cursor=512, done=False)}
        self.assertIn('--cursor 512', self.context(self.event('Stop', stop_hook_active=True)))
        self.assertIn('hookSpecificOutput', self.event('Stop', stop_hook_active=True))
        self.assertEqual(self.event('Stop', stop_hook_active=True), {})  # At most three nudges.

    @no_background
    def test_the_guard_continues_a_turn_with_all_the_requests_it_carries(self):
        self.activate('direct')
        self.prompt('/d First task')  # Its relay never finished.
        first = self.store().read()['turnRoute']['requestId']
        self.prompt('/d Second task')
        second = self.store().read()['turnRoute']['requestId']
        before = self.context(self.event('Stop', stop_hook_active=False))  # Claude stopped before any relay.
        self.assertIn(self.command('relay', '--request', first, '--request', second, '--cursor', '0'), before)
        with self.store().edit() as state:
            state['relayProgress'] = {first: dict(cursor=0, done=False), second: dict(
                cursor=12, done=False, chain=dict(requests=[first, second], cursor=900))}
        later = self.context(self.event('Stop', stop_hook_active=True))
        self.assertIn(self.command('relay', '--request', first, '--request', second, '--cursor', '900'), later)

    def test_no_guard_after_done_or_outside_relay_turns(self):
        self.assertEqual(self.event('Stop'), {})  # CLI-MODE never used here.
        self.activate('direct')
        self.prompt('/d Task')
        request = self.store().read()['turnRoute']['requestId']
        with self.store().edit() as state:
            state['relayProgress'] = {request: dict(cursor=10, done=True)}
        self.assertEqual(self.event('Stop'), {})
        self.prompt('ordinary host question')
        self.assertEqual(self.event('Stop'), {})


class BackgroundFollow(ClaudeHook):
    """A /d turn posts the Passing line, starts a background `follow` and ends; the follow's end wakes Claude
    for one relay. Probes P1-P4 (2026-09-24) confirmed each Claude Code behaviour this relies on."""
    PROMPT = 'Explain the parser in detail please'
    LABEL = 'Antigravity · Explain the parser in detail p…'  # Agent, then 30 characters of the prompt.

    def start(self):
        self.activate('direct')
        text = self.context(self.prompt('/d ' + self.PROMPT))
        return text, self.store().read()['turnRoute']['requestId']

    def pre_tool_use(self, command, tool='Bash'):
        event = dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse', tool_name=tool,
                     tool_input={'command': command, 'description': 'Claude\'s own words', 'timeout': 30000})
        return claude.handle(event, self.data).get('hookSpecificOutput') or {}

    def running(self, request):
        from operations import follow_path
        follow_path(self.store(), request).write_text(str(os.getpid()), encoding='ascii')  # A live process.

    def test_a_d_turn_posts_the_passing_line_starts_one_follow_and_ends(self):
        import presentation
        text, request = self.start()
        follow = '`' + self.command('follow', '--request', request) + '`'
        self.assertIn(follow, text)
        self.assertIn('`' + self.command('relay', '--request', request) + '`', text)
        self.assertLess(text.index(presentation.strong('Passing to Antigravity...', True)), text.index(follow))
        for phrase in ('nothing posted after the opening line', 'background tasks', 'whatever the follow\'s exit code',
                       'exactly as printed', 'nothing added', 'Agent tool stays unused', '30000 ms'):
            self.assertIn(phrase, text)
        self.assertNotIn('up to 25 seconds', text)  # Nothing waits in the turn.
        self.assertLess(len(text), 10000)

    def test_a_follow_runs_in_the_background_labelled_with_the_agent_and_prompt(self):
        _, request = self.start()
        command = self.command('follow', '--request', request)
        for tool in ('Bash', 'PowerShell'):
            with self.subTest(tool=tool):
                output = self.pre_tool_use(command, tool)
                self.assertEqual(output['permissionDecision'], 'allow')
                # Claude Code replaces the whole input: the checked command, unchanged, plus the two fields.
                self.assertEqual(output['updatedInput'], dict(command=command, timeout=30000, run_in_background=True,
                                                              description=self.LABEL))
        with self.store().edit() as state:
            state['requests'][request]['status'] = 'completed'
        Controller(self.store(), self.backend).mode('passthrough')
        self.prompt('Fix\n\nthe   bug')
        short = self.store().read()['turnRoute']['requestId']
        output = self.pre_tool_use(self.command('follow', '--request', short))
        self.assertEqual(output['updatedInput']['description'], 'Antigravity · Fix the bug')

    def test_one_follow_per_request_and_none_for_requests_of_other_sessions(self):
        _, request = self.start()
        self.running(request)
        denied = self.pre_tool_use(self.command('follow', '--request', request))
        self.assertEqual(denied['permissionDecision'], 'deny')
        self.assertIn('already following', denied['permissionDecisionReason'])
        for words in (('follow', '--request', 'f' * 32), ('follow', '--request', request, '--request', request),
                      ('follow',)):
            with self.subTest(words=words):
                self.assertNotIn('permissionDecision', self.pre_tool_use(self.command(*words)))

    def test_resume_and_compaction_never_start_a_second_follow(self):
        from operations import follow_path
        _, request = self.start()
        follow_path(self.store(), request).write_text('999999999', encoding='ascii')  # It died without a wake-up.
        self.assertIn(self.command('follow', '--request', request),
                      self.context(self.event('SessionStart', source='compact')))
        self.running(request)
        for text in (self.context(self.event('SessionStart', source='compact')), self.context(self.prompt('/cli resume'))):
            self.assertIn('already running', text)
            self.assertIn('one line', text)
            self.assertNotIn(' follow --request', text)
            self.assertIn(self.command('relay', '--request', request), text)

    def test_a_finished_request_is_relayed_at_once_without_a_follow(self):
        _, request = self.start()
        with self.store().edit() as state:
            state['requests'][request]['status'] = 'completed'
        text = self.context(self.prompt('/cli resume'))
        self.assertNotIn(' follow --request', text)
        self.assertIn(self.command('relay', '--request', request), text)

    def test_without_background_tasks_the_relay_waits_in_the_turn(self):
        with no_background:
            text, _ = self.start()
        self.assertNotIn(' follow --request', text)
        self.assertIn('up to 25 seconds', text)

    def test_a_turn_ends_freely_while_its_follow_runs(self):
        _, request = self.start()
        follow = self.command('follow', '--request', request)
        task = dict(id='b1', type='shell', status='running', description=self.LABEL, command=follow)
        self.assertEqual(self.event('Stop', stop_hook_active=False, background_tasks=[task]), {})
        self.assertNotIn('relayNudges', self.store().read())  # Ending here is the plan, not a missed relay.
        # Claude ended the turn without starting it: the guard gives the follow, then the relay after it.
        nudged = self.context(self.event('Stop', stop_hook_active=False, background_tasks=[dict(task, status='completed')]))
        self.assertLess(nudged.index('`' + follow + '`'), nudged.index(self.command('relay', '--request', request)))
        self.running(request)  # An older Claude Code sends no task list: the follow's own process file decides.
        self.assertEqual(self.event('Stop', stop_hook_active=False), {})
        with self.store().edit() as state:
            state['requests'][request]['status'] = 'completed'
        woken = self.context(self.event('Stop', stop_hook_active=False, background_tasks=[]))
        self.assertIn(self.command('relay', '--request', request, '--cursor', '0'), woken)  # The wake-up's relay.


class Approval(ClaudeHook):
    def allowed(self, text, tool='Bash', session=SESSION):
        event = dict(session_id=session, cwd=str(self.cwd), hook_event_name='PreToolUse', tool_name=tool,
                     tool_input={'command': text})
        output = claude.handle(event, self.data)
        return output.get('hookSpecificOutput', {}).get('permissionDecision') == 'allow'

    def test_exact_controller_commands_for_this_session_are_approved_in_both_shells(self):
        relay = self.command('relay', '--request', 'a' * 32, '--cursor', '120')
        self.assertIn("'" + self.project.as_posix() + "'", relay)  # The spaced path is quoted.
        self.assertTrue(self.allowed(relay))
        self.assertTrue(self.allowed(relay, tool='PowerShell'))
        chain = self.command('relay', '--request', 'a' * 32, '--request', 'b' * 32, '--cursor', '4096')
        self.assertTrue(self.allowed(chain))
        self.assertTrue(self.allowed(chain, tool='PowerShell'))
        self.assertTrue(self.allowed(self.command('bind', '--agent', 'grok-build')))
        self.assertTrue(self.allowed(self.command('activate', '--agent', 'agy')))
        self.assertTrue(self.allowed(self.command('activate', '--access', 'prompt', '--agent', 'agy')))

    def test_installing_or_widening_access_asks_the_user_through_claude_code(self):
        # Prompt text alone cannot stop Claude from running these, for example after injected instructions.
        for words in (('setup-start', '--approved', '--agent', 'agy'), ('activate', '--access', 'allow'),
                      ('activate', '--agent', 'claude', '--access', 'auto-edit')):
            with self.subTest(words=words):
                event = dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse', tool_name='Bash',
                             tool_input={'command': self.command(*words)})
                output = claude.handle(event, self.data)['hookSpecificOutput']
                self.assertEqual(output['permissionDecision'], 'ask')
                self.assertIn('CLI-MODE', output['permissionDecisionReason'])

    def decision(self, text):
        event = dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse', tool_name='Bash',
                     tool_input={'command': text})
        return claude.handle(event, self.data).get('hookSpecificOutput', {}).get('permissionDecision')

    def test_widening_access_from_settings_asks_and_narrowing_does_not(self):
        """C7: /cli access allow and the access menu reach the controller as tune/choose, not activate."""
        control = Controller(self.store(), self.backend)
        control.frontend()
        control.activate('gemini-3.8-flash-high', 'prompt')
        tune = self.command('tune', '--phase', 'access', '--apply')
        self.prompt('/cli access allow')
        self.assertEqual(self.decision(tune), 'ask')
        self.prompt('/cli access prompt')
        self.assertEqual(self.decision(tune), 'allow')  # Same level: nothing widens.
        self.prompt('/cli access')  # No choice typed: it only opens the access menu.
        self.assertEqual(self.decision(tune), 'allow')
        self.prompt('/cli menu')
        self.prompt('3')  # Change access: the numbered access list.
        choices = [item['value'] for item in self.store().read()['pending']['choices']]
        self.assertEqual(self.decision(self.command('choose', str(choices.index('allow') + 1))), 'ask')
        self.assertEqual(self.decision(self.command('choose', str(choices.index('prompt') + 1))), 'allow')

    def test_anything_else_keeps_claude_codes_normal_permissions(self):
        good = self.command('queue')
        rejected = [
            good + '; rm -rf ~', good + ' && whoami', good + ' | tee x', good + ' `whoami`', good + ' $(whoami)',
            good + '\nwhoami', good.replace(' queue', '  queue'), good.replace('--thread ' + SESSION, '--thread other'),
            good.replace(self.data.as_posix(), (self.root / 'other').as_posix()),
            self.command('send', '--request', 'a' * 32), self.command('queue', "it's"), self.command('queue', '${X}'),
            good.replace('controller.py', 'evil.py'), 'python -c "print(1)"', 'python ' + good[len('python '):] + ' x;',
        ]
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(self.allowed(text))
        self.assertFalse(self.allowed(good, session='another-session'))

    def test_a_workspace_with_a_quote_is_never_auto_approved(self):
        quoted = self.root / "it's here"
        quoted.mkdir()
        with patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(quoted)}):
            self.assertFalse(self.allowed(self.command('queue')))


class Reset(ClaudeHook):
    def test_reset_sets_aside_unreadable_state_and_cli_starts_fresh(self):
        self.prompt('/cli')
        path = claude.state_path(dict(session_id=SESSION), self.data)
        path.write_text('{not json', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.prompt('/cli')
        text = self.prompt('/cli reset')['reason']
        self.assertIn('set aside', text)
        self.assertFalse(path.exists())
        self.assertEqual(len(list(path.parent.glob(path.name + '.bad-*'))), 1)
        self.assertIn('Setup CLI Agent.', self.prompt('/cli')['reason'])
        self.assertIn('no saved state', claude.handle(dict(session_id='fresh', cwd=str(self.cwd),
                      hook_event_name='UserPromptSubmit', prompt='$cli reset'), self.data)['reason'])

    def test_reset_of_an_idle_session_just_clears_it(self):
        self.event('SessionStart', source='startup')  # Records hook evidence, as every session does.
        path = claude.state_path(dict(session_id=SESSION), self.data)
        self.assertTrue(path.exists())
        self.assertIn('had nothing running', self.prompt('/cli reset')['reason'])
        self.assertFalse(path.exists())
        self.assertFalse(list(path.parent.glob('*.bad-*')))

    def test_reset_keeps_a_copy_when_an_agent_was_active(self):
        self.activate()
        self.assertIn('set aside', self.prompt('/cli reset')['reason'])


class Shortcuts(ClaudeHook):
    """nothing_to_do() answers most events before anything loads; it must never change a reply."""
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, {'CLAUDE_PLUGIN_DATA': str(self.data)})
        environment.start()
        self.addCleanup(environment.stop)

    def events(self):
        ours = self.command('relay', '--request', 'a' * 32)
        return [('start', dict(hook_event_name='SessionStart', source='startup')),
                ('resume', dict(hook_event_name='SessionStart', source='resume')),
                ('compact', dict(hook_event_name='SessionStart', source='compact')),
                ('prose', dict(hook_event_name='UserPromptSubmit', prompt='How do I read a file?')),
                ('x', dict(hook_event_name='UserPromptSubmit', prompt='x')),
                ('number', dict(hook_event_name='UserPromptSubmit', prompt='1')),
                ('cli', dict(hook_event_name='UserPromptSubmit', prompt='  /cli')),
                ('d', dict(hook_event_name='UserPromptSubmit', prompt='$d task')),
                ('python', dict(hook_event_name='PreToolUse', tool_name='Bash', tool_input={'command': 'python -V'})),
                ('ours', dict(hook_event_name='PreToolUse', tool_name='PowerShell', tool_input={'command': ours})),
                ('agent', dict(hook_event_name='PreToolUse', tool_name='Agent', tool_input={})),
                ('stop', dict(hook_event_name='Stop', stop_hook_active=False))]

    def states(self):
        yield 'never used', lambda: None
        yield 'help open', lambda: self.prompt('/cli help')
        yield 'help closed', lambda: self.prompt('x')
        yield 'setup open', lambda: self.prompt('/cli')
        yield 'active direct', lambda: (self.activate('direct'), self.prompt('What is 2+2?'))
        yield 'relaying', lambda: self.prompt('/d task')
        yield 'mode menu', lambda: self.prompt('/cli mode')
        yield 'active passthrough', lambda: (self.prompt('1'), Controller(self.store(), self.backend).mode('passthrough'))
        yield 'stopped', lambda: self.prompt('/cli stop')

    def test_a_shortcut_gives_exactly_the_reply_handle_would(self):
        taken = set()
        for state, arrive in self.states():
            arrive()
            for label, fields in self.events():
                with self.subTest(state=state, event=label):
                    event = dict(session_id=SESSION, cwd=str(self.cwd), **fields)
                    before = self.store().path.read_bytes() if self.store().path.exists() else None
                    if claude.nothing_to_do(json.dumps(event)):
                        taken.add(label)
                        self.assertEqual(claude.handle(dict(event), self.data), {})
                    after = self.store().path.read_bytes() if self.store().path.exists() else None
                    if label not in ('start', 'resume', 'compact', 'stop', 'python', 'ours', 'agent'):
                        # Undo what a prompt changed, so every event meets the state it was listed under.
                        if before is None:
                            self.store().path.unlink(missing_ok=True)
                        elif after != before:
                            self.store().path.write_bytes(before)
        self.assertLessEqual({'start', 'resume', 'prose', 'python', 'agent', 'stop'}, taken)

    def test_commands_and_this_sessions_controller_always_get_the_full_hook(self):
        self.prompt('/cli help')
        self.prompt('x')  # Used before, nothing open: plain replies take the shortcut.
        for prompt in ('/cli', '$cli', '/cli-mode:cli', '/d x', '/help', '/cli reset'):
            self.assertFalse(claude.nothing_to_do(json.dumps(dict(
                session_id=SESSION, hook_event_name='UserPromptSubmit', prompt=prompt))), prompt)
        ours = self.command('bind', '--agent', 'codex')  # Activation starts from an inactive session.
        self.assertFalse(claude.nothing_to_do(json.dumps(dict(
            session_id=SESSION, hook_event_name='PreToolUse', tool_name='PowerShell', tool_input={'command': ours}))))


class PersonalCommands(ClaudeHook):
    """Bare /cli and /d for autocomplete. Plugin commands are always namespaced (/cli-mode:cli); the zip
    installer and /cli shortcuts (for GitHub installs) add personal commands, never over the user's own."""
    def setUp(self):
        super().setUp()
        self.config = self.root / 'claude config'
        environment = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config)}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)

    def test_shortcuts_add_bare_commands_and_keep_the_users_own(self):
        commands = self.config / 'commands'
        commands.mkdir(parents=True)
        (commands / 'd.md').write_text('---\ndescription: mine\n---\nMine.\n', encoding='utf-8')
        reply = self.prompt('/cli shortcuts')['reason']
        self.assertIn('/cli now autocomplete as typed', reply)
        self.assertIn('Kept your own', reply)
        self.assertEqual((commands / 'd.md').read_text(encoding='utf-8'), '---\ndescription: mine\n---\nMine.\n')
        written = (commands / 'cli.md').read_bytes()
        self.assertTrue(written.startswith(b'---\n'))  # No BOM, so the frontmatter still reads.
        self.assertIn(b'Installed by CLI-MODE', written)
        plugin = (claude.PLUGIN / 'claude/commands/cli.md').read_text(encoding='utf-8').replace('\r\n', '\n')
        self.assertEqual(written.decode('utf-8').replace('\n' + __import__('claude_shortcuts').MARKER, ''), plugin)
        # A file CLI-MODE wrote, even with the 0.2.0 installer's marker, is its to replace.
        (commands / 'cli.md').write_text('---\n---\n<!-- Installed by CLI-MODE (install-claude.ps1); '
                                         'reinstalling replaces this file. -->\nold\n', encoding='utf-8')
        self.prompt('/cli-mode:cli shortcuts')
        self.assertIn(b'CLI-MODE', (commands / 'cli.md').read_bytes())
        self.assertNotIn(b'\nold\n', (commands / 'cli.md').read_bytes())

    def test_shortcuts_are_claude_only(self):
        from state import route
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLI_MODE_HOST', None)
            self.assertNotEqual(route('/cli shortcuts', dict(active=False, routingMode='direct'))['route'],
                                'shortcuts')


class Colour(ClaudeHook):
    """Menu titles in green, inside the same box, in chat; plain text wherever colour would not show."""
    def test_chat_menus_have_a_green_title_and_notices_never_do(self):
        import help_view
        del os.environ['CLI_MODE_CLAUDE_INSTANT']  # The default display: chat.
        card = self.context(self.prompt('/cli help'))
        box = help_view.render().split('\n')
        rule = box[1][1:-1]
        band = ['+' + row[1:-1] + '+' for row in box[2:4]]  # "+ CLI-MODE ... +", "+ Help ... +": green.
        # Borders take ".", "|" and "'" corners: a "+" would colour them too.
        self.assertIn('\n'.join(['```diff', '.' + rule + '.', *band, '|' + rule + '|', *box[5:-2],
                                 "'" + rule + "'", '```']), card)
        with patch.dict(os.environ, {'CLI_MODE_CLAUDE_INSTANT': 'block'}):
            notice = self.prompt('/cli help')['reason']  # A hook notice is plain text: no colour.
        self.assertTrue(notice.startswith('+---'))
        self.assertIn('| CLI-MODE', notice)
        self.assertNotIn('```diff', notice)

    def test_colour_off_is_saved_with_the_display_choice(self):
        del os.environ['CLI_MODE_CLAUDE_INSTANT']
        self.prompt('/cli display instant')
        self.assertIn('plain bold', self.prompt('/cli color off')['reason'])
        self.assertEqual(json.loads((self.data / 'display.json').read_text(encoding='utf-8')),
                         {'display': 'instant', 'color': 'off'})
        self.assertIn('colour is off', self.prompt('/cli colour')['reason'])
        self.prompt('/cli display chat')
        card = self.context(self.prompt('/cli help'))
        self.assertIn('| CLI-MODE', card)  # The title stays in the box, uncoloured.
        self.assertIn('```text', card)
        self.assertNotIn('```diff', card)


class Process(ClaudeHook):
    """The hook as Claude Code runs it: a fresh process reading one event."""
    def run_hook(self, event, code=None):
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(self.data), CLAUDE_PROJECT_DIR=str(self.project))
        script = code or ('import runpy,sys; runpy.run_path(sys.argv[1], run_name="__main__")')
        # Claude Code sends UTF-8 JSON with non-ASCII text unescaped.
        done = subprocess.run([sys.executable, '-c', script, str(PLUGIN / 'hooks/claude.py')],
                              input=json.dumps(event, ensure_ascii=False).encode('utf-8'), capture_output=True,
                              env=env, timeout=60)
        return subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode('utf-8', 'replace'),
                                           done.stderr.decode('utf-8', 'replace'))

    def test_unrelated_python_commands_return_before_importing_cli_mode(self):
        code = ('import runpy,sys\n'
                'try:\n    runpy.run_path(sys.argv[1], run_name="__main__")\n'
                'finally:\n    print(sorted(m for m in ("state", "controller", "route") if m in sys.modules))')
        done = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse',
                                  tool_name='Bash', tool_input={'command': 'python manage.py test'}), code)
        self.assertEqual(done.stdout.splitlines(), ['{}', '[]'])

    def test_sessions_that_never_use_cli_mode_load_nothing_and_leave_no_state(self):
        code = ('import runpy,sys\n'
                'try:\n    runpy.run_path(sys.argv[1], run_name="__main__")\n'
                'finally:\n    print(sorted(m for m in ("host", "state", "route", "pathlib", "hashlib")'
                ' if m in sys.modules))')
        for fields in (dict(hook_event_name='SessionStart', source='startup'),
                       dict(hook_event_name='UserPromptSubmit', prompt='Explain this function'),
                       dict(hook_event_name='PreToolUse', tool_name='Agent', tool_input={}),
                       dict(hook_event_name='Stop', stop_hook_active=False)):
            done = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), **fields), code)
            self.assertEqual(done.stdout.splitlines(), ['{}', '[]'], fields)
        self.assertFalse(self.store().path.exists())

    def test_prompts_are_read_as_utf8_whatever_the_console_code_page(self):
        # Windows Python reads stdin as the ANSI code page (cp1252) unless told otherwise.
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'cp1252'}):
            done = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='UserPromptSubmit',
                                      prompt='Explain \u6765 and caf\u00e9'))
        self.assertEqual((done.returncode, done.stdout.strip()), (0, '{}'), done.stderr)

    def test_a_utf8_prompt_reaches_route_intact_on_any_code_page(self):
        code = ('import runpy,sys,json\n'
                'import route\n'
                'seen = []\n'
                'original = route.decide\n'
                'route.decide = lambda event, *a, **k: seen.append(event[\'prompt\']) or original(event, *a, **k)\n'
                'try:\n    runpy.run_path(sys.argv[1], run_name="__main__")\n'
                'finally:\n    print(json.dumps(seen))')
        self.prompt('/cli')
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'cp1252', 'PYTHONPATH': str(PLUGIN / 'hooks') + os.pathsep +
                                     str(PLUGIN / 'scripts')}):
            done = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='UserPromptSubmit',
                                      prompt='/cli caf\u00e9'), code)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1]), ['/cli caf\u00e9'], done.stderr)

    def test_a_busy_or_failed_control_is_not_reported_as_unreadable_state(self):
        code = ('import runpy,sys\n'
                'import route\n'
                'def busy(*args, **kwargs):\n'
                '    raise RuntimeError("CLI-MODE state is busy; retry this control operation.")\n'
                'route.decide = busy\n'
                'runpy.run_path(sys.argv[1], run_name="__main__")')
        self.prompt('/cli')
        with patch.dict(os.environ, {'PYTHONPATH': str(PLUGIN / 'hooks') + os.pathsep + str(PLUGIN / 'scripts')}):
            done = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='UserPromptSubmit',
                                      prompt='/cli cancel'), code)
        blocked = json.loads(done.stdout)
        self.assertEqual(blocked['decision'], 'block')
        self.assertIn('state is busy', blocked['reason'])
        self.assertNotIn('could not be read', blocked['reason'])
        self.assertNotIn('/cli reset', blocked['reason'])  # Resetting would set a healthy state file aside.

    def test_unreadable_state_blocks_prompts_with_the_way_out_and_never_tools(self):
        self.prompt('/cli')
        claude.state_path(dict(session_id=SESSION), self.data).write_text('{not json', encoding='utf-8')
        prompt = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='UserPromptSubmit',
                                    prompt='/cli'))
        self.assertEqual(prompt.returncode, 0)
        blocked = json.loads(prompt.stdout)
        self.assertEqual(blocked['decision'], 'block')
        self.assertIn('/cli reset', blocked['reason'])
        tool = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='PreToolUse',
                                  tool_name='Agent', tool_input={}))
        self.assertEqual((tool.returncode, tool.stdout.strip()), (0, '{}'))
        stop = self.run_hook(dict(session_id=SESSION, cwd=str(self.cwd), hook_event_name='Stop'))
        self.assertEqual((stop.returncode, stop.stdout.strip()), (0, '{}'))


if __name__ == '__main__':
    unittest.main()
