"""The lean service image must carry the model runtime.

Without the ``hf`` extra (torch, transformers) every ``/v1/detect`` and
``/v1/selftest`` call fails with ``DETECT_FAILED`` while ``/health`` still
passes, so the image would look healthy but detect nothing.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "docker" / "lean-service.Dockerfile"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _installed_extras() -> set[str]:
    match = re.search(r'\.whl\)\[([a-z0-9,\-]+)\]"', _dockerfile())
    assert match, "the wheel install line with extras was not found"
    return set(match.group(1).split(","))


def test_image_installs_service_lean_pdf_and_model_runtime_extras():
    assert {"service", "lean-pdf", "hf"} <= _installed_extras()


def test_hf_extra_provides_torch_and_transformers():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    hf = " ".join(pyproject["project"]["optional-dependencies"]["hf"])
    assert "torch" in hf
    assert "transformers" in hf


def test_torch_comes_from_the_cpu_wheel_index():
    text = _dockerfile()
    assert "https://download.pytorch.org/whl/cpu" in text
    cpu_install = text.index("download.pytorch.org/whl/cpu")
    wheel_install = text.index(".whl)[")
    assert cpu_install < wheel_install, "CPU torch must be installed first"


def test_image_never_installs_pymupdf_or_bakes_the_audit_key():
    text = _dockerfile()
    install_lines = [
        line
        for line in text.splitlines()
        if "pip install" in line and not line.startswith("#")
    ]
    assert not any(re.search(r"pymupdf|fitz", line, re.I) for line in install_lines)
    assert not re.search(r"^\s*(ENV|ARG)\s+OPENMED_AUDIT_KEY", text, re.M)
