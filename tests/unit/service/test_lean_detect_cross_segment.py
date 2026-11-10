"""Cross-segment propagation of labelled values in ``POST /v1/detect``.

The consumer sends a document as several segments (table rows, page blocks).
A birth date or record number labelled in one segment must also tag its
unlabelled copies in the other segments of the same request (final review I2).
Every value here is synthetic. The model is always stubbed.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from openmed.processing.outputs import PredictionResult
from openmed.service.lean.app import create_app, get_model_detector

UR = "0063391"
DOB = "15/03/1972"
SURNAME = "TREVANION"
SEGMENT_1 = f"UR: {UR}\nDOB: {DOB}"
SEGMENT_2 = f"Page 2 header: {SURNAME}, Edith {UR} {DOB}"


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: _stub_detector
    return TestClient(app, raise_server_exceptions=False)


def _detect(client, segments, policy="ccia_l3", **extra):
    payload = {
        "segments": [{"segment_id": sid, "text": text} for sid, text in segments],
        "policy": policy,
        **extra,
    }
    response = client.post("/v1/detect", json=payload)
    assert response.status_code == 200, response.text
    return response


def _at(findings, segment_id, start, end):
    return [
        (f["label"], f.get("subtype"))
        for f in findings
        if f["segment_id"] == segment_id and f["start"] < end and f["end"] > start
    ]


def _assert_segment_2_copies(findings, text, segment_id="s2"):
    ur = text.index(UR)
    dob = text.index(DOB)
    assert _at(findings, segment_id, ur, ur + len(UR)) == [("ID_NUM", "AU_UR")]
    assert _at(findings, segment_id, dob, dob + len(DOB)) == [
        ("DATE_OF_BIRTH", "AU_HEADER")
    ]
    # Offsets index the segment's own text.
    spans = {(f["start"], f["end"]) for f in findings if f["segment_id"] == segment_id}
    assert (ur, ur + len(UR)) in spans
    assert (dob, dob + len(DOB)) in spans


def test_labelled_values_tag_copies_in_another_segment(client):
    findings = _detect(client, [("s1", SEGMENT_1), ("s2", SEGMENT_2)]).json()[
        "findings"
    ]
    _assert_segment_2_copies(findings, SEGMENT_2)
    # The labelled segment is unchanged.
    ur = SEGMENT_1.index(UR)
    assert _at(findings, "s1", ur, ur + len(UR)) == [("ID_NUM", "AU_UR")]


def test_label_in_a_later_segment_tags_an_earlier_copy(client):
    findings = _detect(client, [("s2", SEGMENT_2), ("s1", SEGMENT_1)]).json()[
        "findings"
    ]
    _assert_segment_2_copies(findings, SEGMENT_2)
    # Findings stay in request segment order.
    assert [f["segment_id"] for f in findings] == sorted(
        (f["segment_id"] for f in findings), key=["s2", "s1"].index
    )


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_offsets_stay_per_segment_with_leading_whitespace(client, preserve_whitespace):
    padded = "   \n  " + SEGMENT_2
    findings = _detect(
        client,
        [("s1", SEGMENT_1), ("s2", padded)],
        preserve_whitespace=preserve_whitespace,
    ).json()["findings"]
    _assert_segment_2_copies(findings, padded)


def test_segments_without_cross_copies_are_unchanged(client):
    other = "Seen 02/02/2026 for review. Batch 77120334 logged."
    alone = [
        _detect(client, [("a", SEGMENT_1)]).json()["findings"],
        _detect(client, [("b", other)]).json()["findings"],
    ]
    together = _detect(client, [("a", SEGMENT_1), ("b", other)]).json()["findings"]
    assert together == alone[0] + alone[1]


def test_unlabelled_copy_alone_is_not_propagated(client):
    findings = _detect(client, [("s2", SEGMENT_2)]).json()["findings"]
    ur = SEGMENT_2.index(UR)
    assert ("ID_NUM", "AU_UR") not in _at(findings, "s2", ur, ur + len(UR))


def test_non_au_policy_does_not_propagate_across_segments(client):
    findings = _detect(
        client, [("s1", SEGMENT_1), ("s2", SEGMENT_2)], policy="hipaa_safe_harbor"
    ).json()["findings"]
    assert not [f for f in findings if f.get("subtype") == "AU_UR"]
    assert not [f for f in findings if f["label"] == "DATE_OF_BIRTH"]


def test_cross_segment_propagation_logs_and_returns_no_values(client, caplog):
    with caplog.at_level(logging.DEBUG):
        response = _detect(client, [("s1", SEGMENT_1), ("s2", SEGMENT_2)])
    blob = caplog.text + "\n".join(
        f"{record.getMessage()} {record.args}" for record in caplog.records
    )
    for value in (UR, DOB, SURNAME, "Edith"):
        assert value not in blob
        assert value not in response.text
