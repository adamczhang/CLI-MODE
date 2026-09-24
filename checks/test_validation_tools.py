"""The live-validation tools themselves, offline: the shared scenario, Codex view read-back, the report."""
import json
from pathlib import Path
import tempfile
import unittest

from test_controller import PLUGIN  # noqa: F401  (puts the plugin's scripts on the path)
import codex_user_validation
import menu_view
import presentation
import validation_report
import validation_scenarios as scenarios


def menu(title, rows):
    return presentation.menu_block('CLI-MODE\n' + title + '\n\n' + '\n'.join(rows))


class FakeHost:
    """Answers each prompt the way the installed plugin would, closely enough for the checks."""
    host = 'claude-code'

    def __init__(self, project):
        self.project = project
        self.data = dict(active=False, requests={})
        self.sent = []

    def state(self):
        return self.data

    def words(self, request):
        return 'Done: ' + request

    def reply(self, prompt):
        d = self.data
        if prompt in ('/cli help', '/help'):
            return menu('Help', ['/cli  Open the menu'])
        if prompt == 'x':
            return 'Help closed.' if self.sent[-2:-1] in (['/cli help'], ['/help']) else 'Closed.'
        last = self.last
        if prompt == '/cli':
            return menu('Setup CLI Agent', ['1. Antigravity CLI: Installed', '2. Codex CLI: Installed'])
        if prompt.isdigit():
            if 'Setup CLI Agent' in last:
                return menu('Agent Settings', ['1. Yes, use these defaults', '2. Change defaults'])
            if 'Change defaults' in last:
                return menu('Select Model', ['1. gpt-big  (current)', '2. gpt-mini', 'R. Refresh models'])
            if 'Select Model' in last and not d['active']:
                return menu('Select Reasoning Effort', ['1. Low', '2. Extra High'])
            if 'Reasoning' in last:
                return menu('Select Access Level', ['1. Allow', '2. Prompt'])
            if 'Access Level' in last:
                d.update(active=True, backend='codex')
                return '**CLI-MODE Activated**\n\n**Model:** gpt-big | **Effort:** Low | **Access:** Allow\n\n**Utilization:** 1%'
            if 'Passthrough' in last:
                return 'Routing is Passthrough.'
            if prompt == '1':
                return menu('Select Model', ['1. gpt-big  (current)', '2. gpt-mini'])
            return menu('Routing', ['1. Passthrough', '2. Direct'])
        if prompt.startswith('/cli bind codex'):
            d.update(active=True, backend='codex')
            return '**CLI-MODE Activated**\n\n**Model:** gpt-big | **Effort:** Low | **Access:** Allow\n\n**Utilization:** 1%'
        if prompt.startswith('/cli bind'):
            return 'CLI-MODE: unknown agent'
        if prompt in ('/cli menu', '$CLI MENU'):
            return menu('Agent Settings', ['Model: gpt-big', '1. Model', '4. Routing mode', '5. Progress'])
        if prompt.startswith('/cli model') or prompt.startswith('/cli effort'):
            return 'CLI-MODE Activated'
        if prompt.startswith('/cli mode'):
            return 'Routing is Direct.'
        if not d['active'] and prompt.startswith('/d'):
            return '/cli to activate.'
        if prompt == '/d':
            return 'Nothing was sent.'
        if prompt.startswith('/d /model'):
            return 'Use /cli model instead.'
        if prompt.startswith('/d') or prompt.startswith('Reply with only'):
            request = '%032x' % len(self.sent)
            d['requests'][request] = {}
            d['turnRoute'] = dict(route='direct', requestId=request)
            if 'multiply' in prompt:
                (self.project / 'test_calc.py').write_text('', encoding='utf-8')
            work = '' if d.get('quiet') else '\n\n_Codex work: 1 done_'
            return '**Passing to Codex...**\n\n**Codex says...**\n\nDone: ' + request + work + '\nQueued behind ' + \
                ' '.join('Done: ' + r for r in d['requests'])
        if prompt.startswith('/cli progress'):
            d['quiet'] = prompt.endswith('quiet')
            return 'Progress set.'
        if prompt.startswith('/cli display'):
            return 'CLI-MODE uses instant replies.' if 'instant' in prompt else 'CLI-MODE uses normal chat messages.'
        if prompt.startswith('/cli color'):
            return 'CLI-MODE titles and names are now ' + ('green.' if prompt.endswith('on') else 'plain bold.')
        if prompt in ('/cli queue', '/cli resume'):
            if not d['active']:
                return '/cli to activate.'
            return 'CLI-MODE queue\nWorker: idle\n' + '\n'.join('  %s  completed' % r[:8] for r in d['requests']) + \
                '\n' + '\n'.join('Done: ' + r for r in d['requests'])
        if prompt.startswith('What is'):
            return '42'
        if prompt == '/cli frobnicate':
            return 'Say /cli help to see options.'
        if prompt == '/cli stop':
            was = d['active']
            d.update(active=False, owned=[])
            return 'CLI-MODE is off. The agent session was closed.' if was else 'CLI-MODE is off.'
        return ''

    last = ''

    def send(self, prompt, scenario, kill_after=None):
        self.sent.append(prompt)
        text = self.reply(prompt)
        self.last = text
        instant = prompt.startswith('/cli display instant') or (self.sent[-2:-1] == ['/cli display instant'])
        if instant and prompt == '/cli menu':
            text = presentation.unfence(text)
        return dict(texts=[text], result=text, problems=[], notes=[], modelTurns=0 if instant else 1,
                    controllerCalls=0 if prompt.startswith('What') else 1)

    def shown(self, turn):
        return presentation.plain_strong('\n\n'.join(turn['texts']))


class Scenario(unittest.TestCase):
    def run_fake(self, depth, host='claude-code'):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeHost(Path(folder))
            fake.host = host
            return scenarios.run_steps(fake, 'codex', depth), fake

    def test_full_depth_runs_every_step_with_placeholders_filled(self):
        results, fake = self.run_fake('full')
        failed = [(r['step'], r['problems']) for r in results if r['status'] == 'fail']
        self.assertEqual(failed, [])
        self.assertIn('2', fake.sent)  # The model was chosen by its number, the default one.
        self.assertIn('/cli model gpt-mini', fake.sent)  # The cheapest-looking alternate.
        self.assertIn('/cli model gpt-big', fake.sent)  # And straight back.
        self.assertIn('/d /status', fake.sent)  # Codex's read-only native command.
        self.assertTrue(all('{' not in prompt for prompt in fake.sent), fake.sent)

    def test_smoke_depth_is_the_short_path(self):
        results, fake = self.run_fake('smoke')
        self.assertEqual(fake.sent[0], '/cli bind codex')
        self.assertEqual([r['step'] for r in results],
                         ['bind', 'models-menu', 'models-list', 'models-x', 'model-direct', 'model-back', 'coding', 'stop'])

    def test_codex_host_skips_claude_display_steps(self):
        results, fake = self.run_fake('full', 'codex')
        self.assertNotIn('/cli display instant', fake.sent)
        self.assertEqual(fake.sent[0], '/help')

    def test_every_step_names_known_features(self):
        for item in scenarios.STEPS:
            self.assertTrue(set(item['features']) <= set(scenarios.FEATURES), item['id'])


class Options(unittest.TestCase):
    def test_wrapped_labels_are_joined(self):
        block = menu('Select Model', ['1. A very long model name that wraps past forty columns  (current)',
                                      '2. short', 'R. Refresh models'])
        rows = scenarios.options(presentation.chat_menu(block, True))
        self.assertEqual((rows[0][0], ' '.join(rows[0][1].split())),
                         ('1', 'A very long model name that wraps past forty columns (current)'))
        self.assertEqual(rows[1], ('2', 'short'))


class CodexViews(unittest.TestCase):
    def test_menu_view_reads_back_as_numbered_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = menu_view.write(menu('Select Model', ['1. gpt-big  (current)', '2. gpt-mini']),
                                   Path(folder) / 'm.html')
            text = codex_user_validation.view_text(path)
        self.assertIn('Select Model', text)
        self.assertEqual(scenarios.options(text), [('1', 'gpt-big  (current)'), ('2', 'gpt-mini'), ])


class Report(unittest.TestCase):
    def test_matrix_marks_fail_over_pass_and_lists_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / 'claude-codex'
            run.mkdir()
            (run / 'summary.json').write_text(json.dumps(dict(host='claude-code', agent='codex', results=[
                dict(step='help', features=['K3'], status='pass'),
                dict(step='stop', features=['I1'], status='fail', problems=['still active'])])), encoding='utf-8')
            (Path(folder) / 'rows.jsonl').write_text(json.dumps(dict(
                feature='I1', host='claude-code', agent='codex', status='pass')) + '\n', encoding='utf-8')
            text = validation_report.markdown(validation_report.rows(folder))
        self.assertIn('| K3 help card | P |', text)
        self.assertIn('| I1 /cli stop | F |', text)
        self.assertIn('**I1** claude-code/codex (stop): still active', text)
        self.assertIn('- A1 Codex install from GitHub', text)


if __name__ == '__main__':
    unittest.main()
