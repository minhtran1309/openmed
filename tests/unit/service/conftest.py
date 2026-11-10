"""Shared fixtures for the service unit tests.

The lean service refuses to start without ``OPENMED_AUDIT_KEY`` and signs
every ``/v1/apply`` audit with it. The key is set here, for this directory
only: the CLI reads the same variable, and a global key would hide its
missing-key path.
"""

from __future__ import annotations

import pytest

SYNTHETIC_AUDIT_KEY = "synthetic-audit-key-not-a-secret-0001"


@pytest.fixture(autouse=True)
def _audit_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("OPENMED_AUDIT_KEY", SYNTHETIC_AUDIT_KEY)
    return SYNTHETIC_AUDIT_KEY
