"""Tests for ``POST /v1/selftest`` on the lean service.

The probe is the synthetic consumer probe (published checksum-valid Medicare
and ABN values, fictional name and phone). The model is always stubbed: no
network and no model download. The expected shifted date is computed here with
``datetime`` and day-first parsing, independently of the implementation.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from openmed.core import apply_spans as apply_spans_module
from openmed.core import pii_i18n
from openmed.processing.outputs import EntityPrediction, PredictionResult
from openmed.service.lean import selftest as selftest_module
from openmed.service.lean.app import create_app, get_model_detector
from openmed.service.lean.schemas import schema_names

PROBE = (
    "Patient Jane Citizen, Medicare 2123 45670 1, ABN 51 824 753 556, "
    "ph 0412 345 678, seen 04/07/2023, diagnosed 2019."
)
CHECKS = ("medicare", "abn", "phone", "date_shift", "person_tag", "year_tag")
PROBE_VALUES = (
    "Jane",
    "Citizen",
    "2123 45670 1",
    "51 824 753 556",
    "0412 345 678",
    "04/07/2023",
    "2019",
)


def _expected_shifted(offset_days: int) -> str:
    original = datetime.strptime("04/07/2023", "%d/%m/%Y")
    return (original + timedelta(days=offset_days)).strftime("%d/%m/%Y")


def _entity(text, value, label):
    start = text.find(value)
    return EntityPrediction(
        text=value, label=label, confidence=0.97, start=start, end=start + len(value)
    )


def _make_detector(*, person=True, year=True, first_name_only=False):
    def detector(text, **kwargs):
        entities = []
        if first_name_only:
            entities.append(_entity(text, "Jane", "NAME"))
        elif person:
            entities.append(_entity(text, "Jane Citizen", "NAME"))
        if year:
            entities.append(_entity(text, "2019", "DATE"))
        return PredictionResult(
            text=text,
            entities=entities,
            model_name="stub",
            timestamp="2026-01-01T00:00:00",
        )

    return detector


def _client(detector=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: (
        detector if detector is not None else _make_detector()
    )
    return TestClient(app, raise_server_exceptions=False)


def _run(client: TestClient) -> dict[str, str]:
    response = client.post("/v1/selftest")
    assert response.status_code == 200
    return response.json()["checks"]


def _failed(checks: dict[str, str]) -> set[str]:
    return {name for name, value in checks.items() if value == "fail"}


# ---------------------------------------------------------------------------
# Probe and happy path
# ---------------------------------------------------------------------------


def test_probe_text_is_verbatim_and_offset_fixed() -> None:
    assert selftest_module.SELFTEST_TEXT == PROBE
    assert isinstance(selftest_module.SELFTEST_OFFSET_DAYS, int)
    assert selftest_module.SELFTEST_OFFSET_DAYS != 0


def test_all_six_checks_pass_with_stubbed_model() -> None:
    body = _client().post("/v1/selftest").json()

    assert set(body) == {"checks"}
    assert body["checks"] == {name: "pass" for name in CHECKS}


def test_accepts_empty_object_body_and_rejects_extra_fields() -> None:
    client = _client()

    assert client.post("/v1/selftest", json={}).status_code == 200
    extra = client.post("/v1/selftest", json={"text": "x"})
    assert extra.status_code == 400
    assert extra.json() == {"error_code": "INVALID_REQUEST"}


def test_rendered_output_matches_independent_expectation() -> None:
    """The pure check function sees the exact independently computed date."""
    offset = selftest_module.SELFTEST_OFFSET_DAYS
    assert selftest_module.expected_shifted_date(offset) == _expected_shifted(offset)


# ---------------------------------------------------------------------------
# Locale trap and replace trap
# ---------------------------------------------------------------------------


def test_disabling_en_au_locale_fails_medicare(monkeypatch) -> None:
    without_au = [
        pattern
        for pattern in pii_i18n.LOCALE_PII_PATTERNS["en_au"]
        if not str(getattr(pattern, "subtype", "") or "").startswith("AU_")
    ]
    assert len(without_au) < len(pii_i18n.LOCALE_PII_PATTERNS["en_au"])
    monkeypatch.setitem(pii_i18n.LOCALE_PII_PATTERNS, "en_au", without_au)

    checks = _run(_client())

    assert checks["medicare"] == "fail"
    assert checks["abn"] == "fail"
    assert checks["phone"] == "fail"


def test_non_au_implied_locale_fails_medicare(monkeypatch) -> None:
    """A policy that no longer implies en_AU is the other form of the trap."""
    from openmed.core import policy as policy_module

    real_load = policy_module.load_policy

    def load_without_locale(name, *args, **kwargs):
        profile = real_load(name, *args, **kwargs)
        return dataclasses.replace(profile, implied_locale="en_US")

    monkeypatch.setattr("openmed.service.lean.detect.load_policy", load_without_locale)

    assert _run(_client())["medicare"] == "fail"


def test_forcing_non_shift_action_on_dates_fails_date_shift(monkeypatch) -> None:
    """``replace`` is unsupported in mt.1, so model its effect: the policy
    action for dates is no longer ``shift``, so the date is tagged (original
    gone, shifted date absent), exactly what a replace-style mask produces.

    This patches the private ``_render_action`` as a stand-in; switch to a real
    ``replace`` treatment once ticket 09 lands.
    """
    real = apply_spans_module._render_action

    def never_shift(cluster):
        action = real(cluster)
        return "mark" if action == "shift" else action

    monkeypatch.setattr(apply_spans_module, "_render_action", never_shift)

    checks = _run(_client())

    # The full date is tagged first (``[DATE_1]``), so the year becomes
    # ``[DATE_2]`` and year_tag fails too: the year is no longer ``[DATE_1]``.
    assert _failed(checks) == {"date_shift", "year_tag"}


# ---------------------------------------------------------------------------
# One failure test per remaining check, through the HTTP path
# ---------------------------------------------------------------------------


def test_abn_fails_when_abn_pattern_missing(monkeypatch) -> None:
    kept = [
        p
        for p in pii_i18n.LOCALE_PII_PATTERNS["en_au"]
        if getattr(p, "subtype", None) != "AU_ABN"
    ]
    monkeypatch.setitem(pii_i18n.LOCALE_PII_PATTERNS, "en_au", kept)

    assert _failed(_run(_client())) == {"abn"}


def test_phone_fails_when_phone_pattern_missing(monkeypatch) -> None:
    kept = [
        p
        for p in pii_i18n.LOCALE_PII_PATTERNS["en_au"]
        if not str(getattr(p, "subtype", "") or "").startswith("AU_PHONE")
    ]
    monkeypatch.setitem(pii_i18n.LOCALE_PII_PATTERNS, "en_au", kept)

    assert _failed(_run(_client())) == {"phone"}


def test_person_tag_fails_when_only_first_name_tagged() -> None:
    checks = _run(_client(_make_detector(first_name_only=True)))

    assert _failed(checks) == {"person_tag"}


def test_person_tag_fails_when_model_misses_the_name() -> None:
    checks = _run(_client(_make_detector(person=False)))

    assert _failed(checks) == {"person_tag"}


def test_year_tag_fails_when_year_not_detected() -> None:
    checks = _run(_client(_make_detector(year=False)))

    assert _failed(checks) == {"year_tag"}


# ---------------------------------------------------------------------------
# Pure check function
# ---------------------------------------------------------------------------


def _good_findings():
    def f(label, subtype=None):
        return {"label": label, "subtype": subtype}

    return [
        f("PERSON"),
        f("ID_NUM", "AU_MEDICARE"),
        f("ID_NUM", "AU_ABN"),
        f("PHONE", "AU_PHONE"),
        f("DATE"),
        f("DATE"),
    ]


_GOOD_OUTPUT = "Patient [PERSON_1], seen 14/07/2023, diagnosed [DATE_1]."


def test_evaluate_checks_all_pass() -> None:
    checks = selftest_module.evaluate_checks(
        _good_findings(), _GOOD_OUTPUT, shifted_date="14/07/2023"
    )

    assert checks == {name: "pass" for name in CHECKS}


@pytest.mark.parametrize(
    ("name", "findings", "output"),
    [
        (
            "medicare",
            [f for f in _good_findings() if f["subtype"] != "AU_MEDICARE"],
            _GOOD_OUTPUT,
        ),
        (
            "abn",
            [f for f in _good_findings() if f["subtype"] != "AU_ABN"],
            _GOOD_OUTPUT,
        ),
        (
            "phone",
            [f for f in _good_findings() if f["subtype"] != "AU_PHONE"],
            _GOOD_OUTPUT,
        ),
        (
            "date_shift",
            _good_findings(),
            "Patient [PERSON_1], seen [DATE_2], diagnosed [DATE_1].",
        ),
        (
            "date_shift",
            _good_findings(),
            "Patient [PERSON_1], seen 04/07/2023 14/07/2023, diagnosed [DATE_1].",
        ),
        (
            "person_tag",
            _good_findings(),
            "Patient Jane, seen 14/07/2023, diagnosed [DATE_1].",
        ),
        (
            "person_tag",
            _good_findings(),
            "Patient [PERSON_1] Citizen, seen 14/07/2023, diagnosed [DATE_1].",
        ),
        (
            "year_tag",
            _good_findings(),
            "Patient [PERSON_1], seen 14/07/2023, diagnosed 2019.",
        ),
        (
            "year_tag",
            _good_findings(),
            "Patient [PERSON_1], seen 14/07/2023 [DATE_1], diagnosed 2019.",
        ),
        (
            "year_tag",
            _good_findings(),
            "Patient [PERSON_1], seen [DATE_1] 14/07/2023, diagnosed [ID_NUM_1].",
        ),
        (
            "medicare",
            _good_findings(),
            "Patient [PERSON_1], 2123 45670 1 seen 14/07/2023, diagnosed [DATE_1].",
        ),
        (
            "abn",
            _good_findings(),
            "Patient [PERSON_1], 51 824 753 556 seen 14/07/2023, diagnosed [DATE_1].",
        ),
        (
            "phone",
            _good_findings(),
            "Patient [PERSON_1], 0412 345 678 seen 14/07/2023, diagnosed [DATE_1].",
        ),
    ],
)
def test_evaluate_checks_each_failure_mode(name, findings, output) -> None:
    checks = selftest_module.evaluate_checks(
        findings, output, shifted_date="14/07/2023"
    )

    assert checks[name] == "fail"
    assert _failed(checks) == {name}


# ---------------------------------------------------------------------------
# Errors, privacy, schema
# ---------------------------------------------------------------------------


def test_unexpected_detect_failure_returns_safe_error() -> None:
    def boom(text, **kwargs):
        raise RuntimeError(f"model failure on {text}")

    response = _client(boom).post("/v1/selftest")

    assert response.status_code == 500
    assert response.json() == {"error_code": "DETECT_FAILED"}


def test_unexpected_apply_failure_returns_safe_error(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError(f"apply failure on {PROBE}")

    monkeypatch.setattr("openmed.service.lean.apply.apply_spans", boom)

    response = _client().post("/v1/selftest")

    assert response.status_code == 500
    assert response.json() == {"error_code": "APPLY_FAILED"}


@pytest.mark.parametrize("stage", ["detect", "apply"])
def test_client_class_faults_are_reported_as_server_errors(
    monkeypatch, caplog, stage
) -> None:
    from openmed.service.lean.errors import LeanError

    def misconfigured(*args, **kwargs):
        raise LeanError("UNKNOWN_POLICY")

    if stage == "detect":
        monkeypatch.setattr(selftest_module, "detect_segments", misconfigured)
        expected = "DETECT_FAILED"
    else:
        monkeypatch.setattr(selftest_module, "apply_document", misconfigured)
        expected = "APPLY_FAILED"

    with caplog.at_level(logging.DEBUG):
        response = _client().post("/v1/selftest")

    assert response.status_code == 500
    assert response.json() == {"error_code": expected}
    assert PROBE not in caplog.text


def test_inverted_finding_offsets_are_apply_failed(monkeypatch) -> None:
    bad = [{"label": "PERSON", "start": 9, "end": 2}]
    monkeypatch.setattr(selftest_module, "detect_segments", lambda *a, **k: bad)

    response = _client().post("/v1/selftest")

    assert response.status_code == 500
    assert response.json() == {"error_code": "APPLY_FAILED"}


def _log_blob(caplog) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(str(record.args))
        parts.append(record.exc_text or "")
        parts.append(str(record.exc_info))
    return "\n".join(parts)


def test_response_and_logs_contain_no_probe_values(caplog) -> None:
    def boom(text, **kwargs):
        raise RuntimeError(f"model failure on {text}")

    with caplog.at_level(logging.DEBUG):
        ok = _client().post("/v1/selftest")
        failing = _client(boom).post("/v1/selftest")

    assert ok.status_code == 200
    serialized = json.dumps(ok.json()) + ok.text + failing.text
    blob = _log_blob(caplog)
    for value in PROBE_VALUES:
        assert value not in serialized
        assert value not in blob
    assert _expected_shifted(selftest_module.SELFTEST_OFFSET_DAYS) not in blob
    assert _expected_shifted(selftest_module.SELFTEST_OFFSET_DAYS) not in ok.text


def test_selftest_response_schema_published() -> None:
    assert "selftest_response" in set(schema_names())
    schema = _client().get("/v1/schemas/selftest_response").json()
    assert set(schema["properties"]) == {"checks"}
