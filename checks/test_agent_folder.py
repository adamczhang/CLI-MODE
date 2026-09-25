"""Agent working folders: what an agent saves outside the project's own files, on both hosts."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_controller import FakeBackend, hook
import agent_folder
from controller import Controller
import host
from state import Store, agent_label


def git(folder, *args):
    return subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True).stdout


class Saving(FakeBackend):
    """An agent that, during its turn, runs `self.work(project, name)`; what it was sent is in `sent`."""
    def __init__(self, folder):
        super().__init__()
        self.folder = folder
        self.work = None

    def start(self, owned, args, timeout=60):
        if '--file' in args and self.work:
            self.work(self.folder, owned['alias'])
        return super().start(owned, args, timeout)


class Folder(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)

    def test_the_folder_is_made_and_kept_out_of_git(self):
        folder = agent_folder.ensure(self.project, 'ART')
        self.assertEqual(folder, self.project / 'Agent_Working_Folder' / 'ART')
        self.assertTrue(folder.is_dir())
        self.assertIn('\n*\n', (self.project / 'Agent_Working_Folder' / '.gitignore').read_text(encoding='utf-8'))
        self.assertEqual(agent_folder.ensure(self.project, 'ART'), folder)  # Again: nothing changes.

    def test_only_agent_names_make_folders(self):
        for name in ('', None, '../ART', 'A/B', '-ART', 'ART PROJECT'):
            self.assertIsNone(agent_folder.ensure(self.project, name))
        self.assertFalse((self.project / 'Agent_Working_Folder').exists())

    def test_the_instruction_names_the_folder(self):
        line = agent_folder.instruction('COD-7K')
        self.assertIn('`Agent_Working_Folder/COD-7K/`', line)
        self.assertIn('If this task is coding, change the project\'s files as asked.', line)

    def test_new_changed_and_removed_files(self):
        folder = agent_folder.ensure(self.project, 'ART')
        (folder / 'keep.txt').write_text('a', encoding='utf-8')
        (folder / 'edit.txt').write_text('a', encoding='utf-8')
        (folder / 'gone.txt').write_text('a', encoding='utf-8')
        before = agent_folder.listing(folder)
        (folder / 'edit.txt').write_text('longer', encoding='utf-8')
        (folder / 'gone.txt').unlink()
        (folder / 'marble').mkdir()
        (folder / 'marble' / 'face-1.png').write_bytes(b'\x89PNG' + bytes(100))
        saved = agent_folder.compare('ART', before, agent_folder.listing(folder))
        self.assertEqual({item['path']: item['status'] for item in saved['paths']},
                         {'edit.txt': 'changed', 'gone.txt': 'removed', 'marble/face-1.png': 'new'})
        self.assertEqual((saved['saved'], saved['removed'], saved['folder']), (2, 1, 'Agent_Working_Folder/ART'))
        self.assertIsNone(agent_folder.compare('ART', before, before))  # Nothing changed: no line.

    def test_counts_read_naturally(self):
        def counts(saved, removed):
            return agent_folder.counts(dict(saved=saved, removed=removed))
        self.assertEqual(counts(1, 0), 'saved 1 file')
        self.assertEqual(counts(3, 1), 'saved 3 files and removed 1')
        self.assertEqual(counts(0, 2), 'removed 2 files')

    def test_a_very_full_folder_is_summarised_without_false_removals(self):
        folder = agent_folder.ensure(self.project, 'ART')
        for index in range(5):
            (folder / (str(index) + '.txt')).write_text('a', encoding='utf-8')
        with patch.object(agent_folder, 'LIMIT', 3):
            before = agent_folder.listing(folder)
            self.assertIsNone(agent_folder.compare('ART', before, agent_folder.listing(folder)))
            (folder / '00-new.txt').write_text('a', encoding='utf-8')  # Pushes 2.txt past the cut-off.
            saved = agent_folder.compare('ART', before, agent_folder.listing(folder))
        self.assertTrue(before['truncated'])
        self.assertEqual((saved['partial'], saved['paths']), (True, []))  # Not "2.txt removed".
        import relay_view
        self.assertEqual(relay_view.saved_markdown('ART', saved),
                         'ART changed files in `Agent_Working_Folder/ART/` (too many files there to list them).')


class Turns(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / 'game'
        self.project.mkdir()
        git(self.project, 'init', '-q')
        (self.project / 'app.py').write_text('one\n', encoding='utf-8')
        git(self.project, 'add', '.')
        git(self.project, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'start')
        self.start(self.project)

    def start(self, project, thread='folders'):
        self.backend = Saving(project)
        self.store = Store(thread, project, self.root / ('state-' + thread))
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')
        state = self.store.read()
        self.label, self.name = agent_label(state), state['owned'][0]['alias']
        self.folder = 'Agent_Working_Folder/' + self.name

    def turn(self, text='/d draw four cube themes'):
        hook.handle(dict(session_id=self.store.thread, cwd=str(self.store.workspace),
                         hook_event_name='UserPromptSubmit', prompt=text), self.store.root)
        request = self.store.read()['turnRoute']['requestId']
        self.control.send_request(request, output=lambda event: None)
        return request

    def test_the_agent_is_told_its_folder_and_the_chat_keeps_the_users_words(self):
        request = self.turn()
        self.assertTrue(self.backend.sent[-1].startswith('draw four cube themes\n\n---\nCLI-MODE: '))
        self.assertIn('`' + self.folder + '/`', self.backend.sent[-1])
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)
        self.assertNotIn('Agent_Working_Folder', self.control.relay_text(request, wait=0)['text'])  # Nothing saved.
        self.assertIsNone(self.store.read()['requests'][request].get('saved'))
        self.assertTrue((self.project / self.folder).is_dir())  # Made when the task named it.

    def test_an_agents_own_command_leaves_the_project_untouched(self):
        self.turn('/d /context')
        self.assertEqual(self.backend.sent[-1], '/context')
        self.assertFalse((self.project / 'Agent_Working_Folder').exists())

    def saving_art(self, project, name):
        folder = project / 'Agent_Working_Folder' / name
        (folder / 'marble').mkdir()
        (folder / 'marble' / 'face-1.svg').write_text('<svg/>', encoding='utf-8')
        (folder / 'notes.md').write_text('four themes\n', encoding='utf-8')
        (project / 'app.py').write_text('one\ntwo\n', encoding='utf-8')  # And a project edit.

    def test_saved_files_come_back_on_claude_code_apart_from_project_edits(self):
        self.backend.work = self.saving_art
        request = self.turn()
        record = self.store.read()['requests'][request]
        self.assertEqual([item['path'] for item in record['changes']['paths']], ['app.py'])  # Git: the project only.
        self.assertEqual(record['saved']['saved'], 2)
        self.assertNotIn('Agent_Working_Folder', git(self.project, 'status', '--porcelain'))
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)
        text = self.control.relay_text(request, wait=0)['text']
        self.assertIn(self.label + ' saved 2 files in `' + self.folder + '/`: `marble/face-1.svg` new · '
                      '`notes.md` new', text)
        lines = []
        self.control.follow(request, lines.append, poll=0)
        self.assertEqual(lines[-1], self.label + ' finished. It changed 1 file (+1 -0). It saved 2 files in ' +
                         self.folder + '/.')

    def test_a_deleted_ignore_file_never_turns_saved_files_into_removed_project_files(self):
        self.backend.work = self.saving_art
        self.turn()
        (self.project / 'Agent_Working_Folder' / '.gitignore').unlink()
        self.backend.work = None
        request = self.turn('/d anything else?')
        record = self.store.read()['requests'][request]
        self.assertEqual(record['changes']['files'], 0)  # Not "marble/face-1.svg removed".
        self.assertTrue((self.project / 'Agent_Working_Folder' / '.gitignore').exists())

    def test_codex_shows_saved_files_in_the_final_view(self):
        self.backend.work = self.saving_art
        request = self.turn()
        result = self.control.relay(request, wait=0, view_dir=self.root / 'views')
        html = Path(result['messageView']['path']).read_text(encoding='utf-8')
        self.assertIn('<code>marble/face-1.svg</code> new', html)
        self.assertIn(self.label + ' saved 2 files in ' + self.folder + '/.', result['text'])

    def test_outside_git_saved_files_are_still_reported(self):
        plain = self.root / 'research'
        plain.mkdir()
        self.start(plain, thread='plain')
        self.backend.work = lambda project, name: (
            project / 'Agent_Working_Folder' / name / 'engines.md').write_text('three.js\n', encoding='utf-8')
        request = self.turn('/d compare 3D engines')
        record = self.store.read()['requests'][request]
        self.assertNotIn('changes', record)  # No git receipt outside git...
        self.assertEqual(record['saved']['paths'], [dict(path='engines.md', status='new')])  # ...but this one.


if __name__ == '__main__':
    unittest.main()
