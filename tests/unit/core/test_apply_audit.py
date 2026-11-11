"""Tests for the signed audit report built for an apply render.

All names, dates and keys are synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re

import pytest

from openmed.core.apply_audit import (
    AUDIT_KEY_ENV,
    AUDIT_KEY_ID,
    SEGMENT_JOIN,
    build_apply_audit,
    joined_text,
)
from openmed.core.apply_spans import apply_spans
from openmed.core.audit import AuditReport, hash_text
from openmed.core.provenance import policy_fingerprint

KEY = "synthetic-audit-key-not-a-secret-0001"
NAME = "Zelphira Quandt"
DATE = "04/07/2023"


def _span(span_id, text, value, label, **extra):
    start = text.index(value)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(value),
        "label": label,
        **extra,
    }


def _document():
    t1 = f"Patient {NAME} admitted {DATE} with asthma."
    t2 = f"{NAME.upper()} discharged home."
    return [
        {
            "segment_id": "s1",
            "text": t1,
            "spans": [
                _span("n1", t1, NAME, "PERSON", source="model", score=0.93),
                _span("d1", t1, DATE, "DATE", source="pattern"),
            ],
        },
        {
            "segment_id": "s2",
            "text": t2,
            "spans": [_span("n2", t2, NAME.upper(), "PERSON")],
        },
    ]


def _render(document, policy="ccia_l1"):
    result = apply_spans(document, treatment="mark", policy=policy, offset_days=10)
    payload = result.to_dict()
    report = build_apply_audit(document, payload, policy=policy, key=KEY)
    return payload, report


def _mutate(data, path, change):
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = change(target[path[-1]])


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


def test_constants() -> None:
    assert SEGMENT_JOIN == "\n"
    assert AUDIT_KEY_ENV == "OPENMED_AUDIT_KEY"
    assert AUDIT_KEY_ID == "openmed-lean"


def test_audit_verifies_and_any_field_change_fails() -> None:
    _, report = _render(_document())
    data = report.to_dict()
    assert data["signature"]["key_id"] == AUDIT_KEY_ID
    assert AuditReport.from_dict(copy.deepcopy(data)).verify(KEY) is True
    for path, change in MUTATIONS:
        mutated = copy.deepcopy(data)
        _mutate(mutated, path, change)
        assert AuditReport.from_dict(mutated).verify(KEY) is False, path
    assert AuditReport.from_dict(copy.deepcopy(data)).verify("wrong-key") is False


def test_hashes_follow_the_join_rule() -> None:
    document = _document()
    payload, report = _render(document)
    joined_in = SEGMENT_JOIN.join(s["text"] for s in document)
    joined_out = joined_text(payload["segments"])
    assert joined_out == SEGMENT_JOIN.join(s["text"] for s in payload["segments"])
    expected_in = hmac.new(KEY.encode(), joined_in.encode(), hashlib.sha256)
    assert report.input_hash == f"hmac-sha256:{expected_in.hexdigest()}"
    assert report.deidentified_text_hash == hash_text(joined_out)
    assert report.document_length == len(joined_out)
    assert report.verify(KEY, deidentified_text=joined_out)
    assert report.resolved_profile["segments"] == [
        {
            "segment_id": s_in["segment_id"],
            "input_length": len(s_in["text"]),
            "output_length": len(s_out["text"]),
        }
        for s_in, s_out in zip(document, payload["segments"], strict=True)
    ]


def test_output_offsets_index_joined_output() -> None:
    payload, report = _render(_document())
    joined_out = joined_text(payload["segments"])
    rendered = {
        span.evidence["span_id"]: joined_out[span.start : span.end]
        for span in report.spans
    }
    assert rendered == {"n1": "[PERSON_1]", "d1": "14/07/2023", "n2": "[PERSON_1]"}
    actions = {span.evidence["span_id"]: span.action for span in report.spans}
    assert actions == {"n1": "mask", "d1": "shift", "n2": "mask"}


def test_span_fields_carry_provenance_without_text() -> None:
    _, report = _render(_document())
    spans = {span.evidence["span_id"]: span for span in report.spans}
    assert spans["n1"].sources == ["model"]
    assert spans["n1"].confidence == pytest.approx(0.93)
    assert spans["d1"].sources == ["pattern"]
    assert spans["d1"].confidence == 1.0
    assert spans["n2"].sources == ["caller"]
    for span in report.spans:
        assert span.threshold == 0.0
        assert span.surrogate is None
        assert span.context == {}
        assert span.label == span.canonical_label
        assert set(span.evidence) == {
            "span_id",
            "segment_id",
            "apply_action",
            "merged_span_ids",
        }
    assert {d.source for d in report.detectors} == {"model", "pattern", "caller"}
    assert report.safety_sweep == {"enabled": False}
    assert report.thresholds == {}
    assert report.residual_risk == {
        "keep_span_count": 0,
        "shift_span_count": 1,
        "replace_span_count": 0,
        "replacement_span_count": 0,
        "generalise_span_count": 0,
    }


def test_merged_cluster_is_one_audit_span() -> None:
    text = f"Seen {NAME} Jr today."
    start = text.index(NAME)
    document = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [
                {"span_id": "a", "start": start, "end": start + 15, "label": "PERSON"},
                {
                    "span_id": "b",
                    "start": start + 9,
                    "end": start + 18,
                    "label": "PERSON",
                },
            ],
        }
    ]
    _, report = _render(document)
    assert len(report.spans) == 1
    (span,) = report.spans
    assert span.evidence["merged_span_ids"] == ["b"]
    union = text[start : start + 18]
    assert union == f"{NAME} Jr"
    expected = hmac.new(KEY.encode(), union.encode(), hashlib.sha256).hexdigest()
    assert span.text_hash == f"hmac-sha256:{expected}"


def test_text_hash_is_keyed() -> None:
    document = _document()
    _, report = _render(document)
    spans = {span.evidence["span_id"]: span for span in report.spans}
    for span in report.spans:
        assert re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", span.text_hash)
    assert spans["n1"].text_hash != hash_text(NAME)
    assert spans["d1"].text_hash != hash_text(DATE)


def test_resolved_profile_never_records_shift_inputs() -> None:
    _, report = _render(_document())
    profile = report.resolved_profile
    assert profile["treatment"] == "mark"
    # The ccia_l1 digest holds a 13-digit run; it must not be redacted.
    assert profile["policy_fingerprint"] == policy_fingerprint("ccia_l1")
    for forbidden in ("offset_days", "patient_key", "secret", "shift_mode"):
        assert forbidden not in profile


def test_empty_key_is_rejected() -> None:
    document = _document()
    payload = apply_spans(
        document, treatment="mark", policy="ccia_l1", offset_days=10
    ).to_dict()
    with pytest.raises(ValueError):
        build_apply_audit(document, payload, policy="ccia_l1", key="")


def test_digest_values_survive_a_json_round_trip() -> None:
    _, report = _render(_document())
    data = report.to_dict()
    assert data["resolved_profile"]["policy_fingerprint"] == policy_fingerprint(
        "ccia_l1"
    )
    again = AuditReport.from_dict(copy.deepcopy(data)).to_dict()
    assert again == data


def test_input_hash_is_keyed() -> None:
    document = _document()
    payload, report = _render(document)
    joined_in = joined_text(document)
    assert re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", report.input_hash)
    assert report.input_hash != hash_text(joined_in)
    expected = hmac.new(KEY.encode(), joined_in.encode(), hashlib.sha256)
    assert report.input_hash == f"hmac-sha256:{expected.hexdigest()}"
    other = build_apply_audit(document, payload, policy="ccia_l1", key="other-key")
    assert other.input_hash != report.input_hash
    assert other.deidentified_text_hash == report.deidentified_text_hash
