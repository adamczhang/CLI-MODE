"""Codex output must match the golden record taken before the Claude Code port.

The Claude Code port shares the router, controller and menus with Codex. This
test pins every hook response and controller result Codex receives, so shared
changes cannot alter Codex. Re-record only for an intentional Codex change:
python checks/codex_golden.py --write
"""
import json
import unittest

from codex_golden import FIXTURE, record


class CodexGolden(unittest.TestCase):
    maxDiff = None

    def test_every_codex_step_matches_the_golden_record(self):
        expected = json.loads(FIXTURE.read_text(encoding='utf-8'))
        # The same serialization the fixture was written with.
        actual = json.loads(json.dumps(record(), ensure_ascii=True, sort_keys=True))
        self.assertEqual(sorted(actual), sorted(expected), 'Scenario set changed')
        for scenario, steps in expected.items():
            with self.subTest(scenario=scenario):
                self.assertEqual([step['step'] for step in actual[scenario]], [step['step'] for step in steps])
                for index, (got, want) in enumerate(zip(actual[scenario], steps)):
                    self.assertEqual(got, want, scenario + ' step ' + str(index) + ': ' + want['step'])


if __name__ == '__main__':
    unittest.main()
