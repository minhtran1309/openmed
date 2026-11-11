"""HTTP tests for the ``generalise`` and ``keep`` treatments of ``/v1/apply``.

Ticket 10. All names, identifiers, keys and secrets are synthetic; phones are
from the ACMA fictional ranges. Sentinels avoid hex-only values, which can
occur by chance inside a SHA-256 digest, so the generalised DOB year (all
digits) is checked against the rendered segments only.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from openmed.core.audit import AuditReport
from openmed.core.schemas.span import ACTION_VALUES
from openmed.service.lean.app import create_app

SYNTHETIC_AUDIT_KEY = "synthetic-audit-key-not-a-secret-0001"
PATIENT_KEY = "synthetic-patient-key-sentinel-gnrl"
SECRET = "synthetic-secret-sentinel-gnrlkp"
NAME = "Zelphira Quandt"
PHONE = "0491 570 156"
DOB = "28/12/1960"
DATE = "04/07/2023"
ADDRESS = "12 Smith St, Carlton VIC 3053"
ORGANISATION = "Royal Synthetic Hospital"
CANCER = "Lung Adenocarcinoma"
UNMAPPED_CANCER = "Synthetic Mystery Neoplasm"
REPLACEMENT = "Synthetic Group Wording"
REPLACED_SOURCE = "Quokkaville Reserve"
GENERALISED = {
    "p1": "04xx xxx xxx",
    "a1": "Carlton VIC",
    "o1": "hospital",
    "c1": "Non-Small Cell Lung Cancer",
}
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
    t1 = (
        f"{NAME}, DOB {DOB}, ph {PHONE}, admitted {DATE} to {ORGANISATION}. "
        f"Lives {ADDRESS}."
    )
    t2 = f"Dx {CANCER}; {UNMAPPED_CANCER}; asthma. Trial site {REPLACED_SOURCE}."
    return [
        _seg(
            "s1",
            t1,
            [
                _span("n1", t1, NAME, "PERSON", source="model", score=0.93),
                _span("b1", t1, DOB, "DATE_OF_BIRTH", source="pattern"),
                _span("p1", t1, PHONE, "PHONE"),
                _span("d1", t1, DATE, "DATE"),
                _span("o1", t1, ORGANISATION, "ORGANIZATION"),
                _span("a1", t1, ADDRESS, "STREET_ADDRESS"),
            ],
        ),
        _seg(
            "s2",
            t2,
            [
                _span("c1", t2, CANCER, "CANCER"),
                _span("c2", t2, UNMAPPED_CANCER, "CANCER"),
                _span("c3", t2, "asthma", "DISEASE"),
                _span(
                    "r1",
                    t2,
                    REPLACED_SOURCE,
                    "LOCATION",
                    replacement=REPLACEMENT,
                ),
            ],
        ),
    ]


def _apply(client, document, *, treatment, policy="ccia_l3", **extra):
    payload = {
        "document": document,
        "treatment": treatment,
        "policy": policy,
        **extra,
    }
    return client.post("/v1/apply", json=payload)


def _keyed():
    return {"patient_key": PATIENT_KEY, "secret": SECRET}


def _slices(body):
    texts = {s["segment_id"]: s["text"] for s in body["segments"]}
    return {
        s["span_id"]: (
            texts[s["segment_id"]][s["out_start"] : s["out_end"]],
            s["action"],
        )
        for s in body["spans"]
    }


def test_generalise_round_trip(client) -> None:
    response = _apply(client, _doc(), treatment="generalise", **_keyed())
    assert response.status_code == 200
    body = response.json()
    slices = _slices(body)
    assert slices["n1"] == ("[PERSON_1]", "mark")
    assert slices["p1"] == ("04xx xxx xxx", "generalise")
    assert slices["d1"][1] == "shift"
    assert slices["b1"][1] == "generalise"
    assert re.fullmatch(r"\d{4}", slices["b1"][0])
    assert slices["o1"] == ("hospital", "generalise")
    assert slices["a1"] == ("Carlton VIC", "generalise")
    assert slices["c1"] == ("Non-Small Cell Lung Cancer", "generalise")
    assert slices["c2"] == ("[CANCER_1]", "mark")
    assert slices["c3"] == ("asthma", "keep")
    assert slices["r1"] == (REPLACEMENT, "replacement")
    assert body["audit"]["resolved_profile"]["treatment"] == "generalise"
    assert AuditReport.from_dict(body["audit"]).verify(SYNTHETIC_AUDIT_KEY) is True


def test_keep_round_trip(client) -> None:
    response = _apply(client, _doc(), treatment="keep", **_keyed())
    assert response.status_code == 200
    body = response.json()
    slices = _slices(body)
    assert slices["n1"] == ("[PERSON_1]", "mark")
    assert slices["p1"] == ("[PHONE_1]", "mark")
    assert slices["b1"][1] == "shift"
    assert slices["d1"][1] == "shift"
    assert slices["o1"] == ("[ORGANIZATION_1]", "mark")
    assert slices["a1"] == ("[STREET_ADDRESS_1]", "mark")
    assert slices["c1"] == (CANCER, "keep")
    assert slices["c2"] == (UNMAPPED_CANCER, "keep")
    assert slices["c3"] == ("asthma", "keep")
    # A caller replacement marks a quasi-identifier kept for trial matching.
    assert slices["r1"] == (REPLACED_SOURCE, "keep")
    assert AuditReport.from_dict(body["audit"]).verify(SYNTHETIC_AUDIT_KEY) is True


@pytest.mark.parametrize("treatment", ["generalise", "keep"])
def test_no_key_needed_without_dates(client, treatment) -> None:
    text = f"{NAME} at {ORGANISATION}."
    document = [
        _seg(
            "s1",
            text,
            [
                _span("n1", text, NAME, "PERSON"),
                _span("o1", text, ORGANISATION, "ORGANIZATION"),
            ],
        )
    ]
    assert _apply(client, document, treatment=treatment).status_code == 200
    text = f"DOB {DOB}."
    dated = [_seg("s1", text, [_span("b1", text, DOB, "DATE_OF_BIRTH")])]
    response = _apply(client, dated, treatment=treatment)
    assert response.status_code == 400
    assert response.json()["error_code"] == "DATE_SHIFT_KEY_REQUIRED"


@pytest.mark.parametrize("treatment", ["mark", "replace", "generalise", "keep"])
def test_no_phi_in_audit_for_generalise_and_keep(client, treatment) -> None:
    response = _apply(client, _doc(), treatment=treatment, **_keyed())
    assert response.status_code == 200
    body = response.json()
    assert SYNTHETIC_AUDIT_KEY not in response.text
    slices = _slices(body)
    rendered = [
        value
        for value, action in slices.values()
        if action in {"replace", "replacement", "generalise"}
    ]
    if treatment == "generalise":
        for span_id, value in GENERALISED.items():
            assert slices[span_id] == (value, "generalise")
        assert REPLACEMENT in rendered
    sentinels = [
        NAME,
        PHONE,
        DOB,
        DATE,
        ADDRESS,
        "Carlton",
        ORGANISATION,
        CANCER,
        UNMAPPED_CANCER,
        REPLACED_SOURCE,
        REPLACEMENT,
        PATIENT_KEY,
        SECRET,
        *GENERALISED.values(),
        *(value for value in rendered if not value.isdigit()),
    ]
    for name in ("audit", "attestation"):
        serialized = json.dumps(body[name])
        for value in sentinels:
            assert value not in serialized, (name, treatment)
    for span in body["audit"]["spans"]:
        if span["evidence"]["apply_action"] in {"replace", "replacement", "generalise"}:
            assert _SURROGATE_HASH.fullmatch(span["surrogate"])
        else:
            assert span["surrogate"] is None
        assert span["context"] == {}


@pytest.mark.parametrize("treatment", ["mark", "replace", "generalise", "keep"])
def test_every_audit_action_is_in_the_vocabulary(client, treatment) -> None:
    body = _apply(client, _doc(), treatment=treatment, **_keyed()).json()
    expected = {
        "mark": "mask",
        "shift": "shift",
        "keep": "keep",
        "replace": "replace",
        "replacement": "replace",
        "generalise": "format_preserve",
    }
    for span in body["audit"]["spans"]:
        assert span["action"] in ACTION_VALUES
        assert span["action"] == expected[span["evidence"]["apply_action"]]
    risk = body["audit"]["residual_risk"]
    actions = [s["action"] for s in body["spans"]]
    for action in ("replace", "replacement", "generalise"):
        assert risk[f"{action}_span_count"] == actions.count(action)


def test_attestation_flags_generalised_address(client) -> None:
    body = _apply(client, _doc(), treatment="generalise", **_keyed()).json()
    geographic = next(
        c
        for c in body["attestation"]["categories"]
        if c["category"] == "GEOGRAPHIC_SUBDIVISION"
    )
    assert geographic["applied_action_counts"].get("format_preserve") == 1
    assert geographic["residual_risk"] is True
    assert "Generalised geography" in geographic["residual_risk_reason"]


def test_attestation_flags_kept_quasi_identifier(client) -> None:
    body = _apply(client, _doc(), treatment="keep", **_keyed()).json()
    geographic = next(
        c
        for c in body["attestation"]["categories"]
        if c["category"] == "GEOGRAPHIC_SUBDIVISION"
    )
    # The address is tagged; the replacement-marked location is kept.
    assert geographic["applied_action_counts"] == {"mask": 1, "keep": 1}
    assert geographic["residual_risk"] is True
    assert "keep action" in geographic["residual_risk_reason"]


def test_attestation_unflagged_when_address_tagged(client) -> None:
    text = f"Lives {ADDRESS}."
    document = [_seg("s1", text, [_span("a1", text, ADDRESS, "STREET_ADDRESS")])]
    body = _apply(client, document, treatment="mark").json()
    geographic = next(
        c
        for c in body["attestation"]["categories"]
        if c["category"] == "GEOGRAPHIC_SUBDIVISION"
    )
    assert geographic["residual_risk"] is False
