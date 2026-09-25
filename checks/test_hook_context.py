"""Each turn carries only the rule groups it needs, within a fixed size budget."""
import tempfile
import unittest
from pathlib import Path

from test_controller import FakeBackend, hook
from controller import Controller
from state import Store

# Distinctive phrases from each rule group in hooks/route.py.
RELAY = 'post its `markdown` right away'
MENU = 'X on active Settings or tuning pages runs'
SETUP = 'Follow pending.onboarding'
HELP = 'Help is the same framed card'
# Budgets leave headroom over today's sizes (about 2.7k, 2.5k, 3.2k and 1.6k).
BUDGET = {'delegate': 3500, 'menu': 3000, 'setup': 3500, 'help': 1800}


class HookContext(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store('context', self.root, self.root / 'data')

    def context(self, prompt):
        event = dict(session_id='context', cwd=str(self.root), hook_event_name='UserPromptSubmit', prompt=prompt)
        return hook.handle(event, self.store.root)['hookSpecificOutput']['additionalContext']

    def activate(self):
        control = Controller(self.store, FakeBackend())
        control.frontend()
        control.activate('gemini-3.8-flash-high', 'allow')

    def assertGroups(self, text, present, budget):
        for phrase in (RELAY, MENU, SETUP, HELP):
            self.assertEqual(phrase in text, phrase in present, phrase)
        self.assertLessEqual(len(text), budget)

    def test_setup_turns_carry_menu_and_setup_rules_only(self):
        self.assertGroups(self.context('/cli'), (MENU, SETUP), BUDGET['setup'])

    def test_delegated_turns_carry_relay_rules_only(self):
        self.activate()
        text = self.context('/d Explain the parser')
        self.assertGroups(text, (RELAY,), BUDGET['delegate'])
        self.assertIn('relay --request ', text)
        self.assertNotIn('"pending"', text)  # Relay turns do not echo menu transactions.
        self.assertGroups(self.context('/cli queue'), (RELAY,), BUDGET['delegate'])

    def test_menu_and_help_turns_do_not_carry_relay_rules(self):
        self.activate()
        self.assertGroups(self.context('/cli menu'), (MENU,), BUDGET['menu'])
        self.assertGroups(self.context('/help'), (HELP,), BUDGET['help'])


if __name__ == '__main__':
    unittest.main()
