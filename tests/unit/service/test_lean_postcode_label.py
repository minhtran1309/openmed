"""AU display label: ``ZIPCODE`` is shown as ``POSTCODE`` under ``en_AU`` policies.

``ZIPCODE`` stays the canonical label inside openmed. Only lean-service output
under a policy whose ``implied_locale`` is ``en_AU`` (the ``ccia_*`` profiles)
shows ``POSTCODE``; every other policy keeps ``ZIPCODE``. All values are
synthetic.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openmed.core.apply_spans import apply_spans
from openmed.core.audit import AuditReport
from openmed.processing.outputs import PredictionResult
from openmed.service.lean.app import create_app, get_model_detector

TEXT = "Lives in Randwick NSW 2031."
POSTCODE = "2031"


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: _stub_detector
    return TestClient(app, raise_server_exceptions=False)


def _postcode_span(label: str) -> dict:
    start = TEXT.index(POSTCODE)
    return {
        "span_id": "p1",
        "start": start,
        "end": start + len(POSTCODE),
        "label": label,
    }


def _apply(client, label: str, policy: str):
    return client.post(
        "/v1/apply",
        json={
            "document": [
                {"segment_id": "s1", "text": TEXT, "spans": [_postcode_span(label)]}
            ],
            "treatment": "mark",
            "policy": policy,
            "offset_days": 10,
        },
    )


@pytest.mark.parametrize("policy", ["ccia_l1", "ccia_l2", "ccia_l3"])
def test_detect_reports_postcode_under_au_policy(client, policy) -> None:
    body = client.post(
        "/v1/detect",
        json={"segments": [{"segment_id": "s1", "text": TEXT}], "policy": policy},
    ).json()
    found = [f for f in body["findings"] if f.get("subtype") == "AU_POSTCODE"]
    assert len(found) == 1
    assert found[0]["label"] == "POSTCODE"
    assert found[0]["policy_class"] == "QUASI_IDENTIFIER"
    assert all(f["label"] != "ZIPCODE" for f in body["findings"])


def test_table_column_reports_postcode_under_au_policy(client) -> None:
    text = "Randwick  2031"
    segment = {
        "segment_id": "row1",
        "text": text,
        "table_header": ["Suburb", "Postcode"],
        "cells": [{"start": 0, "end": 8}, {"start": 10, "end": 14}],
    }
    body = client.post(
        "/v1/detect", json={"segments": [segment], "policy": "ccia_l3"}
    ).json()
    table = [f for f in body["findings"] if f.get("source") == "table-column"]
    assert [f["label"] for f in table] == ["POSTCODE"]


@pytest.mark.parametrize("label", ["ZIPCODE", "POSTCODE"])
def test_apply_tags_postcode_under_au_policy(client, label) -> None:
    response = _apply(client, label, "ccia_l3")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["segments"][0]["text"] == "Lives in Randwick NSW [POSTCODE_1]."
    audit = body["audit"]
    assert AuditReport.from_dict(audit).verify("synthetic-audit-key-not-a-secret-0001")
    assert [(s["label"], s["canonical_label"]) for s in audit["spans"]] == [
        ("POSTCODE", "ZIPCODE")
    ]
    geographic = [
        c for c in body["attestation"]["categories"] if "ZIPCODE" in c["mapped_labels"]
    ]
    assert len(geographic) == 1
    assert geographic[0]["detection_count"] == 1


def test_apply_keeps_zipcode_under_non_au_policy(client) -> None:
    response = _apply(client, "ZIPCODE", "hipaa_safe_harbor")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["segments"][0]["text"] == "Lives in Randwick NSW [ZIPCODE_1]."
    assert [s["label"] for s in body["audit"]["spans"]] == ["ZIPCODE"]


def test_apply_accepts_postcode_input_under_non_au_policy(client) -> None:
    response = _apply(client, "POSTCODE", "hipaa_safe_harbor")
    assert response.status_code == 200, response.text
    assert response.json()["segments"][0]["text"] == (
        "Lives in Randwick NSW [ZIPCODE_1]."
    )


def test_library_apply_spans_tag_follows_policy_locale() -> None:
    document = [
        {"segment_id": "s1", "text": TEXT, "spans": [_postcode_span("ZIPCODE")]}
    ]
    au = apply_spans(document, treatment="mark", policy="ccia_l3", offset_days=1)
    other = apply_spans(
        document, treatment="mark", policy="hipaa_safe_harbor", offset_days=1
    )
    assert au.segments[0]["text"].endswith("[POSTCODE_1].")
    assert other.segments[0]["text"].endswith("[ZIPCODE_1].")
