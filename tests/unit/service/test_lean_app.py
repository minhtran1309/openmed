"""Tests for the lean service skeleton (health, provenance, errors, schemas)."""

from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from openmed.service.lean.app import create_app, get_provenance_payload
from openmed.service.lean.errors import ERROR_CODES, LeanError

SYNTHETIC_TEXT = "ZZ-SYNTHETIC-PATIENT-7731 lives at 99 Nowhere Lane"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_provenance(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OPENMED_FORK_TAG", "v3.2.0-mt.1")
    body = client.get("/v1/provenance").json()
    assert set(body) == {"openmed_version", "fork_tag", "models", "policies"}
    assert body["fork_tag"] == "v3.2.0-mt.1"
    assert body["policies"]
    assert all(v.startswith("sha256:") for v in body["policies"].values())


def test_error_vocabulary_is_fixed() -> None:
    assert ERROR_CODES == (
        "INVALID_REQUEST",
        "INTERNAL_ERROR",
        "NOT_FOUND",
        "UNKNOWN_POLICY",
        "EMPTY_DOCUMENT",
        "DUPLICATE_SEGMENT_ID",
        "UNKNOWN_SEGMENT_ID",
        "SPAN_OUT_OF_RANGE",
        "SPAN_INVERTED",
        "UNKNOWN_LABEL",
        "UNSUPPORTED_TREATMENT",
        "DATE_SHIFT_KEY_REQUIRED",
        "DETECT_FAILED",
        "APPLY_FAILED",
        "AUDIT_KEY_MISSING",
    )
    with pytest.raises(ValueError):
        LeanError("NOT_A_CODE")


def test_forced_internal_error_hides_text_and_logs(caplog) -> None:
    app = create_app()

    def boom():
        raise RuntimeError(f"failure containing {SYNTHETIC_TEXT}")

    app.dependency_overrides[get_provenance_payload] = boom
    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/v1/provenance", headers={"x-note": SYNTHETIC_TEXT})
    assert response.status_code == 500
    assert response.json() == {"error_code": "INTERNAL_ERROR"}
    assert "ZZ-SYNTHETIC" not in caplog.text
    assert "Nowhere" not in caplog.text


def test_unhandled_error_does_not_reraise_or_log_text(caplog) -> None:
    """Default TestClient re-raises server errors; ours must not leak or raise."""
    app = create_app()

    def boom():
        raise RuntimeError(f"failure containing {SYNTHETIC_TEXT}")

    app.dependency_overrides[get_provenance_payload] = boom
    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger=None):
        response = client.get("/v1/provenance")
    assert response.status_code == 500
    assert response.json() == {"error_code": "INTERNAL_ERROR"}
    for record in caplog.records:
        blob = f"{record.getMessage()} {record.exc_text or ''} {record.exc_info}"
        assert "ZZ-SYNTHETIC" not in blob
        assert "Nowhere" not in blob
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


def test_library_error_with_code_and_message_does_not_leak(caplog) -> None:
    app = create_app()

    class FakeLibError(Exception):
        error_code = "APPLY_FAILED"

    def boom():
        raise FakeLibError(SYNTHETIC_TEXT)

    app.dependency_overrides[get_provenance_payload] = boom
    with caplog.at_level(logging.DEBUG, logger=None):
        response = TestClient(app).get("/v1/provenance")
    assert response.json() == {"error_code": "APPLY_FAILED"}
    assert "ZZ-SYNTHETIC" not in caplog.text


def test_error_code_exception_maps_through_without_logging_text(caplog) -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/v1/_t")
    def _t():
        raise LeanError("UNKNOWN_POLICY")

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/v1/_t")
    assert response.status_code == 404
    assert response.json() == {"error_code": "UNKNOWN_POLICY"}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_library_exception_with_error_code_attribute_maps() -> None:
    app = create_app()

    class FakeLibError(Exception):
        error_code = "SPAN_INVERTED"

    @app.get("/v1/_t2")
    def _t2():
        raise FakeLibError(SYNTHETIC_TEXT)

    response = TestClient(app, raise_server_exceptions=False).get("/v1/_t2")
    assert response.json() == {"error_code": "SPAN_INVERTED"}
    assert SYNTHETIC_TEXT not in response.text


def test_validation_and_unknown_route_use_error_codes(client: TestClient) -> None:
    unknown = client.get("/v1/nope")
    assert unknown.status_code == 404
    assert unknown.json() == {"error_code": "NOT_FOUND"}
    wrong_method = client.post("/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json() == {"error_code": "INVALID_REQUEST"}


def test_schemas_served(client: TestClient) -> None:
    for name in ("health_response", "provenance_response", "error_response"):
        response = client.get(f"/v1/schemas/{name}")
        assert response.status_code == 200
        assert response.json()["type"] == "object"
    missing = client.get("/v1/schemas/unknown")
    assert missing.status_code == 404
    assert missing.json() == {"error_code": "NOT_FOUND"}


def test_error_schema_enumerates_codes(client: TestClient) -> None:
    schema = client.get("/v1/schemas/error_response").json()
    assert schema["properties"]["error_code"]["enum"] == list(ERROR_CODES)
