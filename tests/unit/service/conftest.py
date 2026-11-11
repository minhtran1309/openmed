"""Shared setup for the service unit tests.

The lean service refuses to start without ``OPENMED_AUDIT_KEY`` and signs
every ``/v1/apply`` audit with it. The key is set here, for this directory
only: the CLI reads the same variable, and a global key would hide its
missing-key path.

The key is set from ``pytest_runtest_setup`` and removed after
``pytest_runtest_teardown`` rather than by an autouse fixture. pytest 9 binds
conftest fixtures to the first ``Directory`` node that collects this
directory, and a file passed directly on the command line makes pytest
re-collect its parent directory into fresh ``Directory`` nodes. An interleaved
order such as ``service/a.py test_b.py service/c.py`` then ran ``c.py`` with no
key. Conftest hooks are resolved by file path, so they apply in every order.
See ``test_audit_key_scoping.py``.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

SYNTHETIC_AUDIT_KEY = "synthetic-audit-key-not-a-secret-0001"
AUDIT_KEY_ENV = "OPENMED_AUDIT_KEY"

_AUDIT_KEY_PATCH = pytest.StashKey[pytest.MonkeyPatch]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    """Set the synthetic audit key before any fixture of a service test runs."""
    patch = pytest.MonkeyPatch()
    patch.setenv(AUDIT_KEY_ENV, SYNTHETIC_AUDIT_KEY)
    item.stash[_AUDIT_KEY_PATCH] = patch
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, None, None]:
    """Restore the environment after every fixture of a service test is torn down."""
    try:
        return (yield)
    finally:
        patch = item.stash.get(_AUDIT_KEY_PATCH, None)
        if patch is not None:
            patch.undo()
            del item.stash[_AUDIT_KEY_PATCH]
