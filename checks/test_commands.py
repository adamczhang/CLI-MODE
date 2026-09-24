"""New controls with fresh state, real hook entrypoints and fixture ACP pipes."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, hook
from controller import Controller
from state import Store, route, INACTIVE_HINT


class Commands(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store('commands', self.root, self.root / 'data')
        self.backend = FakeBackend()
        self.control = Controller(self.store, self.backend)
        self.control.mode('passthrough')  # This fixture exercises unprefixed forwarding.

    def event(self, prompt=None, compact=False):
        return dict(session_id='commands', cwd=str(self.root),
                    hook_event_name='SessionStart' if compact else 'UserPromptSubmit',
                    source='compact' if compact else 'startup', prompt=prompt or '')

    def test_all_help_aliases_win_over_pending_setup_and_active_dispatch(self):
        for state in (dict(active=False, pending=None), dict(active=True, pending=None),
                      dict(active=True, pending={'phase': 'model'})):
            self.assertEqual(route('/help', state)['route'], 'help')
        self.bind()
        for command in ('/help', '$help'):
            before = len(self.backend.calls)
            context = hook.handle(self.event(command), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('controller.py" --thread', context)
            self.assertEqual(len(self.backend.calls), before)
            self.assertTrue(self.store.read()['active'])
            self.assertEqual(self.store.read()['helpMenu'], 'commands')

    def test_help_replies_stay_local_and_survive_compaction(self):
        self.bind()
        before = len(self.backend.calls)
        for mode in ('passthrough', 'direct'):
            self.control.mode(mode)
            hook.handle(self.event('/help'), self.store.root)
            with self.assertRaisesRegex(RuntimeError, 'menu is pending'):
                self.control.send('not a help selection')
            self.assertEqual(route('2', self.store.read()), {'route': 'help-invalid'})
            context = hook.handle(self.event('2'), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('Help is showing the command table', context)
            self.assertEqual(self.store.read()['helpMenu'], 'commands')
            restored = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('-menu.html" commands`', restored)
            self.assertEqual(route('not a menu choice', self.store.read())['route'], 'help-invalid')
            invalid = hook.handle(self.event('not a menu choice'), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('Do not forward the reply', invalid)
            self.assertEqual(self.store.read()['helpMenu'], 'commands')
            back = hook.handle(self.event('B'), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('Help is showing the command table', back)
            self.assertEqual(self.store.read()['helpMenu'], 'commands')
            hook.handle(self.event('X'), self.store.root)
            self.assertIsNone(self.store.read()['helpMenu'])
        self.assertEqual(len(self.backend.calls), before)

    def test_explicit_cli_control_closes_help_and_direct_task_can_leave_it(self):
        self.bind()
        hook.handle(self.event('/help'), self.store.root)
        self.assertEqual(route('/cli queue', self.store.read())['route'], 'queue')
        hook.handle(self.event('/cli queue'), self.store.root)
        self.assertIsNone(self.store.read()['helpMenu'])
        self.control.mode('direct')
        hook.handle(self.event('/help'), self.store.root)
        self.assertEqual(route('/d do work', self.store.read())['route'], 'direct')

    def test_off_alias_and_setup_exit_gate_routing(self):
        for command in ('/cli off', '$cli off', '/cli stop'):
            self.bind()
            hook.handle(self.event(command), self.store.root)
            self.assertFalse(self.store.read()['active'])
            self.control.off()
        self.control.frontend('home')
        hook.handle(self.event('X'), self.store.root)
        self.assertIsNone(self.store.read()['pending'])
        self.assertEqual(route('x', dict(active=True, pending=None, routingMode='passthrough'))['route'], 'delegate')
        self.assertEqual(route('/cli off extra', dict(active=True))['route'], 'hint')

    def test_help_exit_preserves_agent_and_pending_menu(self):
        self.bind()
        original = self.store.read()['main']
        for mode in ('passthrough', 'direct'):
            self.control.mode(mode)
            hook.handle(self.event('/help'), self.store.root)
            self.assertEqual(route('x', self.store.read())['route'], 'help-dismiss')
            context = hook.handle(self.event('x'), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('Help closed', context)
            state = self.store.read()
            self.assertTrue(state['active'])
            self.assertEqual(state['main'], original)
            self.assertEqual(state['routingMode'], mode)
            self.assertEqual(state['turnRoute']['route'], 'help-dismiss')
            self.assertIsNone(state['helpMenu'])

        settings = self.control.settings_menu()['pending']
        hook.handle(self.event('/help'), self.store.root)
        hook.handle(self.event('X'), self.store.root)
        self.assertEqual(self.store.read()['pending'], settings)
        self.assertEqual(route('done', self.store.read())['route'], 'settings-dismiss')

        self.control.mode()
        hook.handle(self.event('/help'), self.store.root)
        startup = self.event()
        startup['hook_event_name'] = 'SessionStart'
        restored = hook.handle(startup, self.store.root)['hookSpecificOutput']['additionalContext']
        self.assertIn('-menu.html" commands`', restored)
        hook.handle(self.event('X'), self.store.root)
        self.assertTrue(self.store.read()['modeMenu'])
        self.assertEqual(self.store.read()['pending'], settings)
        self.assertEqual(route('X', self.store.read())['route'], 'mode-dismiss')

    def test_help_exit_preserves_inactive_setup(self):
        self.control.frontend('home')
        pending = self.store.read()['pending']
        hook.handle(self.event('/help'), self.store.root)
        hook.handle(self.event('X'), self.store.root)
        self.assertEqual(self.store.read()['pending'], pending)
        self.assertFalse(self.store.read()['active'])
        self.assertEqual(route('1', self.store.read())['route'], 'choose')
        hook.handle(self.event('X'), self.store.root)
        self.assertIsNone(self.store.read()['pending'])

    def test_provider_commands_preserve_the_thread_workspace(self):
        import agy
        state = self.bind()
        owned = state['owned'][0]
        self.assertEqual(owned['workspace'], str(self.root.resolve()))
        with patch('acpx.AcpxBackend.cli', return_value=['acpx']):
            command = agy.Backend().command(owned, ['sessions', 'show', owned['name']])
        self.assertEqual(command[command.index('--cwd') + 1], str(self.root.resolve()))

    def bind(self):
        hook.handle(self.event('/cli bind agy'), self.store.root)
        with patch('frontends.installation_check', return_value=dict(backend='agy', confirmed=True, checks=[])):
            return self.control.bind()

    def test_bind_checks_then_activates_without_menu_reply(self):
        state = self.bind()
        self.assertTrue(state['active'])
        self.assertIsNone(state['pending'])
        self.assertEqual(state['settings']['model'], 'gemini-3.8-flash-high')
        self.assertEqual(state['settings']['access'], 'allow')
        self.assertEqual(len(state['owned']), 1)
        context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
        self.assertIn('control completed', context)

    def test_compaction_before_during_and_after_tuning_never_forwards_control(self):
        original = self.bind()
        hook.handle(self.event('/cli effort low'), self.store.root)
        context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
        self.assertIn('tune --phase effort --apply`', context)
        self.assertNotIn('Pass the complete original message', context)
        self.control.tune('effort')
        context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
        self.assertIn('setup menu is pending', context)
        self.control.activate('gemini-3.8-flash-low', 'allow', require_hooks=True)
        context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
        self.assertIn('control completed', context)
        self.assertNotIn('Pass the complete original message', context)
        self.assertEqual(self.store.read()['main'], original['main'])
        hook.handle(self.event('$cli bind agy'), self.store.root)
        context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
        self.assertIn(' bind --agent ', context)
        self.assertNotIn('control completed', context)

    def test_bind_missing_install_and_untrusted_hooks_never_launch(self):
        with patch('frontends.installation_check', return_value=dict(backend='agy', confirmed=False, checks=[])):
            with self.assertRaisesRegex(RuntimeError, 'Install or repair'):
                self.control.bind()
        with patch('frontends.installation_check', return_value=dict(backend='agy', confirmed=True, checks=[])):
            with self.assertRaisesRegex(RuntimeError, 'No routing hook has run'):
                self.control.bind()
        self.assertFalse(self.backend.calls)
        self.assertFalse(self.store.read()['active'])

    def test_older_activation_cannot_overwrite_newer_control_even_same_kind(self):
        self.bind()
        for origin, phase in (('/cli effort low', 'effort'), ('/cli bind agy', None)):
            hook.handle(self.event(origin), self.store.root)
            if phase:
                self.control.tune(phase)
            else:
                self.control.frontend('agy')
            original_id = self.store.read()['turnRoute']['id']
            newer = []
            def new_control():
                hook.handle(self.event('/cli bind agy'), self.store.root)
                newer.append(self.store.read()['turnRoute'])
            self.backend.after_start = new_control
            self.control.activate('gemini-3.8-flash-low', 'allow', require_hooks=True)
            self.assertNotEqual(newer[0]['id'], original_id)
            self.assertEqual(self.store.read()['turnRoute'], newer[0])
            context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn(' bind --agent ', context)
            self.assertNotIn('control completed', context)

    def test_bound_settings_survive_tuning_help_and_compaction(self):
        original = self.bind()
        for phase, command in (('model', '/cli model Gemini 3.1 Pro'),
                               ('effort', '$cli effort low'), ('access', '/cli permissions auto edit')):
            hook.handle(self.event(command), self.store.root)
            pending = self.control.tune(phase)
            self.assertTrue(pending['tuning'])
            self.assertEqual(pending['phase'], phase)
            self.assertIn('modelFamilies', pending['draft']['snapshot'])
            self.assertEqual(pending['draft']['settings'], original['settings'])
            before = len(self.backend.calls)
            hook.handle(self.event('/help'), self.store.root)
            context = hook.handle(self.event(compact=True), self.store.root)['hookSpecificOutput']['additionalContext']
            self.assertIn('controller.py" --thread', context)
            self.assertEqual(self.store.read()['pending'], pending)
            self.assertEqual(route('1', self.store.read())['route'], 'help-invalid')
            hook.handle(self.event('X'), self.store.root)
            self.assertEqual(route('1', self.store.read())['route'], 'setup')
            with self.assertRaises(RuntimeError): self.control.send('never send menu reply')
            self.assertEqual(len(self.backend.calls), before)
            self.assertEqual(self.store.read()['settings'], original['settings'])

    def test_resolved_choices_reconfigure_same_session_and_become_defaults(self):
        original = self.bind()
        for phase, model, access in (('model', 'gemini-pro-agent', 'allow'),
                                     ('effort', 'gemini-3.1-pro-low', 'allow'),
                                     ('access', 'gemini-3.1-pro-low', 'prompt')):
            self.control.tune(phase)
            updated = self.control.activate(model, access, require_hooks=True)
            self.assertEqual(updated['main'], original['main'])
            self.assertEqual(len(updated['owned']), 1)
            self.assertEqual(updated['settings']['model'], model)
            self.assertEqual(updated['settings']['access'], access)
        self.control.off()
        rebound = self.control.bind()
        self.assertNotEqual(rebound['main'], original['main'])
        self.assertEqual(rebound['settings'], updated['settings'])

    def test_tune_off_busy_and_unsupported_choices_do_not_mutate_live_settings(self):
        with self.assertRaisesRegex(RuntimeError, 'No bound agent'):
            self.control.tune('model')
        original = self.bind()
        def during_work(event):
            if event['type'] == 'dispatched':
                with self.assertRaisesRegex(RuntimeError, 'Settle current work'):
                    self.control.tune('effort')
        self.control.send('task', output=during_work)
        self.control.tune('effort')
        before = len(self.backend.calls)
        with self.assertRaises(ValueError):
            self.control.activate('invented-model', 'allow', require_hooks=True)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.store.read()['settings'], original['settings'])
        hook.handle(self.event('$cli stop'), self.store.root)
        self.assertFalse(self.store.read()['active'])
        self.assertIsNone(self.store.read()['pending'])
        self.assertTrue(self.control.off()['shutdownComplete'])

    def test_command_matrix_and_raw_choices(self):
        for prefix in ('/', '$'):
            for suffix, expected in (('', 'home'), (' agy', 'frontend'), (' bind agy', 'bind'), (' stop', 'off')):
                self.assertEqual(route(prefix + 'cLi' + suffix, self.store.read())['route'], expected)
            # Both prefixes reach help; /commands is still not a help alias.
            self.assertEqual(route(prefix + 'help', self.store.read())['route'], 'help')
            self.assertEqual(route(prefix + 'commands', self.store.read())['route'], 'host')
            self.assertEqual(route(prefix + 'cli typo', self.store.read())['text'], INACTIVE_HINT)
        self.bind()
        for phase in ('model', 'effort', 'access', 'permissions'):
            for choice in ('', 'use the lower effort', 'Gemini 3.1 Pro', 'ask me before tools'):
                decision = route('/cli ' + phase + ' ' + choice, self.store.read())
                self.assertEqual(decision, {'route':'settings'} if phase == 'model' and not choice else
                                 dict(route='tune', phase='access' if phase == 'permissions' else phase, text=choice))
        for word in ('commands', 'help'):
            self.assertEqual(route(word, self.store.read())['route'], 'delegate')
        self.assertEqual(route('/cliX', self.store.read())['route'], 'delegate')


if __name__ == '__main__': unittest.main()
