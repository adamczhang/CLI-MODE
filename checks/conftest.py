"""Shared test-run setup for every check.

Parallel runs (pytest-xdist, `-n auto --dist loadgroup`): tests that share one resource outside their own temporary
folders run on a single worker, one after another: the package builds, which write dist/. The real-ACPX tests need
no group: each gives ACPX its own home (HOME/USERPROFILE, so its own ~/.acpx sessions and config), each environment
gets its own bridge (acpx.bridge_request keys them by it), and ACPX names a queue owner's pipe after its session ID.
Without xdist the grouping marker does nothing.

Fast runs (`-m "not slow"`): the slow tests are the real-ACPX runtime (5-19 s each) and the console-window
rendering corpus (about 25 s each), half of all test time. The full suite runs them.
"""
import pytest

SERIAL = {'test_claude_package': 'package', 'test_package_reproducibility': 'package'}
SLOW_MODULES = {'test_acpx_runtime'}
SLOW_CLASSES = {('test_formatting_corpus', 'Window')}


def pytest_configure(config):
    config.addinivalue_line('markers', 'slow: a real-ACPX or console-window test; `-m "not slow"` leaves it out')


@pytest.hookimpl(tryfirst=True)  # Before xdist's own hook, which reads the marker to group the tests.
def pytest_collection_modifyitems(items):
    for item in items:
        module = item.module.__name__.rpartition('.')[2]
        group = SERIAL.get(module)
        if group:
            item.add_marker(pytest.mark.xdist_group(group))
        if module in SLOW_MODULES or (module, getattr(item.cls, '__name__', None)) in SLOW_CLASSES:
            item.add_marker(pytest.mark.slow)
