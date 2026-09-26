"""First-use menus, installation checks and command boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PLUGIN = Path(os.environ.get('CLI_MODE_TEST_PLUGIN', Path(__file__).resolve().parents[1] / 'plugins/cli-mode')).resolve()
sys.path.insert(0, str(PLUGIN / 'scripts'))
import frontends
from controller import Controller
from state import Store, route, INACTIVE_HINT


class Frontends(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store('menus', self.root, self.root / 'data')
        self.control = Controller(self.store)

    @staticmethod
    def passed(agent):
        return dict(backend=agent, confirmed=True, checks=[dict(name='ACPX', installed=True), dict(name='Antigravity CLI', installed=True)])

    def test_both_first_use_menus_offer_check_and_do_not_activate(self):
        for agent in ('home', 'agy'):
            menu = self.control.frontend(agent)
            self.assertTrue(menu['activationMenu'].startswith('```text\n+---'))
            self.assertIn('| CLI-MODE', menu['activationMenu'])
            self.assertIn('New User Detected.', menu['activationMenu'])
            self.assertIn('1. Antigravity CLI', menu['activationMenu'])
            self.assertNotIn('*Full access in codex is required*', menu['activationMenu'])
            self.assertEqual(menu['pending']['onboarding'], 'select-agent')
            self.assertFalse(menu['active'])
            self.assertFalse(menu['owned'])

    def test_access_check_does_not_assume_missing_or_custom_profile_is_full(self):
        for profile, status in ((':danger-full-access', 'enabled'), (':workspace', 'disabled'),
                                (':read-only', 'disabled'), ('custom', 'unknown'), ('', 'unknown')):
            with patch.dict(os.environ, CODEX_PERMISSION_PROFILE=profile):
                result = frontends.access_readiness()
                self.assertEqual(result['status'], status)
                self.assertEqual(result['ready'], status == 'enabled')

    def test_restricted_onboarding_does_not_write_or_run_probes(self):
        with patch.dict(os.environ, CODEX_PERMISSION_PROFILE=':workspace'), \
             patch.object(frontends, 'installation_check') as probe:
            result = self.control.frontend('home')
            self.assertFalse(result['hostAccess']['ready'])
            self.assertIn('New User Detected.', result['activationMenu'])
            checked = self.control.first_time_check()
            self.assertFalse(checked['setupReady'])
            self.assertFalse(self.store.root.exists())
            probe.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'Full Access'):
                self.control.activate('gemini-3.8-flash-high', 'allow', require_hooks=True)

    def test_missing_routing_is_shown_before_activation_choice(self):
        for agent in ('home', 'agy'):
            result = self.control.frontend(agent)
            self.assertEqual(result['routingReadiness']['reason'], 'missing')
            self.assertIn('New User Detected.', result['activationMenu'])
            self.assertNotIn('Yes - use these defaults', result['activationMenu'])
        checked = self.control.first_time_check(check=self.passed)
        self.assertTrue(checked['confirmed'])
        self.assertFalse(checked['routingReadiness']['ready'])
        self.assertFalse(checked['setupReady'])
        self.assertIn('**Desktop: Plugins > CLI-MODE >', checked['activationMenu'])
        self.assertIn('Review / Trust all', checked['activationMenu'])
        self.assertIn('R. Recheck setup', checked['activationMenu'])
        self.assertEqual(self.store.read()['pending']['onboarding'], 'check')
        self.assertFalse(self.store.read()['owned'])

    def test_current_hook_restores_yes_and_stale_or_other_copy_blocks(self):
        frontends.check_and_save(self.store.root, 'agy', self.passed)
        for reason, plugin, stamp in (
                ('different-plugin', str(PLUGIN / 'other-copy'), time.time()),
                ('stale', str(PLUGIN), time.time() - 601),
                ('stale', str(PLUGIN), time.time() + 600),
                (None, str(PLUGIN), time.time())):
            with self.store.edit() as state:
                state['hookSeen'] = dict(plugin=plugin, time=stamp)
            result = self.control.frontend('agy')
            self.assertEqual(result['routingReadiness']['reason'], reason)
            if reason:
                self.assertIn('Setup needs attention' if reason == 'different-plugin' else '1. Recheck routing', result['activationMenu'])
                self.assertNotIn('Yes - use these defaults', result['activationMenu'])
                with self.assertRaises(RuntimeError):
                    self.control.activate('gemini-3.8-flash-high', 'allow', require_hooks=True)
                self.assertFalse(self.store.read()['owned'])
            else:
                self.assertIn('1. Yes - use these defaults', result['activationMenu'])
            self.assertFalse(result['active'])

    def test_older_plugin_hook_explains_reload_on_home_and_each_setup_menu(self):
        with self.store.edit() as state:
            state['hookSeen'] = dict(plugin=str(PLUGIN / 'previous-version'), time=time.time())
        for agent in ['home', *[item['id'] for item in frontends.backends()]]:
            with self.subTest(agent=agent):
                result = self.control.frontend(agent)
                self.assertIn('This task loaded an older plugin.', result['activationMenu'])
                self.assertIn('Start a new task, then run /cli.', result['activationMenu'])
                self.assertFalse(result['active'])
                self.assertFalse(result['owned'])
                if agent != 'home':
                    checked = self.control.first_time_check(agent, check=self.passed)
                    self.assertIn('Start a new task, then run /cli.', checked['activationMenu'])
                    self.assertFalse(checked['setupReady'])

    def test_parent_selection_enters_agy_without_activation(self):
        frontends.check_and_save(self.store.root, 'agy', self.passed)
        with self.store.edit() as state:
            state['hookSeen'] = dict(time=time.time(), plugin=str(PLUGIN))
        self.control.frontend('home')
        self.assertEqual(route('1', self.store.read())['route'], 'choose')
        with self.assertRaisesRegex(RuntimeError, 'Choose an agent'):
            self.control.activate('gemini-3.8-flash-high', 'allow')
        menu = self.control.frontend('agy')
        self.assertEqual(menu['pending']['phase'], 'activation')
        self.assertIn('Agent Settings', menu['activationMenu'])
        self.assertIn('B. Back to agents', menu['activationMenu'])

    def test_confirmation_is_shared_across_menus_and_conversations(self):
        self.control.frontend('home')
        result = self.control.first_time_check(check=self.passed)
        self.assertTrue(result['confirmed'])
        self.assertNotIn('First Time User Check', result['activationMenu'])
        other = Controller(Store('another-thread', self.root, self.store.root))
        for agent in ('home', 'agy'):
            self.assertNotIn('First Time User Check', other.frontend(agent)['activationMenu'])
        self.assertFalse(other.store.read()['active'])

    def test_failure_prompts_install_and_restores_option(self):
        self.control.first_time_check(check=self.passed)
        failed = lambda agent: dict(backend=agent, confirmed=False, checks=[dict(name='ACPX', installed=False)])
        result = self.control.first_time_check(check=failed)
        self.assertIn('Install or repair', result['message'])
        self.assertFalse(frontends.confirmed(self.store.root, 'agy'))
        self.assertIn('New User Detected.', self.control.frontend('home')['activationMenu'])

    def test_onboarding_returns_to_activation_only_after_real_readiness_evidence(self):
        self.control.frontend('home')
        self.control.first_time_check(check=self.passed)
        self.assertEqual(route('R', self.store.read())['route'], 'setup')
        # Isolated fixture: simulate host delivery, never approve real hooks in a test.
        from test_controller import hook
        hook.handle(dict(session_id='menus', cwd=str(self.root), hook_event_name='UserPromptSubmit', prompt='R'), self.store.root)
        result = self.control.first_time_check(check=self.passed)
        self.assertTrue(result['setupReady'])
        self.assertIn('Agent Settings', result['activationMenu'])
        self.assertIn('1. Yes - use these defaults', result['activationMenu'])
        self.assertNotIn('onboarding', self.store.read()['pending'])
        self.assertFalse(self.store.read()['active'])
        self.assertFalse(self.store.read()['owned'])

    def test_hook_ready_but_missing_installation_stays_in_setup(self):
        self.control.frontend('agy')
        with self.store.edit() as state:
            state['hookSeen'] = dict(time=time.time(), plugin=str(PLUGIN))
        result = self.control.first_time_check(check=lambda agent: dict(confirmed=False, checks=[dict(name='ACPX', installed=False)]))
        self.assertFalse(result['setupReady'])
        self.assertIn('Needs installation', result['activationMenu'])
        self.assertNotIn('Yes - use these defaults', result['activationMenu'])

    def test_unknown_backend_cannot_inherit_confirmation(self):
        self.control.first_time_check(check=self.passed)
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            frontends.confirmed(self.store.root, '../agy')
        with self.assertRaises(ValueError): self.control.frontend('future')

    def test_activation_with_hook_still_requires_installation_confirmation(self):
        self.control.frontend('agy')
        with self.store.edit() as state:
            state['hookSeen'] = dict(time=time.time(), plugin=str(PLUGIN))
        with self.assertRaisesRegex(RuntimeError, 'Run First Time User Check'):
            self.control.activate('gemini-3.8-flash-high', 'allow', require_hooks=True)
        self.assertFalse(self.store.read()['active'])
        self.assertFalse(self.store.read()['owned'])

    def test_windows_scan_is_read_only_and_reports_full_dependencies(self):
        with patch.object(frontends.installer, 'call', return_value={'confirmed': False,
                'checks': [{'name': 'Python', 'installed': False}]}) as scan:
            result = frontends.installation_check('agy')
        scan.assert_called_once_with('Scan', backend='agy')
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['backend'], 'agy')

    def test_missing_tools_offer_installation_only_after_approval(self):
        menu = frontends.setup_menu({'confirmed': False,
            'checks': [{'name': 'ACPX', 'installed': False}]}, {'ready': True}, {'ready': True})
        self.assertIn('I. Install missing components', menu)
        self.assertIn('M. Manual installation help', menu)
        self.assertIn('needs your approval', menu)

    def test_setup_menu_names_selected_backend(self):
        for backend in frontends.backends():
            with self.subTest(backend=backend['id']):
                menu = frontends.setup_menu(dict(confirmed=False, checks=[], backend=backend['id']),
                                            dict(ready=False), dict(ready=False))
                self.assertIn(backend['displayName'], menu)
                if backend['id'] != 'agy':
                    self.assertNotIn('Antigravity CLI', menu)

    def test_invalid_controls_and_unbound_tuning(self):
        for command in ('/cli unknown', '$cli bind future', '/cli agy task', '/cli stop extra',
                        '/cli model Flash', '$cli effort high', '/cli permissions allow'):
            with self.subTest(command=command):
                self.assertEqual(route(command, self.store.read()), dict(route='hint', text=(
                    'CLI-MODE: Agent not activated. /CLI to setup' if command == '/cli model Flash' else
                    'CLI-MODE has no /cli unknown. Say /help to see options.' if command == '/cli unknown' else INACTIVE_HINT)))
        state = dict(active=True, pending=None)
        self.assertEqual(route('/cli unknown', state),
                         dict(route='hint', text='CLI-MODE has no /cli unknown. Say /help to see options.'))
        self.assertEqual(route('commands', state)['route'], 'host')
        self.assertEqual(route('/commands extra', state)['route'], 'host')
        self.assertEqual(route('$commands', state)['route'], 'host')

    def test_setup_status_and_instructions_are_conditional(self):
        for hook_ready in (False, True):
            for access_ready in (False, True):
                text = frontends.setup_menu(self.passed('agy'), dict(ready=hook_ready), dict(ready=access_ready))
                rows = text.splitlines()
                self.assertEqual(rows[5].strip('| '), 'Antigravity CLI')
                self.assertIn('ACPX: Installed', text)
                self.assertIn('Hooks: ' + ('On' if hook_ready else '!Attention!'), text)
                self.assertIn('Full Access: ' + ('On' if access_ready else '!Attention!'), text)
                self.assertEqual('**Desktop:' in text, not hook_ready)
                self.assertEqual('*Full access in codex is required*' in text, not access_ready)
                self.assertEqual(rows[-3].strip('| '), 'X. Exit')

    def test_access_levels_say_that_you_approve_in_chat(self):
        import adapters
        import confirmation
        note = '\u2014 you approve in chat'
        for agent in adapters.implemented():
            with self.subTest(agent=agent):
                data = adapters.module(agent).catalog(self.root) if hasattr(self, 'root') else None
                options = frontends.phase_options(adapters.module(agent).CATALOG.parent, agent, 'access',
                                                  snapshot=data)
                for label, value in options:
                    self.assertEqual(note in label, value != 'allow', label)
        settings = dict(modelName='M', effort='high', access='prompt', accessName='Manual')
        self.assertIn('Access: Prompt (Manual) ' + note, frontends.settings_text('claude', settings))
        self.assertIn('**Access:** Prompt (Manual) ' + note, confirmation.activation('claude', settings, {'status': 'unavailable'}))
        allowed = dict(settings, access='allow', accessName='Bypass permissions')
        self.assertNotIn(note, confirmation.activation('claude', allowed, {'status': 'unavailable'}))


if __name__ == '__main__': unittest.main()
