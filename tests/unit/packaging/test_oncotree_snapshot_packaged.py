"""The bundled OncoTree snapshot must ship in the wheel.

``openmed.core.oncotree`` loads ``openmed/data/oncotree/<version>.json`` with
``importlib.resources``. ``openmed/data`` is not a Python package, so only the
hatch file selection puts the snapshot in the wheel. These tests read the hatch
build config without building (``build`` and ``hatchling`` are not test
dependencies) and fail if the include or exclude rules ever drop the snapshot.
``scripts/release_mt.sh`` checks the built wheel itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from importlib import resources
from pathlib import Path

import pytest

from openmed.core import oncotree

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = f"openmed/{oncotree._SNAPSHOT}"


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style pattern, as hatch uses it, to a regex."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    out = []
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"{prefix}{''.join(out)}")


def _matches(patterns: list[str], path: str) -> bool:
    """Return whether ``path`` or one of its parent directories matches."""
    parts = path.split("/")
    candidates = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    regexes = [_pattern_regex(pattern) for pattern in patterns]
    return any(rx.fullmatch(c) for rx in regexes for c in candidates)


def _wheel_selection(pyproject: dict) -> dict[str, list[str]]:
    """Return the effective wheel file-selection keys.

    Wheel-target keys override the shared ``[tool.hatch.build]`` keys.
    """
    hatch = pyproject.get("tool", {}).get("hatch", {})
    build = hatch.get("build", {})
    wheel = build.get("targets", {}).get("wheel", {})
    keys = ("include", "exclude", "only-include", "packages")
    return {key: list(wheel.get(key, build.get(key, []))) for key in keys}


def wheel_selects(pyproject: dict, path: str) -> bool:
    """Return whether the hatch wheel config selects the repo-relative ``path``."""
    selection = _wheel_selection(pyproject)
    roots = selection["only-include"] + selection["packages"]
    if roots and not any(
        path == root.strip("/") or path.startswith(root.strip("/") + "/")
        for root in roots
    ):
        return False
    if selection["include"] and not _matches(selection["include"], path):
        return False
    return not _matches(selection["exclude"], path)


def _load_pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def _require_git_checkout() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    if _git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        pytest.skip("not running from a git checkout")


def test_hatch_wheel_config_selects_the_oncotree_snapshot() -> None:
    assert wheel_selects(_load_pyproject(), SNAPSHOT), (
        f"{SNAPSHOT} is not selected by the hatch wheel include/exclude rules"
    )


def test_hatch_wheel_config_selects_any_oncotree_json() -> None:
    pyproject = _load_pyproject()
    assert wheel_selects(pyproject, "openmed/data/oncotree/oncotree_2099_01_01.json")


@pytest.mark.parametrize(
    "hatch_build",
    [
        {"include": ["/openmed/**"], "exclude": ["/openmed/data/**"]},
        {"include": ["/openmed/**"], "exclude": ["*.json"]},
        {"include": ["/openmed/**"], "exclude": ["oncotree"]},
        {"include": ["/openmed/**/*.py"]},
        {"include": ["/openmed/**"], "targets": {"wheel": {"exclude": ["data/"]}}},
        {
            "include": ["/openmed/**"],
            "targets": {"wheel": {"packages": ["openmed/core"]}},
        },
        {"only-include": ["openmed/core", "openmed/cli"]},
    ],
)
def test_wheel_selection_check_fails_when_the_snapshot_is_dropped(
    hatch_build: dict,
) -> None:
    pyproject = {"tool": {"hatch": {"build": hatch_build}}}
    assert not wheel_selects(pyproject, SNAPSHOT)


def test_oncotree_snapshot_is_tracked_and_not_ignored() -> None:
    _require_git_checkout()
    tracked = _git("ls-files", "--error-unmatch", SNAPSHOT)
    assert tracked.returncode == 0, f"{SNAPSHOT} is not tracked by git"
    ignored = _git("check-ignore", "--no-index", "-q", SNAPSHOT)
    assert ignored.returncode == 1, f"{SNAPSHOT} matches a .gitignore rule"


def test_oncotree_snapshot_resolves_through_importlib_resources() -> None:
    resource = resources.files("openmed").joinpath(oncotree._SNAPSHOT)
    assert resource.is_file()
    assert resource.read_text(encoding="utf-8").lstrip().startswith(("{", "["))
