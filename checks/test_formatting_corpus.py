"""The formatting corpus through every renderer (validation plan P1-P5, L5-L7).

Each content case and event type is checked in R1 (Codex mid-turn Markdown), R2 (Codex
final view), R3 (Claude Code final text) and R4 (the viewer window, under PowerShell 7
and Windows PowerShell 5.1). Known defects are marked expectedFailure with the reason,
so fixing one turns its test into an unexpected success to clean up.
"""
from pathlib import Path
import re
import shutil
import tempfile
import unittest

import formatting_corpus as corpus
import presentation
import viewer

VIEW_REFERENCE = re.compile(r'(?m)^ {0,3}(visualize\{|::codex-inline-vis)')


def ran(message_ids=True, progress='activity'):
    key = (message_ids, progress)
    if key not in ran.cache:
        folder = Path(tempfile.mkdtemp(prefix='cmc-'))
        ran.cache[key] = corpus.render_r1_r3(corpus.events(message_ids), folder, progress)
        ran.folders.append(folder)
    return ran.cache[key]


ran.cache, ran.folders = {}, []


def tearDownModule():
    for folder in ran.folders:
        shutil.rmtree(folder, ignore_errors=True)


def html_body(html):
    return re.sub(r'<style>.*?</style>', '', html, flags=re.S)


class Fixture(unittest.TestCase):
    def test_fixture_files_match_the_builder(self):
        for ids in (True, False):
            self.assertEqual(corpus.read_events(corpus.fixture(ids)), corpus.events(ids),
                             'run: python checks/formatting_corpus.py --write-fixtures')

    def test_every_event_type_is_present(self):
        kinds = {event['type'] for event in corpus.events()}
        self.assertEqual(kinds, {'plan', 'message', 'activity', 'artifact', 'usage', 'context_warning', 'error', 'done'})


class Markdown(unittest.TestCase):
    """R1 and R3 carry the agent's words verbatim, apart from view references and control characters."""

    def check_verbatim(self, text):
        for name, case in corpus.CASES.items():
            if name in ('view-reference', 'crlf', 'escapes'):
                continue
            with self.subTest(case=name):
                self.assertIn(case.strip('\n'), text)

    def test_r1_verbatim(self):
        for ids in (True, False):
            self.check_verbatim(ran(ids)['r1'])

    def test_r3_verbatim(self):
        for ids in (True, False):
            self.check_verbatim(ran(ids)['r3'])

    def test_view_references_are_defused(self):
        for key in ('r1', 'r3'):
            text = ran()[key]
            self.assertNotRegex(text, VIEW_REFERENCE)
            self.assertNotIn('\ue200visualize\ue202', text)
            self.assertIn('\u200bvisualize{', text)

    def test_crlf_rows_arrive_as_lines(self):
        for key in ('r1', 'r3'):
            self.assertIn('First CRLF line', ran()[key])
            self.assertIn('Second CRLF line', ran()[key])

    def test_preamble_is_its_own_paragraph_without_message_ids(self):
        """F13: Grok, Copilot and Antigravity send no IDs; tool work separates the preamble from the answer."""
        for key in ('r1', 'r3'):
            text = ran(False)[key]
            self.assertRegex(text, re.escape(corpus.PREAMBLE) + r'\n\n+# H1 title')

    def test_r1_passing_says_and_work_line(self):
        text = ran()['r1']
        self.assertTrue(text.startswith('**Passing to Antigravity AGY-4K...**'))
        self.assertEqual(text.count('Antigravity AGY-4K says...'), 1)
        self.assertIn('_Antigravity AGY-4K work: 1 running · 2 done · 1 failed · plan 1/3: Write the answer', text)
        self.assertIn('Artifact: report.txt', text)
        self.assertIn('**Search for multiply did not finish.**', text)
        self.assertIn('**Context is 80% full.**', text)

    def test_r3_green_attribution_within_the_latex_guard(self):
        text = ran()['r3']
        first = text.split('\n', 1)[0]
        self.assertEqual(presentation.plain_strong(first), '**Antigravity AGY-4K says...**')
        for span in re.findall(r'\$([^$]*)\$', first):
            self.assertLessEqual(len(span), presentation.LATEX_MAX)
            self.assertNotRegex(span, r'[#@"`]')
        self.assertIn('Context: 41,234 / 200,000 tokens (20.6%)', text)  # R3's work line carries usage.
        self.assertIn('**Search for multiply did not finish.**', text)

    def test_r3_color_off_is_plain_bold(self):
        folder = Path(tempfile.mkdtemp(prefix='cmc-'))
        ran.folders.append(folder)
        text = corpus.render_r1_r3(corpus.events(), folder, color=False)['r3']
        self.assertTrue(text.startswith('**Antigravity AGY-4K says...**'))
        self.assertNotIn('\\color', text)

    def test_quiet_hides_work_and_usage(self):
        for key in ('r1', 'r3'):
            text = ran(True, 'quiet')[key]
            self.assertNotIn('work:', text)
            self.assertNotIn('Context: 41,234', text)
            self.assertIn('Artifact: report.txt', text)  # Artifacts and errors are not progress.
            self.assertIn('Search for multiply did not finish.', text)

    def test_agent_dollars_are_left_as_sent(self):
        """P3: CLI-MODE does not rewrite '$5 and $10'. Whether the desktop app draws it as maths is Phase 5."""
        self.assertIn('It costs $5 and $10; set $HOME and $PATH first.', ran()['r3'])
        self.assertEqual(corpus.latex_risks(corpus.CASES['dollars']), ['$5 and $', '$HOME and $'])

    def test_unclosed_fence_does_not_swallow_cli_mode_lines(self):
        """An answer ending inside a code fence is closed, so the Artifact, error and work lines after it
        are not code in the chat."""
        for key in ('r1', 'r3'):
            text = ran()[key]
            tail = text[text.index('echo unclosed'):]
            self.assertEqual(tail.count('```') % 2, 1, key + ': no closing fence before CLI-MODE lines')

    def test_terminal_escapes_are_stripped(self):
        """ESC and BEL from agent text never reach the chat or the view (R2 drew them as boxes), as in the viewer."""
        for key in ('r1', 'r3', 'r2'):
            self.assertNotIn('\x1b', ran()[key])
            self.assertNotIn('\x07', ran()[key])
            self.assertIn('Before after, red text moved.', html_body(ran()[key]) if key == 'r2' else ran()[key])


class Helpers(unittest.TestCase):
    def test_close_fence(self):
        import relay_view
        self.assertEqual(relay_view.close_fence('a\n```py\nx\n```\nb'), 'a\n```py\nx\n```\nb')  # Closed: unchanged.
        self.assertEqual(relay_view.close_fence('```bash\necho\n'), '```bash\necho\n```')
        self.assertEqual(relay_view.close_fence('~~~\nx'), '~~~\nx\n~~~')
        self.assertEqual(relay_view.close_fence('````\n```\nstill code'), '````\n```\nstill code\n````')
        self.assertEqual(relay_view.close_fence('plain `code` only'), 'plain `code` only')

    def test_clean_keeps_text_and_line_breaks(self):
        import relay_view
        self.assertEqual(relay_view.clean('a\x1b]0;title\x07b\x1b[1;31mc\x1b[0m\td\r\ne\x00'), 'abc\td\r\ne')


class View(unittest.TestCase):
    """R2: the bounded Markdown vocabulary; everything else stays literal and inert."""

    def body(self, ids=True, progress='activity'):
        return html_body(ran(ids, progress)['r2'])

    def test_supported_blocks(self):
        html = self.body()
        for level in range(1, 7):
            self.assertIn('<h%d>H%d title</h%d>' % (level, level, level), html)
        self.assertIn('<ul><li>Changes<ul><li>Tests<ul><li>Three passed</li></ul></li></ul></li><li>Docs</li></ul>', html)
        self.assertIn('<ol><li>First</li><li>Second<ol><li>Second, part a</li></ol><ul><li>Second, note</li></ul>', html)
        self.assertIn('<th scope="col">Case</th>', html)
        self.assertIn('<td><strong>Pass</strong></td>', html)
        self.assertIn('<td><code>fail</code></td>', html)
        self.assertEqual(html.count('<div class="table-scroll"'), 3)  # Wide tables scroll inside their region.
        self.assertIn('<pre><code>def add(a, b):\n    return a + b</code></pre>', html)
        self.assertIn('<pre><code>{&quot;ok&quot;: true, &quot;items&quot;: [1, 2]}</code></pre>', html)
        self.assertIn('<code>pytest -q</code>', html)
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<blockquote>Quoted advice from the agent.</blockquote>', html)
        self.assertIn('<pre><code>echo unclosed</code></pre>', html)  # An unclosed fence ends with the answer.

    def test_literal_cases(self):
        """Outside the vocabulary: shown as typed (italic, strikethrough, task boxes, web links)."""
        html = self.body()
        self.assertIn('*italic*, _underscored_ and ~~struck~~', html)
        self.assertIn('<li>[x] Done task</li><li>[ ] Open task</li>', html)
        self.assertIn('[the docs](https://example.com/docs)', html)

    def test_file_links_become_inline_code(self):
        self.assertIn('and <code>calc.py</code>.', self.body())

    def test_untrusted_html_is_inert(self):
        html = self.body()
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<a ', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)

    def test_unicode_and_crlf(self):
        html = self.body()
        self.assertIn(corpus.CASES['unicode'], html)
        self.assertNotIn('\r', html)
        self.assertIn('<p>First CRLF line</p><p>Second CRLF line</p>', html)

    def test_last_message_only_and_preamble_already_posted(self):
        for ids in (True, False):
            html, update = self.body(ids), ran(ids)['r1']
            self.assertNotIn(corpus.PREAMBLE, html)
            self.assertIn(corpus.PREAMBLE, update)

    def test_work_is_nested_with_relative_rows_and_full_paths_in_details(self):
        html = self.body()
        self.assertIn('<details class="work"><summary>Antigravity AGY-4K work · 1 running · 2 done · 1 failed</summary>', html)
        self.assertIn('<summary>Plan · 1 of 3 done</summary>', html)
        self.assertRegex(html, r'title="Read calc\.py — [^"]*/workspace/calc\.py:1">Read calc\.py — calc\.py:1</span>')
        self.assertIn('>Run Python tests (unittest)</span>', html)
        self.assertIn('<p class="usage">Context: 41,234 / 200,000 tokens (20.6%)</p>', html)
        self.assertIn('<li>report.txt</li>', html)
        self.assertIn('<div class="error">Search for multiply did not finish.</div>', html)

    def test_quiet_view_has_no_work(self):
        html = self.body(True, 'quiet')
        self.assertNotIn('<details', html)
        self.assertNotIn('Context: 41,234', html)
        self.assertIn('<h1>H1 title</h1>', html)

    def test_fallback_text_cannot_open_a_view(self):
        """The view's plain-text fallback (`text` when done) carries the agent's words defused too."""
        text = ran()['r2Text']
        self.assertNotRegex(text, VIEW_REFERENCE)
        self.assertNotIn('\ue200visualize\ue202', text)

    def test_empty_turn(self):
        folder = Path(tempfile.mkdtemp(prefix='cmc-'))
        ran.folders.append(folder)
        out = corpus.render_r1_r3([dict(type='done', stopReason='end_turn')], folder)
        self.assertIn('Antigravity AGY-4K finished without public output.', out['r2'])
        self.assertIn('_Antigravity AGY-4K finished without public output._', out['r3'])


@unittest.skipUnless(viewer.shell(), 'PowerShell is not installed')
class Window(unittest.TestCase):
    """R4 under each PowerShell the viewer can pick."""
    SHELLS = [path for path in dict.fromkeys([shutil.which('pwsh'), str(Path(
        r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'))]) if path and Path(path).is_file()]

    @classmethod
    def setUpClass(cls):
        cls.outputs = {}
        for shell in cls.SHELLS:
            for ids in (True, False):
                folder = Path(tempfile.mkdtemp(prefix='cmv-'))
                ran.folders.append(folder)
                cls.outputs[shell, ids] = corpus.render_r4(corpus.events(ids), folder, shell, seconds=3)

    def each(self):
        for (shell, ids), result in self.outputs.items():
            with self.subTest(shell=Path(shell).name, messageIds=ids):
                yield result

    def test_ran_and_closed(self):
        for plain, raw, err, code, beat in self.each():
            self.assertEqual(code, 0, err)
            self.assertIn('Viewer turned off', plain)
            self.assertFalse(beat)  # L9: the heartbeat goes with the window.

    def test_every_event_type_is_drawn(self):
        for plain, *_ in self.each():
            for expected in ('Plan  1/3', '✓ Read calc.py', '▸ Write the answer', '○ Check the tests',
                             corpus.PREAMBLE, 'C:/work/garden-app/calc.py:1', '✓ Run     Python tests (unittest)',
                             '✗ Edit', '◐ Search  for multiply', 'Attachment (resource_link)  report.txt',
                             'Attachment (image)', '! Context is 80% full.', '✗ Search for multiply did not finish.',
                             '✓ Done', 'context 41,234 / 200,000 (21%)'):
                self.assertIn(expected, plain)

    def test_markdown_cases(self):
        for plain, *_ in self.each():
            for expected in ('H1 title', 'H6 title', '• Changes', '    • Tests', '• Three passed', '1. First',
                             '1. Second, part a', '☑ Done task', '☐ Open task', 'Case  │ Result', 'Empty │ Pass',
                             'Left      │ Centre │ Right', '─ python', '│ def add(a, b):', '│     return a + b',
                             '│ Quoted advice from the agent.', 'See the docs and calc.py.',
                             '<script>alert(1)</script>', 'Run pytest -q with bold, *italic*',
                             'It costs $5 and $10; set $HOME and $PATH first.', 'First CRLF line', '│ echo unclosed'):
                self.assertIn(expected, plain)
            self.assertNotIn('#', re.sub(r'.*(evil|\$).*', '', plain))  # Heading marks are drawn, not shown.

    def test_unicode_survives_both_shells(self):
        for plain, *_ in self.each():
            self.assertIn(corpus.CASES['unicode'], plain)

    def test_escape_sequences_are_stripped(self):
        for plain, raw, *_ in self.each():
            self.assertIn('Before after, red text moved.', plain)
            self.assertNotIn('\x1b]', raw)
            self.assertNotIn('\x07', raw)
            self.assertNotIn('evil title', raw)

    def test_long_lines_wrap_within_the_window(self):
        for plain, *_ in self.each():
            lines = plain.splitlines()
            self.assertTrue(all(len(line) <= 110 for line in lines), max(lines, key=len))
            self.assertIn('word119 end.', plain)
            self.assertGreaterEqual(sum('x' * 60 in line for line in lines), 2)  # A long token is hard-split.

    def test_table_columns_line_up(self):
        for plain, *_ in self.each():
            rows = [line for line in plain.splitlines() if '│' in line and ('Left' in line or 'long left' in line)]
            self.assertEqual(len({line.index('│') for line in rows}), 1, rows)


if __name__ == '__main__':
    unittest.main()
