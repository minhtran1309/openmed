"""Real-uvicorn check that forced errors never reach server stderr."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration

SCRIPT = """
import sys, uvicorn
from openmed.service.lean.app import create_app, get_provenance_payload

def boom():
    raise RuntimeError("failure containing ZZ-SYNTHETIC-PATIENT-7731")

app = create_app()
app.dependency_overrides[get_provenance_payload] = boom
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="debug")
"""


def test_uvicorn_does_not_log_exception_text(tmp_path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-c", SCRIPT, str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{url}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        try:
            urllib.request.urlopen(f"{url}/v1/provenance", timeout=5)
            raise AssertionError("expected an HTTP error")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            assert exc.read() == b'{"error_code":"INTERNAL_ERROR"}'
    finally:
        proc.terminate()
        output = proc.communicate(timeout=10)[0]
    assert "ZZ-SYNTHETIC" not in output
    assert "Traceback" not in output
