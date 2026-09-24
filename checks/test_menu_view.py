"""Inline menu rendering, escaped content, and read-only CLI presentation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_controller import PLUGIN
import frontends
import menu_view
from presentation import menu_block


class MenuView(unittest.TestCase):
    def test_public_file_links_are_inline_code_and_activation_is_bold(self):
        path = 'C:/Users/example/Desktop/Angry Cats'
        for separator in ('(', '\\('):
            text = '[' + path + ']' + separator + 'file:///C:/Users/example/Desktop/Angry%20Cats)'
            self.assertEqual(menu_view.message_content(text), '<code>' + path + '</code>')
        self.assertEqual(menu_view.message_content('**CLI-MODE Activated**'),
                         '<strong>CLI-MODE Activated</strong>')
        self.assertEqual(menu_view.message_content('`<script>alert(1)</script>`'),
                         '<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>')
        literal = '```md\n[file](file:///tmp/file)\n**literal**\n```'
        self.assertEqual(menu_view.message_content(literal), literal)

    def test_public_unicode_survives_windows_console_encoding(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; sys.path.insert(0, sys.argv[1]); from controller import emit; "
            "emit({'text': chr(0x1f4e6) + chr(0x732b)})", str(PLUGIN / 'scripts')],
            env=dict(os.environ, PYTHONIOENCODING='cp1252'), capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)['text'], '\U0001f4e6\u732b')

    def test_requested_styles_and_conditional_setup_warnings(self):
        for hooks in (False, True):
            for access in (False, True):
                block = frontends.setup_menu(dict(confirmed=True, checks=[dict(name='ACPX', installed=True)]),
                                            dict(ready=hooks), dict(ready=access))
                html = menu_view.render(block)
                self.assertIn('class="title">CLI-MODE', html)
                self.assertIn('class="subtitle">Setup CLI Agent', html)
                self.assertIn('font-weight:700', html)
                self.assertIn('color:var(--card-foreground,#fff)', html)
                self.assertIn('color:var(--green,#42d392)', html)
                self.assertIn('color:var(--red,#ff4d4f)', html)
                self.assertIn('<span class="ok">Installed</span>', html)
                for label, ready in (('Hooks', hooks), ('Full Access', access)):
                    status = '<span class="ok">On</span>' if ready else '<span class="warning">!Attention!</span>'
                    self.assertIn(label + ': ' + status, html)
                self.assertEqual('**Desktop:' in html, not hooks)
                self.assertEqual('*Full access in codex is required*' in html, not access)
                self.assertIn('X. <span class="exit">Exit</span>', html)
                self.assertIn('<div class="body">\n<div class="row">Antigravity CLI', html)

    def test_provider_text_cannot_become_markup_or_select_status_colors(self):
        html = menu_view.render(menu_block('CLI-MODE\nSelect Model\n1. <script>alert(1)</script>\n2. On'))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('class="ok"', html)
        self.assertNotIn('fetch(', html)
        self.assertNotIn('onclick=', html)
        self.assertIn('overflow-wrap:anywhere', html)

    def test_message_cli_preserves_text_and_escapes_provider_markup_without_state(self):
        from html.parser import HTMLParser
        class Content(HTMLParser):
            def __init__(self):
                super().__init__(); self.inside = False; self.text = ''
            def handle_starttag(self, tag, attrs):
                if tag == 'div' and ('class', 'body') in attrs: self.inside = True
            def handle_endtag(self, tag):
                if tag == 'div': self.inside = False
            def handle_data(self, value):
                if self.inside: self.text += value
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            text = 'Antigravity:\n\n  <script>alert(1)</script> & answer\n```py\n  x = 1\n```'
            source = root / 'public.txt'; source.write_text(text, encoding='utf-8')
            output = root / 'message.html'
            result = subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'),
                '--thread', 'message-view-test', '--workspace', str(root), '--message-output', str(output),
                'format-message', '--file', str(source)],
                env=dict(os.environ, CLI_MODE_DATA=str(root / 'state')), capture_output=True, text=True, check=True)
            value = json.loads(result.stdout)
            self.assertEqual(Path(value['messageView']['path']), output)
            html = output.read_text(encoding='utf-8')
            parser = Content(); parser.feed(html)
            # Markdown delimiters become semantic HTML; literal provider HTML
            # and code content remain escaped and readable.
            self.assertEqual(parser.text, 'Antigravity:  <script>alert(1)</script> & answer  x = 1')
            self.assertIn('<pre><code>  x = 1</code></pre>', html)
            self.assertIn('<p>Antigravity:</p>', html)
            self.assertNotIn('<script>', html)
            self.assertIn('color:var(--green,#42d392)', html)
            self.assertIn('background:transparent', html)
            self.assertFalse((root / 'state').exists())

    def test_help_command_returns_plain_text_without_writing_a_view_or_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'views/menu.html'
            result = subprocess.run([sys.executable, str(PLUGIN / 'scripts/controller.py'),
                '--thread', 'view-test', '--workspace', str(root), '--menu-output', str(output), 'commands'],
                env=dict(os.environ, CLI_MODE_DATA=str(root / 'state')), capture_output=True, text=True, check=True)
            value = json.loads(result.stdout)
            # The same card as other menus, with its reference line; no state is written.
            self.assertTrue(output.exists())
            self.assertTrue(value['menuView']['reference'].startswith('\ue200visualize\ue202{'))
            self.assertIn('| Help ', value['text'])
            self.assertFalse((root / 'state').exists())


if __name__ == '__main__': unittest.main()
