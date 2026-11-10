"""Tests for ``POST /v1/apply`` on the lean service.

All names, dates, keys and secrets are synthetic. Apply never detects, so no
model stub is needed: a detection call would fail the test.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from openmed.__about__ import __version__
from openmed.core.date_shift import stable_offset_for
from openmed.core.provenance import policy_fingerprint
from openmed.service.lean import apply as apply_module
from openmed.service.lean.app import create_app
from openmed.service.lean.schemas import schema_names

SYNTHETIC_KEY = "synthetic-patient-key-5521"
SYNTHETIC_SECRET = "synthetic-secret-material-9917"
NAME = "Zelphira Quandt"
DATE = "04/07/2023"


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
    t1 = f"Patient {NAME} admitted {DATE}; in 2019 too."
    t2 = f"{NAME.upper()} discharged."
    return [
        _seg(
            "s1",
            t1,
            [
                _span("n1", t1, NAME, "PERSON"),
                _span("d1", t1, DATE, "DATE"),
                _span("y1", t1, "2019", "DATE"),
            ],
        ),
        _seg("s2", t2, [_span("n2", t2, NAME.upper(), "PERSON")]),
    ]


def test_apply_renders_mark_and_shift(client) -> None:
    response = _apply(client, _doc(), offset_days=10)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"segments", "spans", "merges", "provenance"}
    assert body["segments"] == [
        {
            "segment_id": "s1",
            "text": "Patient [PERSON_1] admitted 14/07/2023; in [DATE_1] too.",
        },
        {"segment_id": "s2", "text": "[PERSON_1] discharged."},
    ]
    texts = {s["segment_id"]: s["text"] for s in body["segments"]}
    expected = {
        "n1": ("mark", "[PERSON_1]"),
        "d1": ("shift", "14/07/2023"),
        "y1": ("mark", "[DATE_1]"),
        "n2": ("mark", "[PERSON_1]"),
    }
    assert [s["span_id"] for s in body["spans"]] == ["n1", "d1", "y1", "n2"]
    for span in body["spans"]:
        assert set(span) == {"span_id", "segment_id", "action", "out_start", "out_end"}
        action, rendered = expected[span["span_id"]]
        assert span["action"] == action
        sliced = texts[span["segment_id"]][span["out_start"] : span["out_end"]]
        assert sliced == rendered
    assert body["merges"] == []
    assert body["provenance"] == {
        "openmed_version": __version__,
        "policy_fingerprint": policy_fingerprint("ccia_l1"),
    }


@pytest.mark.parametrize("policy", ["ccia_l1", "ccia_l2", "ccia_l3"])
def test_apply_marks_au_canonical_labels_and_keeps_id_num_and_ssn(
    client, policy
) -> None:
    # Synthetic checksum-valid AU values (see test_au_identifiers.py) plus a
    # synthetic SSN-shaped value; apply never detects, so labels are given.
    text = (
        "Medicare 2123 45670 1, TFN 123 456 782, IHI 8003601234567894, "
        "ABN 11 345 678 919, ACN 123 456 780, ID 445566, SSN 078-05-1120."
    )
    spans = [
        _span("m", text, "2123 45670 1", "MEDICARE_NUMBER", subtype="AU_MEDICARE"),
        _span("t", text, "123 456 782", "TFN", subtype="AU_TFN"),
        _span("i", text, "8003601234567894", "IHI", subtype="AU_IHI"),
        _span("a", text, "11 345 678 919", "ABN", subtype="AU_ABN"),
        _span("c", text, "123 456 780", "ACN", subtype="AU_ACN"),
        _span("g", text, "445566", "ID_NUM"),
        _span("s", text, "078-05-1120", "SSN"),
    ]
    response = _apply(client, [_seg("s1", text, spans)], policy=policy)

    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["text"] == (
        "Medicare [MEDICARE_NUMBER_1], TFN [TFN_1], IHI [IHI_1], "
        "ABN [ABN_1], ACN [ACN_1], ID [ID_NUM_1], SSN [SSN_1]."
    )
    assert {span["action"] for span in body["spans"]} == {"mark"}


@pytest.mark.parametrize("policy", ["ccia_l1", "ccia_l2", "ccia_l3"])
def test_apply_marks_au_clinician_and_dva_labels(client, policy) -> None:
    # Synthetic values in the ticket 05 formats; apply never detects, so the
    # canonical labels are given.
    text = "AHPRA MED0001234567, DVA QAB12345, Provider No 123456AF."
    spans = [
        _span("a", text, "MED0001234567", "AHPRA_NUMBER", subtype="AHPRA"),
        _span("d", text, "QAB12345", "DVA_NUMBER", subtype="AU_DVA"),
        _span("p", text, "123456AF", "MEDICARE_PROVIDER_NUMBER", subtype="AU_PROVIDER"),
    ]
    response = _apply(client, [_seg("s1", text, spans)], policy=policy)

    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["text"] == (
        "AHPRA [AHPRA_NUMBER_1], DVA [DVA_NUMBER_1], "
        "Provider No [MEDICARE_PROVIDER_NUMBER_1]."
    )
    assert {span["action"] for span in body["spans"]} == {"mark"}


def test_apply_with_patient_key_and_secret(client) -> None:
    offset = stable_offset_for(SYNTHETIC_KEY, max_days=365, secret=SYNTHETIC_SECRET)
    keyed = _apply(client, _doc(), patient_key=SYNTHETIC_KEY, secret=SYNTHETIC_SECRET)
    explicit = _apply(client, _doc(), offset_days=offset)
    assert keyed.status_code == 200
    assert keyed.json() == explicit.json()
    assert SYNTHETIC_KEY not in keyed.text
    assert SYNTHETIC_SECRET not in keyed.text


def test_merges_are_reported(client) -> None:
    text = "Dr Ari Volk attended."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("inner", text, "Ari Volk", "PERSON"),
                _span("outer", text, "Dr Ari Volk", "PERSON"),
            ],
        )
    ]
    body = _apply(client, doc).json()
    assert body["segments"][0]["text"] == "[PERSON_1] attended."
    assert body["merges"] == [{"kept_span_id": "outer", "merged_span_ids": ["inner"]}]
    assert [s["span_id"] for s in body["spans"]] == ["outer"]


def test_unspanned_identifier_comes_back_unchanged(client) -> None:
    text = f"{NAME} phone 0412 345 678"
    doc = [_seg("s", text, [_span("n", text, NAME, "PERSON")])]
    body = _apply(client, doc).json()
    assert body["segments"][0]["text"] == "[PERSON_1] phone 0412 345 678"


def test_optional_span_fields_are_accepted(client) -> None:
    text = f"{NAME} seen"
    span = _span(
        "n", text, NAME, "PERSON", subtype="X", replacement="Y", segment_id="s"
    )
    response = _apply(client, [_seg("s", text, [span])])
    assert response.status_code == 200
    assert response.json()["segments"][0]["text"] == "[PERSON_1] seen"


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def _bad_span(**overrides):
    span = {"span_id": "x", "start": 0, "end": 3, "label": "PERSON"}
    span.update(overrides)
    return [_seg("s", "Ari", [span])]


@pytest.mark.parametrize(
    ("document", "extra", "code", "status"),
    [
        ([], {}, "EMPTY_DOCUMENT", 400),
        ([_seg("s", "a"), _seg("s", "b")], {}, "DUPLICATE_SEGMENT_ID", 400),
        (_bad_span(segment_id="nope"), {}, "UNKNOWN_SEGMENT_ID", 400),
        (_bad_span(end=9), {}, "SPAN_OUT_OF_RANGE", 400),
        (_bad_span(start=-2), {}, "SPAN_OUT_OF_RANGE", 400),
        (_bad_span(start=3, end=1), {}, "SPAN_INVERTED", 400),
        (_bad_span(label="NOT_A_LABEL"), {}, "UNKNOWN_LABEL", 400),
        (_bad_span(), {"treatment": "replace"}, "UNSUPPORTED_TREATMENT", 400),
        (_bad_span(), {"treatment": "generalise"}, "UNSUPPORTED_TREATMENT", 400),
        (_bad_span(), {"treatment": "keep"}, "UNSUPPORTED_TREATMENT", 400),
        (_bad_span(), {"treatment": "scramble"}, "INVALID_REQUEST", 400),
        (_bad_span(), {"policy": "no_such_policy"}, "UNKNOWN_POLICY", 404),
        (
            _bad_span(label="DATE"),
            {},
            "DATE_SHIFT_KEY_REQUIRED",
            400,
        ),
        (_bad_span(), {"offset_days": 0}, "INVALID_REQUEST", 400),
        (_bad_span(), {"offset_days": "10"}, "INVALID_REQUEST", 400),
        (_bad_span(), {"patient_key": SYNTHETIC_KEY}, "INVALID_REQUEST", 400),
        (_bad_span(), {"unexpected": 1}, "INVALID_REQUEST", 400),
        (_bad_span(start="0"), {}, "INVALID_REQUEST", 400),
    ],
)
def test_fail_closed_codes(client, document, extra, code, status) -> None:
    payload = {"document": document, "treatment": "mark", "policy": "ccia_l1"}
    payload.update(extra)
    response = client.post("/v1/apply", json=payload)
    assert response.status_code == status
    assert response.json() == {"error_code": code}


def test_unexpected_failure_is_apply_failed(client, monkeypatch, caplog) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError(f"failure mentioning {NAME} {SYNTHETIC_SECRET}")

    monkeypatch.setattr(apply_module, "apply_spans", boom)
    with caplog.at_level(logging.DEBUG):
        response = _apply(
            client, _doc(), patient_key=SYNTHETIC_KEY, secret=SYNTHETIC_SECRET
        )
    assert response.status_code == 500
    assert response.json() == {"error_code": "APPLY_FAILED"}
    assert NAME not in caplog.text
    assert SYNTHETIC_SECRET not in caplog.text
    assert "RuntimeError" in caplog.text


# ---------------------------------------------------------------------------
# PHI and secret safety
# ---------------------------------------------------------------------------


def _log_blob(caplog) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(str(record.args))
        parts.append(record.exc_text or "")
        parts.append(str(record.exc_info))
    return "\n".join(parts)


def test_logs_and_errors_never_contain_text_key_or_secret(client, caplog) -> None:
    secrets = {"patient_key": SYNTHETIC_KEY, "secret": SYNTHETIC_SECRET}
    responses = []
    with caplog.at_level(logging.DEBUG):
        responses.append(_apply(client, _doc(), **secrets))
        responses.append(_apply(client, _doc(), offset_days=3, **secrets))
        responses.append(_apply(client, _doc(), patient_key=SYNTHETIC_KEY))
        responses.append(_apply(client, _doc(), treatment="replace", **secrets))
        responses.append(_apply(client, _doc(), policy="nope", **secrets))
        responses.append(_apply(client, _doc(), treatment=7, **secrets))
        bad = _doc()
        bad[0]["spans"][0]["end"] = 999
        responses.append(_apply(client, bad, **secrets))

    assert responses[0].status_code == 200
    assert [r.status_code for r in responses[1:]] == [400, 400, 400, 404, 400, 400]
    blob = _log_blob(caplog)
    for response in responses:
        blob += response.text
    for value in (SYNTHETIC_KEY, SYNTHETIC_SECRET, NAME, DATE, "2019"):
        assert value not in blob


def test_response_contains_no_original_span_text(client) -> None:
    response = _apply(client, _doc(), offset_days=10)
    serialized = json.dumps(response.json())
    for value in (NAME, NAME.upper(), DATE, "2019"):
        assert value not in serialized


def test_apply_schemas_are_published(client) -> None:
    assert {"apply_request", "apply_response"} <= set(schema_names())
    request_schema = client.get("/v1/schemas/apply_request").json()
    assert set(request_schema["required"]) >= {"document", "treatment", "policy"}
    response_schema = client.get("/v1/schemas/apply_response").json()
    assert set(response_schema["required"]) == {
        "segments",
        "spans",
        "merges",
        "provenance",
    }
