"""Every /cli control, for every registered agent, under every accepted prefix."""
import tempfile
from pathlib import Path
import unittest

from test_controller import PLUGIN
import adapters
import frontends
from state import PREFIXES, route, backend_ids

OFF = {'active': False}
ON = {'active': True, 'routingMode': 'direct'}


def control_words(backend_id):
    """Canonical id plus any registered alias."""
    record = next(item for item in frontends.backends() if item['id'] == backend_id)
    return [record['id']] + list(record.get('aliases') or [])


class Prefixes(unittest.TestCase):
    def test_accepted_prefixes_are_slash_and_dollar(self):
        self.assertEqual(PREFIXES, ('/', '$'))

    def test_every_prefix_opens_help(self):
        for prefix in PREFIXES:
            self.assertEqual(route(prefix + 'help', OFF)['route'], 'help')
            self.assertEqual(route(prefix + 'HELP', OFF)['route'], 'help')

    def test_no_other_prefix_is_a_control(self):
        for text in ('?cli', '?help', '!cli', 'cli', 'help', '#cli'):
            self.assertEqual(route(text, OFF)['route'], 'host', text)

    def test_a_partial_token_is_ordinary_text(self):
        for text in ('/client', '$client', '/clip art', '/helper', '$helpme'):
            self.assertEqual(route(text, OFF)['route'], 'host', text)

    def test_help_takes_no_arguments(self):
        for prefix in PREFIXES:
            self.assertNotEqual(route(prefix + 'help me', OFF)['route'], 'help')


class AgentControls(unittest.TestCase):
    """Each agent must answer the same controls, not a subset."""

    def test_menu_control_for_every_agent_and_prefix(self):
        for backend in backend_ids():
            for word in control_words(backend):
                for prefix in PREFIXES:
                    for text in (prefix + 'cli ' + word,
                                 (prefix + 'cli ' + word).upper(),
                                 '  ' + prefix + 'cli  ' + word):
                        self.assertEqual(route(text, OFF),
                                         {'route': 'frontend', 'agent': backend}, text)

    def test_bind_control_for_every_agent_and_prefix(self):
        for backend in backend_ids():
            for word in control_words(backend):
                for prefix in PREFIXES:
                    for text in (prefix + 'cli bind ' + word,
                                 (prefix + 'cli bind ' + word).upper()):
                        self.assertEqual(route(text, OFF),
                                         {'route': 'bind', 'agent': backend}, text)

    def test_menu_and_bind_take_no_task_text(self):
        for backend in backend_ids():
            for word in control_words(backend):
                self.assertEqual(route('/cli %s do the thing' % word, OFF)['route'], 'hint')
                # Bind takes one optional word, the new agent's name; anything more, or a bad name, is refused.
                self.assertEqual(route('/cli bind %s do it now' % word, OFF)['route'], 'hint')
                self.assertEqual(route('/cli bind %s it!' % word, OFF)['route'], 'hint')
                self.assertEqual(route('/cli bind %s now' % word, OFF), dict(route='bind', agent=backend, name='NOW'))

    def test_near_miss_agent_names_are_never_resolved(self):
        for text in ('/cli gr', '/cli grk', '/cli grokbuild', '/cli claud', '/cli clau', '/cli agyx', '/cli co',
                     '/cli bind', '/cli bind nope'):
            self.assertEqual(route(text, OFF)['route'], 'hint', text)


class SharedControls(unittest.TestCase):
    def test_shutdown_under_every_prefix(self):
        for verb in ('stop', 'off'):
            for prefix in PREFIXES:
                self.assertEqual(route(prefix + 'cli ' + verb, ON), {'route': 'off'})

    def test_tuning_phases_under_every_prefix(self):
        for verb, phase in (('model', 'model'), ('effort', 'effort'),
                            ('access', 'access'), ('permissions', 'access')):
            for prefix in PREFIXES:
                self.assertEqual(route(prefix + 'cli ' + verb, ON),
                                 ({'route': 'settings'} if verb == 'model' else {'route': 'tune', 'phase': phase, 'text': ''}))
                self.assertEqual(route(prefix + 'cli ' + verb + ' some choice', ON),
                                 {'route': 'tune', 'phase': phase, 'text': 'some choice'})

    def test_tuning_without_a_bound_agent_hints(self):
        for verb in ('model', 'effort', 'access', 'permissions'):
            self.assertEqual(route('/cli ' + verb, OFF)['route'], 'hint')

    def test_ordinary_text_is_never_captured(self):
        # Only an explicit /d or $d reaches the agent (Passthrough was removed).
        for state in (ON, OFF):
            self.assertEqual(route('fix the bug', state)['route'], 'host')
            self.assertEqual(route('"/cli stop" is the control', state)['route'], 'host')
        self.assertEqual(route('/d fix the bug', ON)['route'], 'direct')
        self.assertEqual(route('$d fix the bug', ON)['route'], 'direct')


class AgentCapabilities(unittest.TestCase):
    """Tuning is backend-agnostic, so every agent must back all three phases."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_every_agent_exposes_model_effort_and_access(self):
        for backend in backend_ids():
            adapter = adapters.module(backend)
            data = adapter.catalog(self.root)
            self.assertTrue(data.get('modelFamilies'), backend)
            self.assertTrue(data['accessControl']['options'], backend)
            # An effort selector is optional; a backend that advertises none
            # must still offer one explicit Provider default row.
            self.assertTrue(frontends.phase_options(self.root, backend, 'effort'), backend)

    def test_every_advertised_access_level_resolves(self):
        for backend in backend_ids():
            adapter = adapters.module(backend)
            data = adapter.catalog(self.root)
            defaults = adapter.DEFAULTS
            for option in data['accessControl']['options']:
                chosen = adapter.selection(self.root, defaults['model'],
                                           option['access'], defaults.get('effort'))
                self.assertEqual(chosen['access'], option['access'])

    def test_access_is_presented_in_one_order_for_every_agent(self):
        """A runtime may advertise its modes in any order; the menu must not."""
        for backend in backend_ids():
            shown = [value for _, value in frontends.phase_options(self.root, backend, 'access')]
            self.assertEqual(shown, [a for a in frontends.ACCESS_ORDER if a in shown], backend)
            self.assertEqual(shown[0], 'allow', backend)

    def test_an_unoffered_access_level_is_explained_not_silently_missing(self):
        for backend in backend_ids():
            data = adapters.module(backend).catalog(self.root)
            shown = {value for _, value in frontends.phase_options(self.root, backend, 'access')}
            explained = {i['access'] for i in data['accessControl'].get('unsupported', [])}
            for alias in set(frontends.ACCESS_ORDER) - shown:
                self.assertIn(alias, explained, '%s: %s unoffered and unexplained' % (backend, alias))

    def test_every_agent_declares_a_menu_and_a_passing_announcement(self):
        for backend in backend_ids():
            adapter = adapters.module(backend)
            self.assertTrue(frontends.settings_text(backend).startswith('CLI-MODE\nAgent Settings'), backend)
            self.assertEqual(adapter.PASSING, 'Passing to ' + adapter.LABEL + '...', backend)
            self.assertTrue(adapter.DISPLAY_NAME.endswith('CLI'), backend)

    def test_the_agent_is_named_the_same_way_everywhere(self):
        """One single word, used by both the passing line and the attribution."""
        expected = {'agy': 'Antigravity', 'claude': 'Claude', 'grok-build': 'Grok',
                    'cursor': 'Cursor', 'copilot': 'Copilot', 'codex': 'Codex'}
        self.assertEqual(set(expected), set(backend_ids()))
        for backend, word in expected.items():
            adapter = adapters.module(backend)
            self.assertEqual(adapter.LABEL, word, backend)
            self.assertEqual(adapter.PASSING, 'Passing to ' + word + '...', backend)
            self.assertNotIn(' ', adapter.LABEL, backend)


class FailureReporting(unittest.TestCase):
    """A failure must name the agent that failed, not a hardcoded provider."""

    def test_dispatch_failure_names_the_bound_agent(self):
        import io, json, tempfile, contextlib
        from test_controller import FakeBackend
        from controller import Controller
        from state import Store
        for backend in backend_ids():
            root, workspace = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
            store = Store('fail-' + backend, str(workspace), root)
            control = Controller(store, FakeBackend(), agent=backend)
            control.backend.stop = 'refusal'  # not end_turn: the gate must fail
            owned = dict(name='s', role='main', ready=True, backend=backend,
                         workspace=str(workspace), settings={'access': 'allow'})
            with store.edit() as state:
                state.update(active=True, backend=backend, main='s', owned=[owned])
            with self.assertRaises(RuntimeError) as caught:
                with contextlib.redirect_stdout(io.StringIO()):
                    control.prompt(owned, 'hi', state['generation'], output=lambda e: None)
            message = str(caught.exception)
            self.assertIn(adapters.module(backend).LABEL, message, backend)
            for other in backend_ids():
                label = adapters.module(other).LABEL
                if label != adapters.module(backend).LABEL:
                    self.assertNotIn(label, message, '%s leaked %s' % (backend, label))


class RegistryCost(unittest.TestCase):
    """The registry is parsed once per process, not once per lookup."""

    def test_backend_records_are_cached(self):
        import state
        self.assertEqual(frontends.backends(), state.backend_records())
        self.assertIsNot(frontends.backends(), frontends.backends())

    def test_rendering_a_menu_does_not_reread_the_registry(self):
        import tempfile
        from pathlib import Path as P
        root = P(tempfile.mkdtemp())
        for backend in backend_ids():
            frontends.check_and_save(root, backend,
                                     check=lambda agent: dict(confirmed=True, checks=[]))
        frontends.backends()  # warm the cache as any real process would
        seen = []
        original = P.read_text

        def counting(self, *args, **kwargs):
            seen.append(self.name)
            return original(self, *args, **kwargs)

        P.read_text = counting
        try:
            frontends.menu(root, 'home', None, {'ready': True}, {'ready': True})
        finally:
            P.read_text = original
        self.assertNotIn('backends.json', seen, seen)
        # Only the per-backend setup receipts, which are mutable state.
        self.assertTrue(all(name.endswith('.json') for name in seen), seen)


class HelpText(unittest.TestCase):
    def setUp(self):
        import help_view
        self.help = help_view

    def test_help_lists_every_agent_and_its_controls(self):
        commands = self.help.render()
        for backend in backend_ids():
            record = next(i for i in frontends.backends() if i['id'] == backend)
            self.assertIn(record['tag'] + ' (', self.help.text())  # Help names each agent by its tag.
        self.assertIn('/cli <agent>', commands)
        self.assertIn('/cli bind|spawn <agent> [name]', commands)

    def test_help_lists_every_shared_control(self):
        commands = self.help.render()
        for control in ('/cli', '/cli <agent>', '/cli bind|spawn <agent> [name]', '/d [names] <PROMPT>', '/cli diff [name]', '/cli timeout [name] <time>', '/cli attach [name]',
                        '/cli list|agents', '/cli use <name>', '/cli menu|settings [name]', '/cli progress <mode>',
                        '/cli queue', '/cli resume', '/cli cancel [name]', '/cli close|stop [name|all]', '/cli off',
                        '/help'):
            self.assertIn(control, commands)

    def test_help_is_one_commands_table(self):
        page = self.help.render()
        # One framed card, like every other menu.
        self.assertTrue(page.startswith('```text\n+---'))
        self.assertIn('| Help ', page)
        self.assertNotIn('Choose a section', page)

    def test_help_documents_both_prefixes_and_no_others(self):
        commands = self.help.render()
        self.assertIn('$ works in place of /', commands)
        self.assertNotIn('?cli', commands)

    def test_help_has_one_exit_reply(self):
        self.assertEqual(self.help.render().count('X. Close help'), 1)


if __name__ == '__main__':
    unittest.main()
