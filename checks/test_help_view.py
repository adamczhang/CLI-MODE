"""The /help page: a compact card framed like every other menu, one short line per command, grouped by task."""
import unittest
from unittest.mock import patch

from test_controller import PLUGIN  # Adds the packaged scripts to the import path.
import help_view
import host
import menu_view


class CardHelp(unittest.TestCase):
    def test_one_line_per_command_grouped_under_headings(self):
        page = help_view.render()
        self.assertTrue(page.startswith('```text\n+---'))
        rows = [line[2:-2].rstrip() for line in page.splitlines() if line.startswith('| ')]
        self.assertEqual(rows[:3], ['CLI-MODE', 'Help', 'AGENTS'])
        for heading in ('AGENTS', 'SEND WORK', 'RESULTS', 'SETTINGS'):
            self.assertIn(heading, rows)
        for command, description in help_view.COMMANDS:
            self.assertIn(command.ljust(help_view.COLUMN) + description, rows)  # Never wrapped onto a second row.
        self.assertEqual(rows[-1], 'X. Close help')

    def test_it_stays_short_and_fits_the_phone(self):
        for which in (host.CODEX, host.CLAUDE):
            with patch.object(host, 'current', return_value=which):
                text = help_view.text()
                self.assertLess(len(help_view.render().splitlines()), 50)  # Was 95 on Claude Code.
            self.assertFalse([line for line in text.splitlines() if len(line) > 36])  # No row wraps.
        self.assertTrue(all(len(command) <= help_view.COLUMN - 2 for command, _ in help_view.COMMANDS))

    def test_codex_bolds_the_command_and_colours_headings(self):
        html = menu_view.render(help_view.render())
        self.assertIn('class="section">AGENTS', html)
        self.assertIn('class="command">/cli spawn &lt;agent&gt;</span>  start another', html)
        self.assertIn('class="exit">Close help', html)

    def test_agents_placeholders_and_the_d_rule_are_explained(self):
        text = help_view.text()
        for agent in ('agy (Antigravity)', 'cla (Claude Code)', 'gro (Grok Build)', 'cur (Cursor)',
                      'cop (GitHub Copilot)', 'cod (Codex CLI)'):
            self.assertIn(agent, text)
        self.assertIn('activity/quiet', text)
        self.assertIn('Only /d reaches an agent.', text)
        self.assertIn('<name>: a name, tag (gro) or -7K', text)
        self.assertIn('$ works in place of /', text)

    def test_claude_code_adds_its_own_settings(self):
        with patch.object(host, 'current', return_value=host.CLAUDE):
            text = help_view.text()
        for row in ('/cli display ...', '/cli color on|off', '/cli shortcuts, /cli reset',
                    '/cli help (this page)', '/cli-mode:cli if /cli clashes.'):
            self.assertIn(row, text)
        self.assertNotIn('/cli display', help_view.text())  # Not on Codex.


if __name__ == '__main__':
    unittest.main()
