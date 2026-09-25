"""Change receipts: what an agent's turn changed in its folder, on both hosts, and /cli diff."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_controller import FakeBackend, hook
import changes
from controller import Controller
import host
from state import Store, agent_label


def git(folder, *args):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True)


class Editing(FakeBackend):
    """An agent that edits the folder during its turn."""
    def __init__(self, folder):
        super().__init__()
        self.folder = folder
        self.edit = None

    def start(self, owned, args, timeout=60):
        if '--file' in args and self.edit:
            self.edit(self.folder)
        return super().start(owned, args, timeout)


def work(folder):
    (folder / 'app.py').write_text('one\nTWO\nthree\n', encoding='utf-8')
    (folder / 'notes.md').write_text('new\n', encoding='utf-8')


class Snapshots(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / 'repo'
        (self.repo / 'sub').mkdir(parents=True)
        git(self.repo, 'init', '-q')
        (self.repo / 'sub' / 'app.py').write_text('one\ntwo\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'start')

    def test_only_the_turn_and_only_the_folder_count(self):
        folder = self.repo / 'sub'
        (folder / 'staged.txt').write_text('before the turn\n', encoding='utf-8')
        git(self.repo, 'add', 'sub/staged.txt')
        before = changes.snapshot(folder)
        work(folder)
        (self.repo / 'outside.txt').write_text('not the agent\'s folder\n', encoding='utf-8')
        receipt = changes.compare(folder, before, changes.snapshot(folder))
        self.assertEqual((receipt['files'], receipt['added'], receipt['removed']), (2, 3, 1))
        self.assertEqual([item['path'] for item in receipt['paths']], ['app.py', 'notes.md'])
        status = subprocess.run(['git', '-C', str(self.repo), 'status', '--porcelain'], capture_output=True,
                                text=True).stdout
        self.assertIn('A  sub/staged.txt', status)  # The user's staging area is untouched.
        self.assertIn(' M sub/app.py', status)
        self.assertIn('+three', changes.diff_text(folder, receipt))

    def test_outside_a_repository_there_is_no_snapshot(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(changes.snapshot(Path(plain)))
        self.assertIsNone(changes.compare(self.repo, None, 'x'))


class Receipts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / 'work'
        self.folder.mkdir()
        git(self.folder, 'init', '-q')
        (self.folder / 'app.py').write_text('one\ntwo\n', encoding='utf-8')
        git(self.folder, 'add', '.')
        git(self.folder, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'start')
        self.backend = Editing(self.folder)
        self.store = Store('receipts', self.folder, self.root / 'state')
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')
        self.label = agent_label(self.store.read())

    def turn(self, text='/d edit the app'):
        hook.handle(dict(session_id='receipts', cwd=str(self.folder), hook_event_name='UserPromptSubmit',
                         prompt=text), self.store.root)
        request = self.store.read()['turnRoute']['requestId']
        self.control.send_request(request, output=lambda event: None)
        return request

    def test_the_receipt_is_saved_and_relayed_in_colour_on_claude_code(self):
        self.backend.edit = work
        request = self.turn()
        receipt = self.store.read()['requests'][request]['changes']
        self.assertEqual((receipt['files'], receipt['added'], receipt['removed']), (2, 3, 1))
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)
        text = self.control.relay_text(request, wait=0)['text']
        self.assertIn(self.label + ' changed 2 files ', text)
        self.assertIn('\\color{228b22}\\small\\textsf{\\textbf{+3}}', text)  # Added: green.
        self.assertIn('\\color{cf222e}\\small\\textsf{\\textbf{-1}}', text)  # Removed: red.
        self.assertIn('`app.py`', text)
        (self.store.root / 'display.json').write_text('{"color": "off"}', encoding='utf-8')
        plain = self.control.relay_text(request, wait=0)['text']
        self.assertIn(self.label + ' changed 2 files +3 -1', plain)
        self.assertNotIn('\\color', plain)

    def test_codex_shows_it_in_the_final_view(self):
        self.backend.edit = work
        request = self.turn()
        result = self.control.relay(request, wait=0, view_dir=self.root / 'views')
        html = Path(result['messageView']['path']).read_text(encoding='utf-8')
        self.assertIn('<span class="add">+3</span> <span class="del">-1</span>', html)
        self.assertIn('<code>notes.md</code>', html)
        self.assertIn(self.label + ' changed 2 files (+3 -1)', result['text'])

    def test_no_edits_say_so_and_the_follow_row_ends_with_the_count(self):
        request = self.turn()
        self.assertEqual(self.store.read()['requests'][request]['changes']['files'], 0)
        self.assertIn(self.label + ' changed no files.', self.control.relay_text(request, wait=0)['text'])
        lines = []
        self.control.follow(request, lines.append, poll=0)
        self.assertEqual(lines[-1], self.label + ' finished. It changed no files.')

    def test_diff_shows_the_last_turn_as_a_diff_block(self):
        self.assertIn('no change receipt yet', self.control.diff()['text'])
        self.backend.edit = work
        self.turn()
        text = self.control.diff()['text']
        self.assertTrue(text.startswith(self.label + ' changed 2 files +3 -1\n\n```diff\n'))
        self.assertIn('+three', text)
        self.assertTrue(text.endswith('\n```'))

    def test_a_folder_outside_git_gets_no_receipt(self):
        with tempfile.TemporaryDirectory() as plain:
            store = Store('plain', Path(plain), self.root / 'state2')
            control = Controller(store, FakeBackend())
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            result = control.send('/d hello', output=lambda event: None)
            self.assertNotIn('changes', store.read()['requests'][result['requestId']])


if __name__ == '__main__':
    unittest.main()
