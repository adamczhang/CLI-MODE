"""The live harness's own checks, on recorded-shape event logs (no Claude Code, no quota)."""
import unittest

import test_controller  # noqa: F401 (puts the plugin's scripts on the path, as for every check)
from claude_background_live import folded_answers
from presentation import plain_strong, strong


def says(label, words):
    return strong(label + ' says...', True) + '\n\n' + words


def turn(*texts):
    """A turn as Claude Code's stream-json reports it: assistant text blocks, then its result."""
    return [dict(type='assistant', message=dict(content=[dict(type='text', text=text)])) for text in texts] + [
        dict(type='result', result=texts[-1] if texts else '')]


class FoldedAnswers(unittest.TestCase):
    def test_answers_in_the_last_message_of_their_turn_pass(self):
        # Live, 2026-09-25 (after the fix): Codex finished mid-turn and the last message carried both answers.
        events = turn(says('Grok GRO-HF', 'PERIWINKLE') + '\n\n' + says('Codex COD-KM', 'PERIWINKLE'))
        events += turn(says('Codex COD-KM', 'done'))
        self.assertEqual(folded_answers(events, plain_strong), [])

    def test_an_answer_posted_before_the_last_message_fails(self):
        # Live, 2026-09-25: a run "passed" with one post per relay; the desktop app folds the first out of view.
        events = turn(says('Codex COD-B7', 'PERIWINKLE'), says('Grok GRO-8Y', 'PERIWINKLE'))
        self.assertEqual(folded_answers(events, plain_strong),
                         ['folded: Codex COD-B7 says... posted before the last message of its turn'])

    def test_other_text_before_the_last_message_is_fine(self):
        events = turn('Checking the queue.', says('Grok GRO-HF', 'ok'))
        self.assertEqual(folded_answers(events, plain_strong), [])


if __name__ == '__main__':
    unittest.main()
