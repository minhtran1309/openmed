"""Tests for the signed audit and Safe Harbor attestation on ``/v1/apply``.

All names, dates, keys and secrets are synthetic. Sentinels avoid hex-only
values, which can occur by chance inside a SHA-256 digest.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from openmed.compliance.safe_harbor import SAFE_HARBOR_CATEGORY_ORDER
from openmed.core.apply_spans import SUPPORTED_TREATMENTS
from openmed.core.audit import AuditReport, hash_text
from openmed.core.date_shift import stable_offset_for
from openmed.processing.outputs import PredictionResult
from openmed.service.lean import app as app_module
from openmed.service.lean.app import (
    AuditKeyMissingError,
    create_app,
    get_model_detector,
)

KEY = "synthetic-audit-key-not-a-secret-0001"

SYNTHETIC_KEY = "synthetic-patient-key-qwxz"
SYNTHETIC_SECRET = "synthetic-secret-material-pqrs"
NAME = "Zelphira Quandt"
DATE = "04/07/2023"
NRP = "Aboriginal and Torres Strait Islander"

MUTATIONS = [
    (("policy",), lambda v: "ccia_l2"),
    (("input_hash",), lambda v: hash_text("synthetic other input")),
    (("deidentified_text_hash",), lambda v: hash_text("synthetic other output")),
    (("document_length",), lambda v: v + 1),
    (("spans", 0, "action"), lambda v: "keep" if v != "keep" else "mask"),
    (("spans", 0, "text_hash"), lambda v: v[:-1] + ("0" if v[-1] != "0" else "1")),
    (("resolved_profile", "segments", 0, "output_length"), lambda v: v + 1),
    (("signature", "value"), lambda v: v[:-1] + ("0" if v[-1] != "0" else "1")),
]

FORBIDDEN_KEYS = {"text", "original_text", "offset_days", "patient_key", "secret"}


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


def _apply(client, document, *, treatment="mark", policy="ccia_l1", **extra):
    payload = {
        "document": document,
        "treatment": treatment,
        "policy": policy,
        **extra,
    }
    return client.post("/v1/apply", json=payload)


def _doc():
    t1 = f"Patient {NAME} admitted {DATE} with asthma."
    t2 = f"{NAME.upper()} discharged home."
    return [
        _seg(
            "s1",
            t1,
            [
                _span("n1", t1, NAME, "PERSON", source="model", score=0.91),
                _span("d1", t1, DATE, "DATE", source="pattern", score=1.0),
            ],
        ),
        _seg("s2", t2, [_span("n2", t2, NAME.upper(), "PERSON")]),
    ]


def _mutate(data, path, change):
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = change(target[path[-1]])


def _canonical(data) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def test_apply_returns_signed_audit_that_verifies(client) -> None:
    response = _apply(client, _doc(), offset_days=10)
    assert response.status_code == 200
    audit = response.json()["audit"]
    assert audit["signature"]["key_id"] == "openmed-lean"
    assert audit["signature"]["algorithm"] == "HMAC-SHA256"
    assert AuditReport.from_dict(copy.deepcopy(audit)).verify(KEY) is True
    for path, change in MUTATIONS:
        mutated = copy.deepcopy(audit)
        _mutate(mutated, path, change)
        assert AuditReport.from_dict(mutated).verify(KEY) is False, path
    assert AuditReport.from_dict(copy.deepcopy(audit)).verify("wrong-key") is False


def test_signature_reproducible_with_stdlib_only(client) -> None:
    audit = _apply(client, _doc(), offset_days=10).json()["audit"]
    signature = audit.pop("signature")
    expected = hmac.new(KEY.encode("utf-8"), _canonical(audit), hashlib.sha256)
    assert expected.hexdigest() == signature["value"]
    repro_hash = audit.pop("repro_hash")
    digest = hashlib.sha256(_canonical(audit)).hexdigest()
    assert f"sha256:{digest}" == repro_hash


def test_deidentified_text_hash_matches_output_segments(client) -> None:
    document = _doc()
    body = _apply(client, document, offset_days=10).json()
    audit = body["audit"]
    joined_out = "\n".join(s["text"] for s in body["segments"])
    assert hash_text(joined_out) == audit["deidentified_text_hash"]
    assert audit["document_length"] == len(joined_out)
    joined_in = "\n".join(s["text"] for s in document)
    keyed_in = hmac.new(KEY.encode(), joined_in.encode(), hashlib.sha256)
    assert audit["input_hash"] == f"hmac-sha256:{keyed_in.hexdigest()}"
    assert audit["input_hash"] != hash_text(joined_in)
    for span in audit["spans"]:
        out_span = next(
            s for s in body["spans"] if s["span_id"] == span["evidence"]["span_id"]
        )
        segment_index = [s["segment_id"] for s in body["segments"]].index(
            out_span["segment_id"]
        )
        base = sum(len(s["text"]) + 1 for s in body["segments"][:segment_index])
        assert span["start"] == base + out_span["out_start"]
        assert span["end"] == base + out_span["out_end"]


@pytest.mark.parametrize("treatment", SUPPORTED_TREATMENTS)
@pytest.mark.parametrize("policy", ["ccia_l1", "ccia_l2", "ccia_l3"])
@pytest.mark.parametrize(
    "shift",
    [
        {"offset_days": 10},
        {"patient_key": SYNTHETIC_KEY, "secret": SYNTHETIC_SECRET},
    ],
    ids=["offset", "keyed"],
)
def test_no_phi_in_audit_or_attestation(client, treatment, policy, shift) -> None:
    document = _doc()
    text = f"Identifies as {NRP}."
    document.append(_seg("s3", text, [_span("e1", text, NRP, "ETHNICITY")]))
    response = _apply(client, document, treatment=treatment, policy=policy, **shift)
    assert response.status_code == 200
    body = response.json()
    assert KEY not in response.text
    # An unkeyed input hash would let a reader brute-force names and offsets.
    unkeyed_input = hash_text("\n".join(s["text"] for s in document))
    for name in ("audit", "attestation"):
        serialized = json.dumps(body[name])
        for value in (
            NAME,
            NAME.upper(),
            DATE,
            NRP,
            SYNTHETIC_KEY,
            SYNTHETIC_SECRET,
            KEY,
            unkeyed_input,
        ):
            assert value not in serialized, name
        assert not FORBIDDEN_KEYS & set(_walk_keys(body[name])), name
    for span in body["audit"]["spans"]:
        assert span["surrogate"] is None
        assert span["context"] == {}
        assert span["text_hash"].startswith("hmac-sha256:")


def test_keyed_and_explicit_offset_give_identical_audit(client) -> None:
    offset = stable_offset_for(SYNTHETIC_KEY, max_days=365, secret=SYNTHETIC_SECRET)
    keyed = _apply(client, _doc(), patient_key=SYNTHETIC_KEY, secret=SYNTHETIC_SECRET)
    explicit = _apply(client, _doc(), offset_days=offset)
    assert keyed.status_code == explicit.status_code == 200
    assert keyed.json()["audit"] == explicit.json()["audit"]
    assert keyed.json()["attestation"] == explicit.json()["attestation"]


def test_keep_span_is_audited_as_keep(client) -> None:
    text = f"{NAME} identifies as {NRP}."
    document = [
        _seg(
            "s1",
            text,
            [_span("n1", text, NAME, "PERSON"), _span("e1", text, NRP, "ETHNICITY")],
        )
    ]
    body = _apply(client, document, policy="ccia_l1").json()
    spans = {s["evidence"]["span_id"]: s for s in body["audit"]["spans"]}
    assert spans["e1"]["action"] == "keep"
    assert spans["e1"]["canonical_label"] == "ETHNICITY"
    assert body["audit"]["residual_risk"]["keep_span_count"] == 1
    categories = {c["category"]: c for c in body["attestation"]["categories"]}
    unique = categories["UNIQUE_IDENTIFIER"]
    assert unique["applied_action_counts"] == {"keep": 1}
    assert unique["residual_risk"] is True


def test_attestation_lists_18_categories_and_shift_is_not_removal(client) -> None:
    body = _apply(client, _doc(), offset_days=10).json()
    audit = body["audit"]
    attestation = body["attestation"]
    assert [c["category"] for c in attestation["categories"]] == list(
        SAFE_HARBOR_CATEGORY_ORDER
    )
    for category in attestation["categories"]:
        assert type(category["detection_count"]) is int
    categories = {c["category"]: c for c in attestation["categories"]}
    assert categories["NAME"]["detection_count"] == 2
    assert categories["NAME"]["applied_action_counts"] == {"mask": 2}
    date = categories["DATE_ELEMENT"]
    assert date["applied_action_counts"]["shift"] == 1
    assert date["residual_risk"] is True
    assert "shifting" in date["residual_risk_reason"]
    assert attestation["source_report_hash"] == audit["repro_hash"]
    assert attestation["policy"] == "ccia_l1"


def test_clinical_label_span_is_audited_not_attested(client) -> None:
    text = f"{NAME} has asthma."
    document = [
        _seg(
            "s1",
            text,
            [_span("n1", text, NAME, "PERSON"), _span("x1", text, "asthma", "DISEASE")],
        )
    ]
    response = _apply(client, document, policy="ccia_l2")
    assert response.status_code == 200
    body = response.json()
    labels = [s["canonical_label"] for s in body["audit"]["spans"]]
    assert sorted(labels) == ["DISEASE", "PERSON"]
    assert body["attestation"]["summary"]["detection_count"] == 1
    total = sum(c["detection_count"] for c in body["attestation"]["categories"])
    assert total == 1


def test_service_refuses_to_start_without_audit_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENMED_AUDIT_KEY", raising=False)
    app = create_app()
    with pytest.raises(AuditKeyMissingError, match="AUDIT_KEY_MISSING"):
        with TestClient(app):
            pass
    monkeypatch.setenv("OPENMED_AUDIT_KEY", "  ")
    with pytest.raises(AuditKeyMissingError, match="AUDIT_KEY_MISSING"):
        with TestClient(create_app()):
            pass
    # Importing the module (which builds ``app``) never needs the key. Restore
    # the original bindings so later tests keep matching dependency objects.
    original = dict(vars(app_module))
    try:
        importlib.reload(app_module)
    finally:
        vars(app_module).update(original)


def test_service_starts_with_audit_key() -> None:
    with TestClient(create_app()) as started:
        assert started.get("/health").json() == {"status": "ok"}


def test_audit_key_missing_error_carries_code() -> None:
    error = AuditKeyMissingError()
    assert error.error_code == "AUDIT_KEY_MISSING"
    assert str(error) == "AUDIT_KEY_MISSING"
    assert isinstance(error, RuntimeError)


def _empty_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


def test_apply_without_key_is_audit_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENMED_AUDIT_KEY", raising=False)
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: _empty_detector
    client = TestClient(app, raise_server_exceptions=False)
    response = _apply(client, _doc(), offset_days=10)
    assert response.status_code == 500
    assert response.json() == {"error_code": "AUDIT_KEY_MISSING"}
    assert client.post("/v1/selftest").status_code == 200


def test_apply_accepts_source_and_score_and_rejects_bad_score(client) -> None:
    document = _doc()
    document[0]["spans"][0]["score"] = 1.5
    assert _apply(client, document, offset_days=10).json() == {
        "error_code": "INVALID_REQUEST"
    }
    document = _doc()
    document[0]["spans"][0]["source"] = "oracle"
    assert _apply(client, document, offset_days=10).json() == {
        "error_code": "INVALID_REQUEST"
    }
    document = _doc()
    document[0]["spans"][0]["source"] = "manual"
    assert _apply(client, document, offset_days=10).status_code == 200


def test_source_and_score_do_not_change_rendering(client) -> None:
    plain = _doc()
    for segment in plain:
        for span in segment["spans"]:
            span.pop("source", None)
            span.pop("score", None)
    with_fields = _apply(client, _doc(), offset_days=10).json()
    without = _apply(client, plain, offset_days=10).json()
    for key in ("segments", "spans", "merges", "provenance"):
        assert with_fields[key] == without[key]
