"""Agent names: generation, validation, targeting, and how /cli and /d route with several agents."""
import unittest

from test_controller import PLUGIN  # noqa: F401  (sets the offline test environment and import path)
import host
import names
from state import route, target_of

ID = set(names.ID_CHARS)


def state_with(*agents, main=None):
    """A conversation with these (alias, backend) agents running; the first is current unless `main`."""
    owned = [dict(name='s' + str(index), alias=alias, backend=backend, ready=True, role='main',
                  settings={'model': 'm', 'modelName': 'M', 'effort': 'high', 'access': 'prompt'})
             for index, (alias, backend) in enumerate(agents)]
    return dict(active=bool(owned), main=main or (owned[0]['name'] if owned else None), owned=owned, pending=None,
                routingMode='direct', generation=0, requests={}, inflight={})


class Generation(unittest.TestCase):
    def test_shape_code_and_alphabet(self):
        for backend, code in names.CODES.items():
            name = names.generate(backend, 'session-' + backend)
            self.assertRegex(name, '^' + code + '-[' + names.ID_CHARS + ']{2}$')
        self.assertEqual(len(names.ID_CHARS), 33)
        self.assertFalse({'0', 'I', 'O'} & ID)

    def test_derived_from_the_session_and_never_reused(self):
        first = names.generate('codex', 'cli-mode-abc')
        self.assertEqual(first, names.generate('codex', 'cli-mode-abc'))
        second = names.generate('codex', 'cli-mode-abc', used=[first])
        self.assertNotEqual(first, second)
        used = [first.lower()]  # Any case counts as used.
        self.assertNotEqual(names.generate('codex', 'cli-mode-abc', used), first)

    def test_many_names_in_one_conversation_stay_unique(self):
        used = []
        for index in range(200):
            used.append(names.generate('grok-build', 'session-' + str(index), used))
        self.assertEqual(len(set(used)), 200)


class Custom(unittest.TestCase):
    def test_letters_and_digits_up_to_ten(self):
        for good in ('ELONMUSK', 'bob', 'a1', 'X9Y8Z7W6V5'):
            self.assertIsNone(names.custom_error(good), good)
        for bad in ('', 'has space', 'dash-ed', '-7K', 'under_score', 'toolongname1', 'émile', 'a.b'):
            self.assertIn('letters or digits', names.custom_error(bad), bad)

    def test_command_words_agent_words_and_generated_shapes_are_refused(self):
        for word in ('all', 'ALL', 'close', 'spawn', 'ultra', 'use', 'grok', 'codex', 'claude', 'agy'):
            self.assertIn('command or agent word', names.custom_error(word), word)
        self.assertIn('COD-7K', names.custom_error('cod7k'))
        self.assertIsNone(names.custom_error('cod70'))  # 0 is never in an id, so this is a plain name.

    def test_a_running_name_is_refused_in_any_case(self):
        self.assertIn('already running', names.custom_error('elon', ['ELON']))


class Resolve(unittest.TestCase):
    agents = {'COD-7K': 's1', 'GRO-7K': 's2', 'GRO-4M': 's3', 'ELON': 's4'}

    def test_full_name_any_case_and_without_the_dash(self):
        for word in ('COD-7K', 'cod-7k', 'Cod-7K', 'cod7k', 'COD7K'):
            self.assertEqual(names.resolve(word, self.agents), ('match', 's1'), word)
        self.assertEqual(names.resolve('elon', self.agents), ('match', 's4'))

    def test_short_form_needs_exactly_one_agent(self):
        self.assertEqual(names.resolve('-4m', self.agents), ('match', 's3'))
        self.assertEqual(names.resolve('-7K', self.agents), ('ambiguous', ['COD-7K', 'GRO-7K']))
        self.assertIsNone(names.resolve('-ZZ', self.agents))

    def test_never_the_bare_id(self):
        for word in ('7k', '4M', 'M', 'COD', 'GRO-', '-', ''):
            self.assertIsNone(names.resolve(word, self.agents), word)


class Routing(unittest.TestCase):
    def setUp(self):
        host.select(host.CLAUDE)
        self.addCleanup(host.select, host.CODEX)
        self.state = state_with(('COD-7K', 'codex'), ('ELON', 'grok-build'))

    def route(self, text):
        return route(text, self.state)

    def test_named_routes(self):
        cases = {
            '/d ELON fix it': ('direct', 's1'),
            '/d cod7k fix it': ('direct', 's0'),
            '/d -7k fix it': ('direct', 's0'),
            '$d elon go': ('direct', 's1'),
            '/cli-mode:d elon go': ('direct', 's1'),
        }
        for text, (kind, session) in cases.items():
            found = self.route(text)
            self.assertEqual((found['route'], found.get('session')), (kind, session), text)
            self.assertTrue(found['named'], text)

    def test_unnamed_and_mid_sentence_words_go_to_the_current_agent(self):
        for text in ('/d fix it', '/d please ask elon', '/d 7k is a size', '/d cod7x is a word'):
            self.assertEqual(self.route(text), {'route': 'direct'}, text)

    def test_generated_shapes_without_an_agent_send_nothing(self):
        for text in ('/d COD-22 hi', '/d -22 hi'):
            found = self.route(text)
            self.assertEqual(found['route'], 'hint', text)
            self.assertIn('Nothing was sent', found['text'])
        self.assertEqual(self.route('/d elon')['route'], 'hint')  # A name and no task.

    def test_ambiguous_short_form_sends_nothing(self):
        self.state = state_with(('COD-7K', 'codex'), ('GRO-7K', 'grok-build'))
        found = self.route('/d -7k hi')
        self.assertEqual(found['route'], 'hint')
        self.assertIn('COD-7K or GRO-7K', found['text'])

    def test_each_alias_pair_routes_identically(self):
        pairs = [('close', 'stop'), ('close', 'off'), ('help', 'commands'), ('menu', 'settings'),
                 ('bind', 'spawn'), ('list', 'agents')]
        tails = ['', ' elon', ' ELON', ' all', ' grok', ' grok Bob', ' max 3', ' cod7k']
        for first, second in pairs:
            for tail in tails:
                for prefix in ('/cli ', '$cli ', '/CLI ', '/cli-mode:cli '):
                    one = self.route(prefix + first + tail)
                    other = self.route(prefix + second.upper() + tail)
                    self.assertEqual(one, other, prefix + first + tail + ' vs ' + second)

    def test_close(self):
        self.assertEqual(self.route('/cli close'), {'route': 'close-menu'})
        self.assertEqual(self.route('/cli close all'), {'route': 'off'})
        self.assertEqual(self.route('/cli close elon'), dict(route='close', session='s1', name='ELON'))
        self.assertEqual(self.route('/cli close nobody')['route'], 'hint')
        self.state = state_with(('COD-7K', 'codex'))
        self.assertEqual(self.route('/cli close'), {'route': 'off'})  # The only agent.
        self.assertEqual(self.route('/cli close cod-7k'), {'route': 'off'})
        self.state = state_with()
        self.assertEqual(self.route('/cli stop'), {'route': 'off'})

    def test_close_chooser_replies(self):
        self.state['closeMenu'] = ['s0', 's1']
        self.assertEqual(self.route('2'), dict(route='close', session='s1', name='ELON'))
        self.assertEqual(self.route('elon'), dict(route='close', session='s1', name='ELON'))
        self.assertEqual(self.route('A'), {'route': 'off'})
        self.assertEqual(self.route('x'), {'route': 'hint', 'text': 'Nothing was closed.'})
        self.assertEqual(self.route('3')['route'], 'host')  # Not a choice: the message is Claude's.
        self.assertEqual(self.route('/d elon go')['route'], 'direct')

    def test_spawn_names_and_limit(self):
        self.assertEqual(self.route('/cli spawn grok'), {'route': 'bind', 'agent': 'grok-build'})
        self.assertEqual(self.route('/cli bind grok bob'), dict(route='bind', agent='grok-build', name='BOB'))
        self.assertIn('already running', self.route('/cli spawn codex elon')['text'])
        self.assertIn('generated name', self.route('/cli spawn codex gro4m')['text'])
        self.state['agentLimit'] = 2
        self.assertIn('the limit', self.route('/cli spawn codex')['text'])

    def test_targeted_controls(self):
        self.assertEqual(self.route('/cli use elon'), dict(route='use', session='s1', name='ELON'))
        self.assertEqual(self.route('/cli cancel elon'), dict(route='cancel', session='s1', name='ELON'))
        self.assertEqual(self.route('/cli cancel'), {'route': 'cancel'})
        self.assertEqual(self.route('/cli settings elon'), dict(route='settings', session='s1', name='ELON'))
        self.assertEqual(self.route('/cli model elon opus'),
                         dict(route='tune', phase='model', text='opus', session='s1', name='ELON'))
        self.assertEqual(self.route('/cli model opus'), dict(route='tune', phase='model', text='opus'))
        self.assertEqual(self.route('/cli permissions elon allow')['phase'], 'access')
        self.assertEqual(self.route('/cli agents max 3'), {'route': 'agents', 'max': 3})
        self.assertEqual(self.route('/cli list max 9')['route'], 'hint')

    def test_targets_ignore_agents_that_are_not_ready_except_for_close(self):
        self.state['owned'][1]['ready'] = False
        self.assertEqual(target_of('elon', self.state), (None, None))
        self.assertEqual(target_of('elon', self.state, every=True), ('s1', 'ELON'))
        self.assertEqual(self.route('/cli close elon')['route'], 'close')


if __name__ == '__main__':
    unittest.main()
