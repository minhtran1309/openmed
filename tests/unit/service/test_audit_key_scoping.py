"""The service audit key must reach every service test in any collection order.

pytest 9 binds conftest fixtures to the ``Directory`` node object that first
collects the conftest's directory. A file passed directly on the command line
makes pytest re-collect its parent directory without deduplication, which
builds fresh child ``Directory`` nodes. So ``service/a.py unit/b.py service/c.py``
runs ``c.py`` under a second ``Dir service`` node that has no conftest fixtures.
These tests run pytest in a subprocess with such an interleaved order.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.unit.service.conftest import SYNTHETIC_AUDIT_KEY

REPO_ROOT = Path(__file__).resolve().parents[3]
THIS_FILE = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

_NO_KEY_PROBE = """
import os


def test_audit_key_is_not_global():
    assert "OPENMED_AUDIT_KEY" not in os.environ
"""


def test_audit_key_is_set_for_service_tests() -> None:
    assert os.environ.get("OPENMED_AUDIT_KEY") == SYNTHETIC_AUDIT_KEY


def test_interleaved_collection_order_keeps_the_service_audit_key(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "test_audit_key_outside_service_probe.py"
    probe.write_text(_NO_KEY_PROBE, encoding="utf-8")
    env = {
        name: value
        for name, value in os.environ.items()
        if name != "OPENMED_AUDIT_KEY" and not name.startswith("PYTEST_")
    }
    args = [
        f"{THIS_FILE}::test_audit_key_is_set_for_service_tests",
        "tests/unit/test_analyze_tagged_text.py",
        str(probe),
        "tests/unit/service/test_lean_apply_audit.py"
        "::test_service_refuses_to_start_without_audit_key",
        "tests/test_medical_remap.py",
        "tests/unit/service/test_lean_apply_audit.py"
        "::test_service_starts_with_audit_key",
    ]

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "AUDIT_KEY_MISSING" not in result.stdout
