"""Every agent gets the same behavior wherever the behavior is not provider-specific."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend
from controller import Controller
from state import Store
import adapters
import frontends
import confirmation
import native_agy
import native_commands
import presentation
import relay_view


class Emissions(unittest.TestCase):
    def test_work_between_texts_separates_messages_for_agents_without_ids(self):
        # Recorded shape from Grok, Copilot and Antigravity: no message IDs.
        events = [dict(type='message', text="I'll read notes.txt first."),
                  dict(type='activity', toolCallId='t', kind='read', status='completed'),
                  dict(type='usage', used=10),
                  dict(type='message', text='The file says'), dict(type='usage', used=11),
                  dict(type='message', text=' to tag the release.')]
        self.assertEqual(relay_view.messages(events, last_only=True), 'The file says to tag the release.')
        self.assertEqual(relay_view.messages(events),
                         "I'll read notes.txt first.\n\nThe file says to tag the release.")

    def test_message_ids_still_separate_messages(self):
        events = [dict(type='message', text='One.', messageId='a'), dict(type='message', text='Two.', messageId='b')]
        self.assertEqual(relay_view.messages(events, last_only=True), 'Two.')


class Commands(unittest.TestCase):
    def test_every_agent_refuses_host_owned_commands_the_same_way(self):
        for agent in adapters.implemented():
            adapter = adapters.module(agent)
            backend = adapter.Backend()
            for name in ('logout', 'model', 'permissions', 'allow-all'):
                with self.subTest(agent=agent, command=name):
                    owned = {'advertisedCommands': [name], 'workspace': str(PLUGIN)}
                    with patch.object(native_agy, 'native_inventory', side_effect=AssertionError('no lookup needed')):
                        with self.assertRaises(RuntimeError) as raised:
                            backend.validate_command(owned, '/' + name + ' x')
                    message = str(raised.exception)
                    self.assertIn('CLI-MODE did not dispatch it to ' + adapter.LABEL, message)

    def test_every_agent_refuses_unknown_commands_the_same_way(self):
        for agent in adapters.implemented():
            adapter = adapters.module(agent)
            with self.subTest(agent=agent), patch.object(native_agy, 'native_inventory',
                                                          return_value={'commands': [], 'skills': []}):
                with self.assertRaises(RuntimeError) as raised:
                    adapter.Backend().validate_command({'advertisedCommands': [], 'workspace': str(PLUGIN)},
                                                       '/definitely-not-a-command')
                self.assertEqual(str(raised.exception), str(native_commands.unknown('definitely-not-a-command',
                                                                                    adapter.LABEL)))

    def test_antigravity_inventory_is_cached_within_its_lifetime(self):
        native_agy._inventory.clear()
        calls = []

        def lookup(workspace, command):
            calls.append(command)
            return {'commands': [{'name': 'plan'}]}
        with patch.object(native_agy, '_native_inventory', side_effect=lookup):
            for _ in range(3):
                native_agy.native_inventory('w', '/help')
        self.assertEqual(calls, ['/help'])
        native_agy._inventory.clear()


class Labels(unittest.TestCase):
    def test_settings_and_activation_use_one_format_for_every_agent(self):
        for agent in adapters.implemented():
            adapter = adapters.module(agent)
            settings = adapter.selection(adapter.CATALOG.parent, **adapter.DEFAULTS)
            with self.subTest(agent=agent):
                text = frontends.settings_text(agent, settings)
                self.assertRegex(text, r'\nAccess: Allow( \([^)]+\))?$')
                self.assertIn('**Access:** Allow', confirmation.activation(agent, settings, {'status': 'unavailable'}))
                for label, value in frontends.phase_options(adapter.CATALOG.parent, agent, 'access'):
                    self.assertRegex(label, r'^(Allow|Auto-edit|Prompt)( \(|$| —)')

    def test_effort_is_ascending_under_one_spelling(self):
        for agent in adapters.implemented():
            adapter = adapters.module(agent)
            settings = adapter.selection(adapter.CATALOG.parent, **adapter.DEFAULTS)
            labels = [label for label, _ in frontends.phase_options(adapter.CATALOG.parent, agent, 'effort', settings)]
            with self.subTest(agent=agent, labels=labels):
                known = [label for label in labels if presentation.effort_key(label)]
                self.assertEqual(known, sorted(known, key=presentation.effort_rank))
                self.assertFalse({'Xhigh', 'Extra high'} & set(labels))


class Tuning(unittest.TestCase):
    def test_typed_choices_match_advertised_options(self):
        claude = adapters.module('claude')
        options = frontends.phase_options(claude.CATALOG.parent, 'claude', 'effort',
                                          claude.selection(claude.CATALOG.parent, **claude.DEFAULTS))
        self.assertEqual(frontends.match_choice(options, 'extra high', 'effort'), ['xhigh'])
        access = frontends.phase_options(claude.CATALOG.parent, 'claude', 'access')
        self.assertEqual(frontends.match_choice(access, 'bypass permissions', 'access'), ['allow'])
        self.assertEqual(frontends.match_choice(access, 'auto edit', 'access'), ['auto-edit'])
        codex = adapters.module('codex')
        models = frontends.phase_options(codex.CATALOG.parent, 'codex', 'model')
        self.assertEqual(len(frontends.match_choice(models, 'sol', 'model')), 2)  # Ambiguous: a menu.
        self.assertEqual(frontends.match_choice(models, 'nothing like this', 'model'), [])

    def test_controller_applies_a_unique_choice_and_shows_the_menu_otherwise(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        control = Controller(Store('tune', root, root / 'state'), FakeBackend())
        control.frontend()
        control.activate('gemini-3.8-flash-high', 'allow')
        with control.store.edit() as state:
            state['turnRoute'] = dict(route='tune', phase='effort', choice='low')
        applied = control.tune_choice('effort')
        self.assertTrue(applied['active'])
        self.assertEqual(control.store.read()['settings']['model'], 'gemini-3.8-flash-low')
        with control.store.edit() as state:
            state['turnRoute'] = dict(route='tune', phase='model', choice='zzz')
        menu = control.tune_choice('model')
        self.assertIn('activationMenu', menu)
        self.assertIn('No advertised model matches "zzz"', menu['message'])
        self.assertEqual(control.store.read()['settings']['model'], 'gemini-3.8-flash-low')


class PowerShell(unittest.TestCase):
    def test_only_the_built_in_windows_powershell_is_used(self):
        for path in PLUGIN.rglob('*'):
            # The optional agent viewer prefers PowerShell 7 when installed and falls back to
            # the built-in one; nothing else may depend on it.
            if (path.suffix in ('.py', '.ps1', '.json', '.mjs') and '__pycache__' not in path.parts
                    and path.name not in ('viewer.py', 'viewer.ps1')):
                text = path.read_text(encoding='utf-8', errors='replace')
                with self.subTest(path=path.name):
                    self.assertNotIn('pwsh', text)
                    self.assertNotIn("'PowerShell 7'", text)
        self.assertFalse(list(PLUGIN.rglob('refresh-models.ps1')))


if __name__ == '__main__':
    unittest.main()
