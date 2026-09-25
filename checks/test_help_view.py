"""The /help page is the same framed card as every other menu."""
import unittest

from test_controller import PLUGIN  # Adds the packaged scripts to the import path.
import help_view
import menu_view


class CardHelp(unittest.TestCase):
    def test_one_card_lists_each_command_with_its_description(self):
        page = help_view.render()
        self.assertTrue(page.startswith('```text\n+---'))
        rows = [line[2:-2].strip() for line in page.splitlines() if line.startswith('| ')]
        self.assertEqual(rows[:2], ['CLI-MODE', 'Help'])
        self.assertEqual(len(help_view.COMMANDS), 17)
        for command, _ in help_view.COMMANDS:
            self.assertIn(command, rows)
        self.assertEqual(rows[-1], 'X. Close help')
        html = menu_view.render(page)
        self.assertIn('class="command">/cli bind|spawn &lt;agent&gt; [name]', html)
        self.assertIn('class="exit">Close help', html)

    def test_agent_names_and_display_modes_are_explained(self):
        text = help_view.text()
        for agent in ('agy (Antigravity)', 'cla (Claude Code)', 'gro (Grok Build)', 'cur (Cursor)',
                      'cop (GitHub Copilot)', 'cod (Codex CLI)'):
            self.assertIn(agent, text)
        for mode in ('activity', 'quiet'):
            self.assertIn(mode, text)
        self.assertIn('Nothing else reaches an agent.', text)  # Only /d reaches an agent.
        self.assertIn('$ works in place of /', text)


if __name__ == '__main__':
    unittest.main()
