"""Width and content guarantees for plain menus; not a chat-rendering simulator."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_controller import PLUGIN
import agy
import frontends
import help_view
import menu_view
from presentation import menu_block, menu_frame, MENU_WIDTH


class Presentation(unittest.TestCase):
    def assert_block(self, text, exit='X. Exit'):
        rows = text.splitlines()
        self.assertEqual(rows[0], '```text')
        self.assertEqual(rows[-1], '```')
        self.assertEqual(rows[-3].strip('| '), exit)
        self.assertEqual(text.count(exit), 1)
        self.assert_frame('\n'.join(rows[1:-1]))

    def assert_frame(self, text):
        rows = text.splitlines()
        self.assertEqual(rows[0], '+' + '-' * (MENU_WIDTH - 2) + '+')
        self.assertEqual(rows[-1], rows[0])
        self.assertTrue(all(len(row) == MENU_WIDTH for row in rows))
        self.assertTrue(all(row == rows[0] or row.startswith('| ') and row.endswith(' |') for row in rows[1:-1]))
        self.assertNotIn('```', text)
        self.assertNotIn('\x1b', text)

    def test_all_builtin_menus_fit_before_and_after_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            for confirmed in (False, True):
                if confirmed:
                    frontends.check_and_save(root, 'agy', check=lambda agent: dict(confirmed=True))
                for agent in ('home', 'agy'):
                    self.assert_block(frontends.menu(root, agent))
                for access in ('allow', 'prompt'):
                    settings = agy.selection(root, 'gemini-pro-agent', access)
                    menu = frontends.menu(root, 'agy', settings)
                    self.assert_block(menu)
                    self.assertIn('Model: Gemini 3.1 Pro' if confirmed else 'New User Detected.', menu)
            self.assert_block(help_view.render(), exit='X. Close help')
            for command in ('/cli bind|spawn <agent> [name]', '/cli progress <mode>', '/help'):
                self.assertIn(command, help_view.render())

    def test_headers_have_a_full_width_divider_before_body(self):
        from presentation import menu_block
        for title in ('Select CLI Agent', 'Agent Settings', 'Select Model',
                      'Select Reasoning Effort', 'Select Access Level', 'Commands'):
            text = menu_block('CLI-MODE\n' + title + '\n1. First choice')
            self.assert_block(text)
            rows = text.splitlines()
            self.assertEqual(rows[2].strip('| '), 'CLI-MODE')
            self.assertEqual(rows[3].strip('| '), title)
            self.assertEqual(rows[4], rows[1])
            self.assertIn('1. First choice', rows[5])

    def test_wrap_preserves_words_long_tokens_blank_rows_and_choice_labels(self):
        token = 'x' * 107
        text = 'Model menu\n\n1. A model name with a very long descriptive label\n2. ' + token
        framed = menu_frame(text)
        self.assert_frame(framed)
        unframed = '\n'.join(row[2:-2].rstrip() for row in framed.splitlines()[1:-1])
        self.assertEqual(''.join(text.split()), ''.join(unframed.split()))
        self.assertIn('| ' + ' ' * 36 + ' |', framed)

    def test_all_cached_choice_sets_and_templates_fit(self):
        with tempfile.TemporaryDirectory() as root:
            catalog = agy.catalog(root)
            groups = [catalog['modelFamilies'], catalog['accessControl']['options']]
            groups += [family['efforts'] for family in catalog['modelFamilies']]
            for group in groups:
                labels = [item.get('name', item.get('access')) for item in group]
                self.assert_frame(menu_frame('\n'.join(f'{i}. {label}' for i, label in enumerate(labels, 1))))
        samples = (PLUGIN / 'backends/agy/assets/setup-menus.txt').read_text(encoding='utf-8')
        for sample in samples.split('Show one phase at a time.'):
            self.assert_block(sample.strip())

    def test_generated_menu_cli_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'menu.txt'
            source.write_text('Choose access\n1. allow - Automatically approve all permitted tools\nB. Back', encoding='utf-8')
            data = root / 'data'
            command = [sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'presentation',
                       '--workspace', str(root), 'format-menu', '--file', str(source)]
            result = subprocess.run(command, env=dict(os.environ, CLI_MODE_DATA=str(data)),
                                    capture_output=True, text=True, timeout=10, check=True)
            self.assert_block(json.loads(result.stdout)['text'])
            self.assertFalse(data.exists())


if __name__ == '__main__': unittest.main()


def _luminance(value):
    parts = []
    for i in (0, 2, 4):
        channel = int(value.lstrip('#')[i:i + 2], 16) / 255
        parts.append(channel / 12.92 if channel <= 0.04045
                     else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def contrast(foreground, background):
    first, second = _luminance(foreground), _luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


class Accent(unittest.TestCase):
    """The accent must stay readable in both themes, everywhere it appears."""

    DARK_CHAT = '#1e1e1e'
    LIGHT_CHAT = '#ffffff'
    MENU_CARD = '#202124'

    def test_each_accent_clears_aa_on_its_own_theme(self):
        self.assertGreaterEqual(contrast(menu_view.ACCENT, self.DARK_CHAT), 4.5)
        self.assertGreaterEqual(contrast(menu_view.ACCENT_LIGHT, self.LIGHT_CHAT), 4.5)

    def test_a_single_accent_could_not_have_served_both(self):
        # Why there are two: each fails badly on the other theme.
        self.assertLess(contrast(menu_view.ACCENT, self.LIGHT_CHAT), 3)
        self.assertLess(contrast(menu_view.ACCENT_LIGHT, self.DARK_CHAT), 4.5)

    def test_the_menu_card_keeps_the_dark_accent(self):
        # Menus follow Codex's theme tokens; outside Codex they fall back to the
        # dark card, which keeps the dark accent (no light-scheme override).
        self.assertGreaterEqual(contrast(menu_view.ACCENT, self.MENU_CARD), 4.5)
        block = menu_view.render(menu_block(chr(10).join(['CLI-MODE', 'Commands', '', '1. One'])))
        self.assertIn(menu_view.ACCENT, block)
        self.assertNotIn(menu_view.ACCENT_LIGHT, block)
        self.assertNotIn('prefers-color-scheme', block)

    def test_messages_adapt_and_never_fade_the_accent(self):
        with tempfile.TemporaryDirectory() as folder:
            for kind in ('passing', 'agent'):
                path = menu_view.write_message('hello', Path(folder) / (kind + '.html'), 'Claude', kind)
                html = Path(path).read_text(encoding='utf-8')
                self.assertIn(menu_view.ACCENT, html, kind)
                self.assertIn('@media (prefers-color-scheme: light)', html, kind)
                self.assertIn(menu_view.ACCENT_LIGHT, html, kind)
                # Opacity would undo the contrast the two colours buy.
                self.assertNotIn('opacity', html, kind)

    def test_only_cli_mode_speaks_in_the_accent(self):
        with tempfile.TemporaryDirectory() as folder:
            chrome = Path(menu_view.write_message(
                'Passing to Claude...', Path(folder) / 'c.html', 'Claude', 'passing')).read_text(encoding='utf-8')
            relay = Path(menu_view.write_message(
                'a long answer', Path(folder) / 'r.html', 'Claude', 'agent')).read_text(encoding='utf-8')
        self.assertIn('{color:' + menu_view.GREEN + ';}', chrome)
        self.assertIn('color:inherit;', relay)
        self.assertIn('Claude says...', relay)
        self.assertNotIn('says...', chrome)
