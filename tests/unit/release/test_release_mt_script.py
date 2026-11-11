"""Behaviour of ``scripts/release_mt.sh`` around the built wheel.

The script runs against a temporary copy of the repo layout with ``git``,
``python3`` and ``docker`` replaced by stubs on ``PATH``. The ``python3`` stub
fakes ``-m build --wheel`` by writing a wheel with chosen entries and passes
every other call to the real interpreter, so the script's own wheel listing
(``python3 -m zipfile -l``) runs for real. Nothing is built, tagged or pushed.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "release_mt.sh"
TAG = "v3.2.0-mt.99"
SNAPSHOT_ENTRY = "openmed/data/oncotree/oncotree_2025_10_03.json"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="release_mt.sh needs bash"
)

_GIT_STUB = """#!/bin/sh
case "$1" in
  tag) echo "$FAKE_TAG" ;;
  rev-parse) echo 0123456789abcdef0123456789abcdef01234567 ;;
esac
exit 0
"""

_DOCKER_STUB = """#!/bin/sh
echo "docker $*" >> "$DOCKER_LOG"
exit 0
"""

_PYTHON_STUB = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "build" ]; then
  exec "{python}" -c '
import os, zipfile
os.makedirs("dist", exist_ok=True)
entries = [e for e in os.environ["FAKE_WHEEL_ENTRIES"].split(",") if e]
with zipfile.ZipFile("dist/openmed-3.2.0-py3-none-any.whl", "w") as wheel:
    for entry in entries:
        wheel.writestr(entry, "" if entry.endswith("/") else "{{}}")
'
fi
exec "{python}" "$@"
"""


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_release(tmp_path: Path, wheel_entries: list[str]) -> tuple[int, str, str]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "release_mt.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "git", _GIT_STUB)
    _write_executable(bin_dir / "docker", _DOCKER_STUB)
    _write_executable(bin_dir / "python3", _PYTHON_STUB.format(python=sys.executable))
    docker_log = tmp_path / "docker.log"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "FAKE_TAG": TAG,
        "FAKE_WHEEL_ENTRIES": ",".join(wheel_entries),
        "DOCKER_LOG": str(docker_log),
    }
    result = subprocess.run(
        ["bash", str(root / "scripts" / "release_mt.sh"), TAG],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    docker_calls = docker_log.read_text() if docker_log.exists() else ""
    return result.returncode, result.stdout + result.stderr, docker_calls


def test_release_builds_image_when_wheel_contains_oncotree_snapshot(
    tmp_path: Path,
) -> None:
    code, output, docker_calls = _run_release(
        tmp_path, ["openmed/__init__.py", "openmed/data/oncotree/", SNAPSHOT_ENTRY]
    )

    assert code == 0, output
    assert f"openmed-lean:{TAG}" in docker_calls
    assert "wheel: dist/openmed-3.2.0-py3-none-any.whl" in output


def test_release_accepts_wheel_without_explicit_directory_entries(
    tmp_path: Path,
) -> None:
    code, output, _ = _run_release(tmp_path, ["openmed/__init__.py", SNAPSHOT_ENTRY])

    assert code == 0, output


@pytest.mark.parametrize(
    "wheel_entries",
    [
        ["openmed/__init__.py"],
        ["openmed/__init__.py", "openmed/data/oncotree/"],
        ["openmed/__init__.py", "openmed/data/oncotree/README.txt"],
        ["openmed/__init__.py", "openmed/core/policies/ccia_l1.json"],
    ],
    ids=["no-oncotree", "empty-dir", "no-json", "json-elsewhere"],
)
def test_release_aborts_before_image_when_wheel_lacks_oncotree_json(
    tmp_path: Path, wheel_entries: list[str]
) -> None:
    code, output, docker_calls = _run_release(tmp_path, wheel_entries)

    assert code != 0
    assert "openmed/data/oncotree/" in output
    assert docker_calls == ""
