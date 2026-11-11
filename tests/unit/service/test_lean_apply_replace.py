"""HTTP tests for the ``replace`` treatment of ``POST /v1/apply`` (ticket 09).

All names, identifiers, keys and secrets are synthetic. Sentinels avoid
hex-only values, which can occur by chance inside a SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openmed.service.lean.app import create_app

SYNTHETIC_AUDIT_KEY = "synthetic-audit-key-not-a-secret-0001"
PATIENT_KEY = "synthetic-patient-key-sentinel-qwrt"
SECRET = "synthetic-secret-sentinel-zxvy"
NAME = "Zelphira Quandt"
REPLACEMENT = "Synthetic Group Wording"
CANCER = "Leiomyosarcoma of the uterus"
GOLDEN = Path(__file__).parent / "fixtures" / "apply_mark_golden.json"
_SURROGATE_HASH = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _span(span_id, text, value, label, **extra):
    start = text.index(value)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(value),
        "label": label,
        **extra,
    }


def _seg(segment_id, text, spans=()):
    return {"segment_id": segment_id, "text": text, "spans": list(spans)}


def _doc():
    t1 = f"{NAME}, Medicare 3664 73786 3, ph 0491 570 156, admitted 04/07/2023."
    t2 = f"{CANCER}; {NAME.upper()} reviewed in 2019."
    return [
        _seg(
            "s1",
            t1,
            [
                _span("n1", t1, NAME, "PERSON"),
                _span("m1", t1, "3664 73786 3", "MEDICARE_NUMBER"),
                _span("p1", t1, "0491 570 156", "PHONE"),
                _span("d1", t1, "04/07/2023", "DATE"),
            ],
        ),
        _seg(
            "s2",
            t2,
            [
                _span("c1", t2, CANCER, "CANCER", replacement=REPLACEMENT),
                _span("n2", t2, NAME.upper(), "PERSON"),
                _span("y1", t2, "2019", "DATE"),
            ],
        ),
    ]


def _apply(client, document, **extra):
    payload = {
        "document": document,
        "treatment": "replace",
        "policy": "ccia_l3",
        "patient_key": PATIENT_KEY,
        "secret": SECRET,
        **extra,
    }
    return client.post("/v1/apply", json=payload)


def _slice(body, span_id):
    span = next(s for s in body["spans"] if s["span_id"] == span_id)
    text = next(
        s["text"] for s in body["segments"] if s["segment_id"] == span["segment_id"]
    )
    return text[span["out_start"] : span["out_end"]]


def test_replace_round_trip(client) -> None:
    response = _apply(client, _doc())
    assert response.status_code == 200
    body = response.json()
    actions = {s["span_id"]: s["action"] for s in body["spans"]}
    assert actions == {
        "n1": "replace",
        "m1": "replace",
        "p1": "replace",
        "d1": "shift",
        "c1": "replacement",
        "n2": "replace",
        "y1": "mark",
    }
    assert _slice(body, "c1") == REPLACEMENT
    assert _slice(body, "y1") == "[DATE_1]"
    assert _slice(body, "n1") == _slice(body, "n2")
    joined = "\n".join(s["text"] for s in body["segments"])
    for value in (NAME, NAME.upper(), CANCER, "3664 73786 3", "0491 570 156"):
        assert value not in joined
    # ``out_*`` offsets slice exactly the rendering of each span.
    for span in body["spans"]:
        assert span["out_end"] > span["out_start"]


def test_replace_without_key_is_surrogate_key_required(client) -> None:
    payload = {"document": _doc(), "treatment": "replace", "policy": "ccia_l3"}
    response = client.post("/v1/apply", json=payload)
    assert response.status_code == 400
    assert response.json() == {"error_code": "SURROGATE_KEY_REQUIRED"}
    payload["offset_days"] = 10
    response = client.post("/v1/apply", json=payload)
    assert response.status_code == 400
    assert response.json() == {"error_code": "SURROGATE_KEY_REQUIRED"}


@pytest.mark.parametrize("replacement", ["", "x" * 257])
def test_replacement_bounds(client, replacement) -> None:
    document = _doc()
    document[1]["spans"][0]["replacement"] = replacement
    response = _apply(client, document)
    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_REQUEST"}


def test_replacement_at_max_length_is_accepted(client) -> None:
    document = _doc()
    document[1]["spans"][0]["replacement"] = "y" * 256
    response = _apply(client, document)
    assert response.status_code == 200
    assert _slice(response.json(), "c1") == "y" * 256


def test_mark_output_byte_identical_to_mt2(client) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["recorded_at"] == "55a05163b"
    assert golden["cases"]
    for case in golden["cases"]:
        response = client.post("/v1/apply", json=case["request"])
        assert response.status_code == 200, case["name"]
        body = response.json()
        for key in ("segments", "spans", "merges"):
            assert json.dumps(body[key], ensure_ascii=False) == json.dumps(
                case["expected"][key], ensure_ascii=False
            ), (case["name"], key)


def test_no_phi_in_audit_for_replace(client) -> None:
    response = _apply(client, _doc())
    assert response.status_code == 200
    body = response.json()
    surrogate = _slice(body, "n1")
    medicare = _slice(body, "m1")
    phone = _slice(body, "p1")
    assert surrogate != NAME
    for name in ("audit", "attestation"):
        serialized = json.dumps(body[name])
        for value in (
            NAME,
            NAME.upper(),
            CANCER,
            surrogate,
            medicare,
            phone,
            REPLACEMENT,
            PATIENT_KEY,
            SECRET,
            SYNTHETIC_AUDIT_KEY,
        ):
            assert value not in serialized, name
    assert PATIENT_KEY not in response.text
    assert SECRET not in response.text
    for out_key in ("spans", "merges", "provenance"):
        serialized = json.dumps(body[out_key])
        for value in (surrogate, REPLACEMENT, PATIENT_KEY, SECRET):
            assert value not in serialized, out_key
    by_id = {s["evidence"]["span_id"]: s for s in body["audit"]["spans"]}
    for span in body["audit"]["spans"]:
        value = span["surrogate"]
        assert value is None or _SURROGATE_HASH.fullmatch(value)
    for span_id in ("n1", "m1", "p1", "c1", "n2"):
        expected = hmac.new(
            SYNTHETIC_AUDIT_KEY.encode(),
            _slice(body, span_id).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert by_id[span_id]["surrogate"] == f"hmac-sha256:{expected}"
    for span_id in ("d1", "y1"):
        assert by_id[span_id]["surrogate"] is None
    assert by_id["c1"]["action"] == "replace"
    assert by_id["c1"]["evidence"]["apply_action"] == "replacement"
    assert by_id["n1"]["action"] == "replace"
    risk = body["audit"]["residual_risk"]
    assert risk["replace_span_count"] == 4
    assert risk["replacement_span_count"] == 1
    assert risk["generalise_span_count"] == 0


def test_replace_errors_and_logs_carry_no_key_or_secret(client, caplog) -> None:
    caplog.set_level("DEBUG")
    payload = {
        "document": _doc(),
        "treatment": "replace",
        "policy": "ccia_l3",
        "secret": SECRET,
    }
    response = client.post("/v1/apply", json=payload)
    assert response.status_code == 400
    assert _apply(client, _doc()).status_code == 200
    for value in (SECRET, PATIENT_KEY, NAME, REPLACEMENT):
        assert value not in response.text
        assert value not in caplog.text


def test_replacement_restating_source_is_audited_as_mask(client) -> None:
    # Review M1: a replacement equal to its source must not attest as removed.
    text = f"Seen: {NAME}."
    span = _span("n", text, NAME, "PERSON", replacement=NAME.lower())
    response = _apply(client, [_seg("s", text, [span])])
    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["text"] == "Seen: [PERSON_1]."
    assert body["spans"][0]["action"] == "mark"
    audit_span = body["audit"]["spans"][0]
    assert audit_span["action"] == "mask"
    assert audit_span["surrogate"] is None
    assert body["audit"]["residual_risk"]["replacement_span_count"] == 0
