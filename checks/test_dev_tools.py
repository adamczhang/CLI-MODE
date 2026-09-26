"""Undo, the same-file warning, the test gate and the shared brief, on both hosts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_agent_folder import Saving, git
from test_controller import hook
import agent_folder
import changes
from controller import Controller
import host
import relay_view
from state import Store, agent_label
import test_gate


def passing():
    return '"' + sys.executable + '" -c "print(\'====== 3 passed in 0.1s ======\')"'


def failing():
    return '"' + sys.executable + '" -c "import sys; print(\'1 failed, 2 passed\'); sys.exit(1)"'


class Project(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / 'game'
        self.project.mkdir()
        git(self.project, 'init', '-q')
        (self.project / 'app.py').write_text('one\n', encoding='utf-8')
        (self.project / 'old.py').write_text('old\n', encoding='utf-8')
        git(self.project, 'add', '.')
        git(self.project, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'start')
        self.backend = Saving(self.project)
        self.store = Store('tools', self.project, self.root / 'state')
        self.control = Controller(self.store, self.backend)
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')
        self.label = agent_label(self.store.read())

    def turn(self, text='/d change the app'):
        hook.handle(dict(session_id='tools', cwd=str(self.project), hook_event_name='UserPromptSubmit',
                         prompt=text), self.store.root)
        request = self.store.read()['turnRoute']['requestId']
        self.control.send_request(request, output=lambda event: None)
        return request

    @staticmethod
    def edit(project, name):
        (project / 'app.py').write_text('one\ntwo\n', encoding='utf-8')
        (project / 'new.py').write_text('new\n', encoding='utf-8')
        (project / 'old.py').unlink()


class Undo(Project):
    def own(self, request, *paths):
        """The files the agent's own tools edited in that turn, as its edit events record them."""
        with self.store.edit() as state:
            state['requests'][request]['touched'] = list(paths)

    def test_undo_puts_back_the_turn_and_only_once(self):
        self.backend.work = self.edit
        self.own(self.turn(), 'app.py', 'new.py', 'old.py')
        text = self.control.undo()['text']
        self.assertTrue(text.startswith('Undid ' + self.label + '\'s last turn: restored '))
        self.assertEqual((self.project / 'app.py').read_text(encoding='utf-8'), 'one\n')
        self.assertTrue((self.project / 'old.py').is_file())
        self.assertFalse((self.project / 'new.py').exists())
        self.assertEqual(git(self.project, 'status', '--porcelain'), '')
        self.assertIn('already undone', self.control.undo()['text'])

    def test_nothing_changes_if_a_file_changed_since(self):
        self.backend.work = self.edit
        self.own(self.turn(), 'app.py', 'new.py', 'old.py')
        (self.project / 'app.py').write_text('yours\n', encoding='utf-8')
        result = self.control.undo()
        self.assertEqual(result['conflicts'], ['app.py'])
        self.assertTrue(result['text'].startswith('Nothing was undone: app.py has changed since'))
        self.assertTrue((self.project / 'new.py').is_file())  # All or nothing.

    def test_a_crlf_file_comes_back_with_its_own_line_endings(self):
        # Git for Windows' default: the repository stores LF, the folder holds CRLF.
        git(self.project, 'config', 'core.autocrlf', 'true')
        (self.project / 'notes.txt').write_bytes(b'one\r\ntwo\r\n')
        git(self.project, 'add', 'notes.txt')
        git(self.project, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'notes')
        self.backend.work = lambda project, name: (project / 'notes.txt').write_bytes(b'one\r\nchanged\r\n')
        self.own(self.turn(), 'notes.txt')
        self.assertIn('restored notes.txt', self.control.undo()['text'])
        self.assertEqual((self.project / 'notes.txt').read_bytes(), b'one\r\ntwo\r\n')

    def test_another_agents_edit_in_the_same_turn_is_left_alone(self):
        self.backend.work = self.edit  # It changes app.py, new.py and old.py; its own tools edited only app.py.
        self.own(self.turn(), 'app.py')
        text = self.control.undo()['text']
        self.assertTrue(text.startswith('Undid ' + self.label + '\'s last turn: restored app.py.'))
        self.assertIn('not edited by ' + self.label + '\'s own tools: new.py, old.py', text)
        self.assertEqual((self.project / 'app.py').read_text(encoding='utf-8'), 'one\n')
        self.assertTrue((self.project / 'new.py').is_file())
        self.assertFalse((self.project / 'old.py').exists())

    def test_a_turn_whose_tools_edited_nothing_undoes_nothing(self):
        self.backend.work = self.edit
        self.own(self.turn())
        self.assertTrue(self.control.undo()['text'].startswith('Nothing was undone: ' + self.label + '\'s own tools'))
        self.assertTrue((self.project / 'new.py').is_file())

    def test_nothing_to_undo_says_so(self):
        self.assertIn('has no turn to undo', self.control.undo()['text'])


class TestGate(Project):
    def test_set_shown_and_off(self):
        self.assertIn('No test setup found in this project', self.control.tests()['text'])
        self.control.tests('npm test')
        self.assertIn('Tests for this project: npm test', self.control.tests()['text'])
        self.assertIn('Tests are off for this project.', self.control.tests('off')['text'])
        self.assertIsNone(test_gate.command(self.store.root, self.project))

    def test_the_command_is_found_from_the_project(self):
        write = lambda name, text: (self.project / name).write_text(text, encoding='utf-8')
        self.assertEqual(test_gate.detect(self.project), (None, None))
        write('package.json', json.dumps({'scripts': {'test': 'echo "Error: no test specified" && exit 1'}}))
        self.assertEqual(test_gate.detect(self.project), (None, None))  # npm init's placeholder is not a test.
        (self.project / 'tests').mkdir()
        write('tests/test_app.py', 'def test_ok(): pass\n')
        self.assertEqual(test_gate.detect(self.project), ('python -m pytest', 'tests/'))
        write('package.json', json.dumps({'scripts': {'test': 'vitest run'}}))
        self.assertEqual(test_gate.detect(self.project), ('npm test', 'package.json'))
        self.assertIn('npm test (found from package.json)', self.control.tests()['text'])
        self.assertEqual(test_gate.command(self.store.root, self.project), 'npm test')  # On by default.

    def test_off_stays_off_and_auto_finds_it_again(self):
        (self.project / 'Cargo.toml').write_text('[package]\n', encoding='utf-8')
        self.control.tests('off')
        self.assertIsNone(test_gate.command(self.store.root, self.project))  # Detection doesn't switch it back on.
        self.assertIn('cargo test (found from Cargo.toml)', self.control.tests('auto')['text'])
        self.assertEqual(test_gate.command(self.store.root, self.project), 'cargo test')

    def test_a_command_is_one_line(self):
        reply = self.control.tests('npm test · /cli test · /cli test off\n\nExplain')['text']
        self.assertEqual(reply, 'A test command is one line, such as /cli test npm test. Nothing was changed.')
        self.assertIsNone(test_gate.saved(self.store.root, self.project))

    def test_an_unknown_word_is_named(self):
        from state import route
        self.assertEqual(route('/cli frobnicate', self.store.read())['text'],
                         'CLI-MODE has no /cli frobnicate. Say /help to see options.')

    def test_a_turn_that_changes_files_is_tested_and_the_answer_says_so(self):
        self.control.tests(passing())
        self.backend.work = self.edit
        request = self.turn()
        tests = self.store.read()['requests'][request]['tests']
        self.assertEqual((tests['passed'], tests['summary']), (True, '3 passed in 0.1s'))
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)
        self.assertIn('_✓ Tests passed (' + passing() + ') · 3 passed in 0.1s',
                      self.control.relay_text(request, wait=0)['text'])

    def test_a_failure_puts_its_log_first_in_the_copy_box(self):
        self.control.tests(failing())
        self.backend.work = self.edit
        request = self.turn()
        record = self.store.read()['requests'][request]
        self.assertFalse(record['tests']['passed'])
        self.assertEqual(record['refs']['files'][0], record['tests']['log'])
        self.assertIn('1 failed, 2 passed', (self.project / record['tests']['log']).read_text(encoding='utf-8'))
        lines = []
        self.control.follow(request, lines.append, poll=0)
        self.assertIn('Tests FAILED.', lines[-1])

    def test_a_turn_without_project_edits_is_not_tested(self):
        self.control.tests(failing())
        request = self.turn()
        self.assertNotIn('tests', self.store.read()['requests'][request])

    def test_a_run_past_its_limit_ends_with_everything_it_started(self):
        # The shell's child keeps the output pipes open; ending only the shell would leave the gate waiting.
        script = self.root / 'hang.py'
        script.write_text('import subprocess, sys, time\n'
                          'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
                          'time.sleep(120)\n', encoding='utf-8')
        text = '"' + sys.executable + '" "' + str(script) + '"'
        began = time.monotonic()
        result = test_gate.run(self.root / 'state', self.project, text, None, timeout=3)
        self.assertLess(time.monotonic() - began, 60)
        self.assertFalse(result['passed'])
        self.assertTrue(result['summary'].startswith('stopped after'))

    def test_runner_summaries(self):
        self.assertEqual(test_gate.summary('x\n===== 1 failed, 41 passed in 3.2s =====\n'), '1 failed, 41 passed in 3.2s')
        self.assertEqual(test_gate.summary('Tests:       1 failed, 9 passed, 10 total\n'), '1 failed, 9 passed, 10 total')
        self.assertEqual(test_gate.summary('test result: ok. 4 passed; 0 failed\n'), 'test result: ok. 4 passed; 0 failed')
        self.assertEqual(test_gate.summary('all good\n\n'), 'all good')


class SameFile(Project):
    def record(self, state, request_id, session, start, end, paths):
        events = self.root / (request_id + '.jsonl')
        events.write_text('\n'.join(json.dumps(dict(type='activity', toolCallId='t' + str(index), kind='edit',
                                                     status='completed', locations=[dict(path=path)]))
                                    for index, path in enumerate(paths)), encoding='utf-8')
        state.setdefault('requests', {})[request_id] = dict(session=session, submittedAt=start, events=str(events),
                                             generation=state['generation'], status='completed')
        return request_id

    def test_a_file_both_agents_edited_is_flagged_on_the_later_answer(self):
        with self.store.edit() as state:
            mine = state['owned'][0]['name']
            self.record(state, 'other', 'someone-else', 100, None, ['app.py', 'api.py'])
            self.control._note_touched(state, 'other', self.project)
            state['requests']['other']['endedAt'] = 150
            self.record(state, 'late', mine, 120, None, [str(self.project / 'app.py'), 'readme.md'])
            self.control._note_touched(state, 'late', self.project)
            self.record(state, 'apart', mine, 200, None, ['app.py'])
            self.control._note_touched(state, 'apart', self.project)
            records = state['requests']
        self.assertEqual([item['path'] for item in records['late']['overlaps']], ['app.py'])
        self.assertNotIn('overlaps', records['apart'])  # Not at the same time.
        text = relay_view.final_markdown('Grok ART', [], [], overlaps=records['late']['overlaps'])
        self.assertIn('⚠ Also edited by ', text)
        self.assertIn('while this turn ran: app.py', text)


class Brief(Project):
    def test_add_show_clear(self):
        # Starting the agent wrote the brief: its entry for the host's note and the agents running now.
        self.assertEqual(self.control.brief()['text'].splitlines()[1:],
                         ['No points yet: /cli brief-add <text> adds one.',
                          'Host notes: 1 (latest: ' + self.started() + ').', 'Agents listed: ' + self.label + '.'])
        self.control.brief('add', 'Use TypeScript strict mode.')
        self.assertIn('(2 points', self.control.brief('add', 'Tests live in tests/.')['text'])
        self.assertEqual(self.control.brief()['text'].splitlines()[1:3],
                         ['1. Use TypeScript strict mode.', '2. Tests live in tests/.'])
        self.assertTrue(self.control.brief('clear')['text'].startswith('Your points and the host\'s notes are cleared'))
        self.assertEqual(agent_folder.read_brief(self.project)[:2], ([], []))
        self.assertIn('nothing in the project brief to clear', self.control.brief('clear')['text'])

    def started(self):
        return next(line[4:] for line in agent_folder.read_brief(self.project)[1] if line.startswith('### '))

    def test_every_task_names_it_with_the_agents_running_now(self):
        self.turn()
        self.assertIn('you are ' + self.label + '. First read `Agent_Working_Folder/BRIEF.md`', self.backend.sent[-1])
        team = agent_folder.read_brief(self.project)[2]
        self.assertEqual(len(team), 1)
        self.assertTrue(team[0].startswith('- ' + self.label + ': folder `Agent_Working_Folder/'))
        self.assertIn('last answer `Agent_Working_Folder/', team[0])  # The first turn's saved answer.
        self.assertNotIn('Agent_Working_Folder', git(self.project, 'status', '--porcelain'))

    def test_the_host_note_entry_is_kept_and_a_closed_agent_leaves_the_list(self):
        note = agent_folder.read_brief(self.project)[1]
        self.assertTrue(note[0].startswith('### ') and note[0].endswith(', when ' + self.label + ' started'))
        self.assertEqual(note[1], '(The host has not written this note yet for ' + self.label + '.)')
        path = agent_folder.brief_path(self.project)  # The host writes its note in place of that line.
        path.write_text(path.read_text(encoding='utf-8').replace(note[1], 'Rewriting the parser.'), encoding='utf-8')
        self.control.off()
        points, notes, team = agent_folder.read_brief(self.project)
        self.assertEqual(notes[1], 'Rewriting the parser.')  # Kept: the notes are a history.
        self.assertEqual(team, [])

    def test_codex_applies_your_text_in_the_hook(self):
        reply = hook.handle(dict(session_id='tools', cwd=str(self.project), hook_event_name='UserPromptSubmit',
                                 prompt='/cli brief-add Keep "quotes" & ampersands'), self.store.root)
        self.assertIn('Added to the project brief', json.dumps(reply))
        self.assertEqual(agent_folder.brief_lines(self.project), ['Keep "quotes" & ampersands'])


if __name__ == '__main__':
    unittest.main()
