"""Menu option cap and paging. A long provider catalog must stay readable."""
import tempfile
from pathlib import Path
import unittest

from test_controller import PLUGIN  # noqa: F401  (puts the plugin scripts on sys.path)
import adapters
import frontends
from presentation import MENU_MAX_OPTIONS, menu_block, options_menu, paginate
from state import backend_ids

NAV = ('> ', '< ')


def selectable(block):
    """Rows a user can actually choose, as rendered inside the frame."""
    rows = [line.strip('| ').rstrip() for line in block.splitlines() if line.startswith('| ')]
    return [r for r in rows if r and (r[0].isdigit() or r[:2] in ('R.', 'B.', 'F.', 'X.', 'I.', 'M.') or r[:2] in NAV)]


class Cap(unittest.TestCase):
    def test_short_lists_are_not_paginated(self):
        built = paginate(['a', 'b', 'c'], reserved=1)
        self.assertEqual(built['pages'], 1)
        self.assertEqual(built['rows'], [])
        self.assertEqual(built['items'], ['a', 'b', 'c'])

    def test_a_list_that_exactly_fits_is_not_paginated(self):
        built = paginate(list(range(MENU_MAX_OPTIONS - 1)), reserved=1)
        self.assertEqual(built['pages'], 1)

    def test_one_more_than_fits_paginates(self):
        built = paginate(list(range(MENU_MAX_OPTIONS)), reserved=1)
        self.assertGreater(built['pages'], 1)

    def test_no_page_ever_exceeds_the_cap(self):
        for total in range(1, 60):
            for reserved in range(0, 5):
                page = 1
                seen = []
                while True:
                    built = paginate(list(range(total)), reserved=reserved, page=page)
                    rows = len(built['items']) + len(built['rows']) + reserved
                    self.assertLessEqual(rows, MENU_MAX_OPTIONS,
                                         'total=%d reserved=%d page=%d' % (total, reserved, page))
                    seen.extend(built['items'])
                    if page >= built['pages']:
                        break
                    page += 1
                # Paging shows every item exactly once, in order.
                self.assertEqual(seen, list(range(total)), 'total=%d reserved=%d' % (total, reserved))

    def test_numbering_is_continuous_across_pages(self):
        labels = ['m%d' % i for i in range(25)]
        numbers = []
        page, pages = 1, None
        while True:
            built = options_menu('Select Model', labels, tail=['B. Back'], page=page)
            rows = built['text'].splitlines()
            numbers += [int(r.split('.')[0]) for r in rows if r[:1].isdigit()]
            pages = built['pages']
            if page >= pages:
                break
            page += 1
        self.assertEqual(numbers, list(range(1, 26)))

    def test_paging_rows_are_worded_as_specified(self):
        built = options_menu('Select Model', ['m%d' % i for i in range(30)], tail=['B. Back'], page=2)
        self.assertIn('> Next page (3 of ', built['text'])
        self.assertIn('< Previous page (1 of ', built['text'])

    def test_first_page_has_no_previous_and_last_has_no_next(self):
        labels = ['m%d' % i for i in range(30)]
        first = options_menu('Select Model', labels, tail=['B. Back'], page=1)
        self.assertIn('> Next page', first['text'])
        self.assertNotIn('< Previous page', first['text'])
        last = options_menu('Select Model', labels, tail=['B. Back'], page=first['pages'])
        self.assertNotIn('> Next page', last['text'])
        self.assertIn('< Previous page', last['text'])

    def test_out_of_range_pages_clamp_rather_than_fail(self):
        labels = ['m%d' % i for i in range(30)]
        self.assertEqual(options_menu('Select Model', labels, page=0)['page'], 1)
        self.assertEqual(options_menu('Select Model', labels, page=999)['page'],
                         options_menu('Select Model', labels, page=1)['pages'])

    def test_reserved_rows_that_leave_no_room_are_refused(self):
        with self.assertRaises(ValueError):
            paginate(['a'] * 20, reserved=MENU_MAX_OPTIONS)
        with self.assertRaises(ValueError):
            paginate(['a'] * 20, reserved=MENU_MAX_OPTIONS - 1)


class RenderedMenus(unittest.TestCase):
    """The cap holds on the real menus, counting the X. Exit row."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for backend in backend_ids():
            frontends.check_and_save(self.root, backend,
                                     check=lambda agent: dict(confirmed=True, checks=[]))

    def test_home_menu_respects_the_cap(self):
        ready = {'ready': True}
        block = frontends.menu(self.root, 'home', None, ready, ready)
        self.assertLessEqual(len(selectable(block)), MENU_MAX_OPTIONS)

    def test_every_phase_menu_of_every_agent_respects_the_cap(self):
        for backend in backend_ids():
            for phase in ('model', 'effort', 'access'):
                page = 1
                while True:
                    built = frontends.phase_menu(self.root, backend, phase, None, page)
                    rows = selectable(built['menu'])
                    self.assertLessEqual(len(rows), MENU_MAX_OPTIONS,
                                         '%s %s page %d: %s' % (backend, phase, page, rows))
                    self.assertTrue(built['menu'].rstrip().endswith('```'))
                    if page >= built['pages']:
                        break
                    page += 1

    def test_phase_menus_offer_only_advertised_choices(self):
        for backend in backend_ids():
            adapter = adapters.module(backend)
            data = adapter.catalog(self.root)
            models = frontends.phase_options(self.root, backend, 'model')
            label = getattr(adapter, 'model_label', str)
            self.assertEqual([m[0] for m in models],
                             [label(f['name']) for f in data['modelFamilies']])
            access = [a[1] for a in frontends.phase_options(self.root, backend, 'access')]
            # Same set as the catalog, but always in the documented order.
            self.assertEqual(set(access), {o['access'] for o in data['accessControl']['options']})
            self.assertEqual(access, [a for a in frontends.ACCESS_ORDER if a in access], backend)

    def test_a_model_without_effort_shows_one_explicit_provider_default(self):
        # Never invent effort levels for a model that advertises none.
        catalog = {'modelFamilies': [{'name': 'Solo', 'modelId': 'solo', 'efforts': []}],
                   'accessControl': {'options': [{'access': 'allow', 'nativeName': 'Allow'}]}}

        class Fake:
            DEFAULTS = {'model': 'solo', 'access': 'allow'}

            @staticmethod
            def catalog(_root):
                return catalog

        import unittest.mock as mock
        with mock.patch.object(adapters, 'module', return_value=Fake):
            options = frontends.phase_options(self.root, 'agy', 'effort')
        self.assertEqual(options, [('Provider default', None)])

    def test_marks_the_current_choice_on_the_page_it_appears(self):
        settings = adapters.module('claude').selection(self.root, 'haiku', 'allow')
        built = frontends.phase_menu(self.root, 'claude', 'model', settings, 1)
        pages = [frontends.phase_menu(self.root, 'claude', 'model', settings, p)['text']
                 for p in range(1, built['pages'] + 1)]
        self.assertTrue(any('Haiku 4.5  (current)' in text for text in pages))


if __name__ == '__main__':
    unittest.main()
