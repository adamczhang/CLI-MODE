"""Complete reference coverage and capability-gated native command dispatch."""
import unittest
from unittest.mock import patch

from test_controller import PLUGIN
import native_agy
import native_commands

REFERENCE = set('add-dir agents boost artifact btw clear config context copy credits diff exit fast feedback fork help hooks keybindings logout mcp model open permissions planning rename remote-control resume rewind skills statusline tasks teamwork-preview title usage voice'.split())
ALIASES = {'new': 'clear', 'settings': 'config', 'quit': 'exit', 'branch': 'fork',
           'switch': 'resume', 'conversation': 'resume', 'undo': 'rewind',
           'teamwork': 'teamwork-preview', 'quota': 'usage', 'record': 'voice'}
INSTALLED = 'agents changelog config credits effort help hooks model permissions skills usage'.split()


class Catalog(unittest.TestCase):
    def test_entire_reference_and_alias_table_is_covered(self):
        self.assertEqual({c['name'] for c in native_agy.COMMANDS}, REFERENCE)
        for alias, canonical in ALIASES.items():
            text = '\t/' + alias.upper() + '  task\r\n  unchanged '
            self.assertEqual(native_agy.expand_alias(text), '\t/' + canonical + '  task\r\n  unchanged ')
        self.assertEqual(len(ALIASES), 10)

    @patch('native_agy.native_inventory')
    def test_every_reference_command_uses_advertised_support_or_explicit_unavailability(self, inventory):
        inventory.return_value = {'commands': [{'name': n} for n in INSTALLED]}
        for command in native_agy.COMMANDS:
            for name in [command['name'], *command['aliases']]:
                text = '/' + name + (' task' if command['name'] in native_agy.WORKFLOWS else '')
                if name in native_commands.HOST_OWNED:
                    # Sign-in and settings commands get the shared refusal, like every agent.
                    with self.assertRaisesRegex(RuntimeError, 'did not dispatch it to Antigravity'):
                        native_agy.validate_command(text, str(PLUGIN))
                elif command['name'] in INSTALLED or command['name'] in native_agy.WORKFLOWS:
                    native_agy.validate_command(text, str(PLUGIN))
                else:
                    with self.assertRaisesRegex(RuntimeError, 'does not advertise a headless handler'):
                        native_agy.validate_command(text, str(PLUGIN))

    @patch('native_agy.native_inventory')
    def test_new_runtime_handlers_and_aliases_work_without_code_changes(self, inventory):
        inventory.return_value = {'commands': [{'name': 'future-native', 'aliases': ['future-alias']}]}
        aliases = native_agy.validate_command('/future-alias keep  spaces', str(PLUGIN))
        self.assertEqual(native_agy.expand_alias('/future-alias keep  spaces', aliases), '/future-native keep  spaces')
        inventory.return_value = {'commands': [{'name': 'voice'}]}
        native_agy.validate_command('/record', str(PLUGIN))

    @patch('native_agy.native_inventory')
    def test_custom_native_skills_and_unknown_commands(self, inventory):
        inventory.side_effect = lambda workspace, command: (
            {'commands': []} if command == '/help' else {'skills': [{'name': 'my-plugin:review'}]})
        self.assertTrue(native_agy.command_request('/my-plugin:review task'))
        native_agy.validate_command('/my-plugin:review task', str(PLUGIN))
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
            native_agy.validate_command('/made-up-command', str(PLUGIN))

    @patch('native_agy.native_inventory')
    def test_commands_is_not_a_help_alias(self, inventory):
        inventory.side_effect = lambda workspace, command: (
            {'commands': [{'name': 'help'}]} if command == '/help' else {'skills': []})
        with self.assertRaisesRegex(RuntimeError, 'Unknown or unavailable'):
            native_agy.validate_command('/commands', str(PLUGIN))
        self.assertEqual(native_agy.expand_alias('/commands'), '/commands')

    @patch('native_agy.native_inventory')
    def test_empty_workflows_and_broken_inventory_fail_before_dispatch(self, inventory):
        inventory.return_value = {'commands': []}
        for name in native_agy.WORKFLOWS:
            with self.assertRaisesRegex(ValueError, 'Usage:'):
                native_agy.validate_command('/' + name, str(PLUGIN))
        inventory.return_value = {'commands': 'invalid'}
        with self.assertRaisesRegex(RuntimeError, 'malformed'):
            native_agy.validate_command('/usage', str(PLUGIN))

    @patch('native_agy.shutil.which', return_value='agy')
    @patch('native_agy.subprocess.run')
    def test_discovery_rejects_model_answers_and_malformed_metadata(self, run, which):
        import json
        from types import SimpleNamespace
        for response in ([], None, {'command': None},
                {'status': 'SUCCESS', 'num_turns': 1, 'command': {'name': 'help', 'data': {}}}):
            run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(response))
            with self.assertRaisesRegex(RuntimeError, 'discovery failed'):
                native_agy.native_inventory(str(PLUGIN), '/help')



if __name__ == '__main__':
    unittest.main()
