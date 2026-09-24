"""Regressions from the installed visual test: structure, escaping and paths."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_controller import PLUGIN
import menu_view
import relay_view
from progress import public_progress


class EmissionFormatting(unittest.TestCase):
    def test_block_structure_and_untrusted_content(self):
        text = ('## Results\n\n- Changes\n  - Tests\n    - Three passed\n\n'
                '| Case | Result |\n|---|---|\n| Empty | **Pass** |\n\n'
                '```python\nprint("<script>evil()</script>")\n```\n\n'
                '<img src=x onerror=evil()>\n[click](javascript:evil())')
        html = menu_view.block_content(text)
        self.assertIn('<h2>Results</h2>', html)
        self.assertIn('<ul><li>Changes<ul><li>Tests<ul>', html)
        self.assertIn('<table><thead>', html)
        self.assertIn('<td><strong>Pass</strong></td>', html)
        self.assertIn('<pre><code>print(&quot;&lt;script&gt;', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<a ', html)
        self.assertNotIn('```', html)

    def test_ordered_lists_and_unclosed_fences_remain_readable(self):
        html = menu_view.block_content('1. One\n2. Two\n   - Nested\n\n```\n**literal**\n<x>')
        self.assertIn('<ol><li>One</li><li>Two<ul>', html)
        self.assertIn('<pre><code>**literal**\n&lt;x&gt;</code></pre>', html)

    def test_workspace_relative_rows_preserve_full_path_in_detail(self):
        event = dict(type='activity', toolCallId='r', kind='read', status='completed',
                     title='Read source', locations=[dict(path='C:/work/src/app.py', line=9)])
        with tempfile.TemporaryDirectory() as folder:
            path, _, _ = relay_view.render('Agent', [event], Path(folder) / 'view.html', workspace='C:/work')
            html = Path(path).read_text(encoding='utf-8')
        self.assertIn('title="Read source — C:/work/src/app.py:9"', html)
        self.assertIn('>Read source — src\\app.py:9</span>', html)
        self.assertEqual(relay_view.display_path('C:/work-other/a.py', 'C:/work'), 'C:/work-other/a.py')
        self.assertEqual(relay_view.display_path('/work/src/a.py', '/work'), 'src/a.py')

    def test_activation_has_semantic_rows_without_space_alignment(self):
        text = ('**CLI-MODE Activated**\n\n**Model:** Example | **Effort:** High | '
                '**Access:** Allow | **Question:** `/help`\n\n'
                '**Utilization:** Five hour: 4% used\n             Weekly: 10% used')
        html = menu_view.activation_content(text)
        self.assertIn('<dt>Model</dt><dd>Example</dd>', html)
        self.assertIn('<li>Weekly: 10% used</li>', html)
        self.assertNotIn('             ', html)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_command_identity_survives_both_boundaries_without_arguments(self):
        script = ('import {createProgressProjector} from ' +
                  json.dumps((PLUGIN / 'scripts/public-progress.mjs').as_uri()) + ';' +
                  'const p=createProgressProjector(); console.log(JSON.stringify(['
                  'p({sessionUpdate:"tool_call",toolCallId:"a",kind:"execute",title:"Bash",'
                  'rawInput:{command:"cd \\"C:/work space\\" && python -m unittest -v --secret=PRIVATE"}}),'
                  'p({sessionUpdate:"tool_call_update",toolCallId:"a",status:"completed"})]));')
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                                capture_output=True, text=True, check=True, timeout=10)
        self.assertNotIn('PRIVATE', result.stdout)
        events = json.loads(result.stdout)
        self.assertEqual(events[-1]['title'], 'Run Python tests (unittest)')
        self.assertEqual([public_progress(event) for event in events], events)
