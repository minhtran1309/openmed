"""Tests for day-first Australian date detection (ticket 07, story 15).

Every date here is arbitrary and synthetic. The model is always stubbed.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from openmed.core.apply_spans import apply_spans
from openmed.core.au_dates import AU_DATE_PII_PATTERNS
from openmed.core.pii import extract_pii
from openmed.core.pii_i18n import (
    LANGUAGE_PII_PATTERNS,
    LOCALE_PII_PATTERNS,
    get_patterns_for_language,
)
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult
from openmed.service.lean.app import create_app, get_model_detector

# Forms detected before this ticket stay detected; the rest are new.
DETECTED_FORMS = (
    "04/07/2023",
    "04-Jul-23",
    "4.7.23",
    "4th July 2023",
    "July 4, 2023",
    "4th of July 2023",
    "04-JUL-2023",
    "4.7.2023",
    "4-7-23",
    "4 Jul 23",
    "04.07.23",
    "4 July, 2023",
    "4TH JULY 2023",
    "1st Sept 2023",
    "22nd Feb. 2024",
    "29.2.24",
)

# Each tuple is (text, date surface that must not be tagged).
NEGATIVES = (
    ("Ratio 4.13.23 noted", "4.13.23"),
    ("Value 31.2.23 noted", "31.2.23"),
    ("Value 29.2.23 noted", "29.2.23"),
    ("Running v4.7.23 build", "4.7.23"),
    ("Running 4.7.23.1 build", "4.7.23"),
    ("Host 10.4.7.23 down", "4.7.23"),
    ("Potassium 4.7 mmol/L", "4.7"),
    ("Give 5 may 20 units", "5 may 20"),
    ("Sodium 4.7.230 trend", "4.7.23"),
)


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


def _fake_analyze_text(inference_text, **kwargs):
    return PredictionResult(
        text=inference_text,
        entities=[],
        model_name=kwargs["model_name"],
        timestamp="2026-01-01T00:00:00",
    )


@pytest.fixture
def no_model(monkeypatch):
    import openmed

    monkeypatch.setattr(openmed, "analyze_text", _fake_analyze_text)


def _pipeline(text: str, *, policy: str = "ccia_l3", locale: str | None = None):
    from openmed.core.pipeline import Pipeline

    return Pipeline(policy=policy, model_detector=_stub_detector).run(
        text, locale=locale
    )


def _extract(text: str, *, locale: str | None = "en_AU"):
    return extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale=locale
    ).entities


def _sweep(text: str, *, locale: str | None = "en_AU"):
    return safety_sweep(text, [], lang="en", locale=locale)


def _covering(items, start: int, end: int):
    return [item for item in items if item.start < end and item.end > start]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_date_patterns_registered_for_en_au_only():
    au_patterns = LOCALE_PII_PATTERNS["en_au"]
    assert all(pattern in au_patterns for pattern in AU_DATE_PII_PATTERNS)
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale)
        assert not any(pattern in patterns for pattern in AU_DATE_PII_PATTERNS)


def test_date_patterns_use_raw_date_label_without_subtype():
    assert AU_DATE_PII_PATTERNS
    for pattern in AU_DATE_PII_PATTERNS:
        assert pattern.entity_type == "date"
        assert pattern.subtype is None
        assert pattern.reject_on_validation_failure


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", DETECTED_FORMS)
def test_day_first_forms_are_one_date_span_in_pipeline(value):
    text = f"Seen {value} in clinic."
    start = text.index(value)
    result = _pipeline(text)
    covering = _covering(result.spans, start, start + len(value))
    assert [(s.start, s.end, s.canonical_label) for s in covering] == [
        (start, start + len(value), "DATE")
    ]
    assert value not in result.redacted_text


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize("value", DETECTED_FORMS)
def test_day_first_forms_in_extract_pii_and_sweep(value):
    text = f"Seen {value} in clinic."
    start = text.index(value)
    expected = [(start, start + len(value), "date")]
    entities = _covering(_extract(text), start, start + len(value))
    assert [(e.start, e.end, e.label) for e in entities] == expected
    swept = _covering(_sweep(text), start, start + len(value))
    assert [(e.start, e.end, e.label) for e in swept] == expected


@pytest.mark.parametrize(
    ("value", "shifted"),
    [
        ("04/07/2023", "14/07/2023"),
        ("04-Jul-23", "14-Jul-23"),
        ("4.7.23", "14.7.23"),
        ("4th July 2023", "14th July 2023"),
        ("4th of July 2023", "14th of July 2023"),
        ("July 4, 2023", "July 14, 2023"),
        ("04-JUL-2023", "14-JUL-2023"),
        ("4.7.2023", "14.7.2023"),
    ],
)
def test_detected_date_round_trips_through_shift(value, shifted):
    text = f"Seen {value} in clinic."
    result = _pipeline(text, policy="ccia_l1")
    document = [
        {
            "segment_id": "s",
            "text": text,
            "spans": [
                {
                    "span_id": f"d{index}",
                    "start": span.start,
                    "end": span.end,
                    "label": span.canonical_label,
                }
                for index, span in enumerate(result.spans)
            ],
        }
    ]
    applied = apply_spans(document, policy="ccia_l1", treatment="mark", offset_days=10)
    assert applied.segments[0]["text"] == f"Seen {shifted} in clinic."
    assert [span["action"] for span in applied.spans] == ["shift"]


def test_ambiguous_numeric_date_is_day_first():
    """``04/07/2023`` is 4 July: +31 days is 4 August (month-first: 07/05)."""
    text = "Seen 04/07/2023 in clinic."
    result = _pipeline(text, policy="ccia_l1")
    (span,) = result.spans
    document = [
        {
            "segment_id": "s",
            "text": text,
            "spans": [
                {
                    "span_id": "d",
                    "start": span.start,
                    "end": span.end,
                    "label": span.canonical_label,
                }
            ],
        }
    ]
    applied = apply_spans(document, policy="ccia_l1", treatment="mark", offset_days=31)
    assert applied.segments[0]["text"] == "Seen 04/08/2023 in clinic."


@pytest.mark.parametrize(
    "text",
    ["D.O.B: 04-Jul-23\n", "DOB 4.7.23\n", "Date of Birth: 4th July 1961\n"],
)
def test_header_dob_keeps_winning_over_day_first_date(text):
    value = text.split(":")[-1].strip() if ":" in text else text[4:].strip()
    start = text.index(value)
    result = _pipeline(text)
    covering = _covering(result.spans, start, start + len(value))
    assert [
        (s.start, s.end, s.canonical_label, s.metadata.get("subtype")) for s in covering
    ] == [(start, start + len(value), "DATE_OF_BIRTH", "AU_HEADER")]


def test_lab_value_shaped_like_a_date_is_tagged_fail_closed():
    """``eGFR 4.7.23`` reads as a day-first date, so it is tagged (fail-closed)."""
    text = "eGFR 4.7.23 trend"
    result = _pipeline(text)
    assert [(text[s.start : s.end], s.canonical_label) for s in result.spans] == [
        ("4.7.23", "DATE")
    ]


@pytest.mark.parametrize(("text", "surface"), NEGATIVES)
def test_invalid_dates_versions_and_decimals_are_not_dates(no_model, text, surface):
    start = text.index(surface)
    end = start + len(surface)
    result = _pipeline(text)
    assert not _covering(
        [s for s in result.spans if s.canonical_label == "DATE"], start, end
    )
    assert not _covering([e for e in _extract(text) if e.label == "date"], start, end)
    assert not _covering([e for e in _sweep(text) if e.label == "date"], start, end)


def test_detect_endpoint_returns_day_first_dates():
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: _stub_detector
    client = TestClient(app, raise_server_exceptions=False)
    text = "Collected 04-Jul-23, reviewed 4.7.23 and 4th July 2023."
    response = client.post(
        "/v1/detect",
        json={"segments": [{"segment_id": "s", "text": text}], "policy": "ccia_l3"},
    )
    assert response.status_code == 200
    found = [
        (text[f["start"] : f["end"]], f["label"]) for f in response.json()["findings"]
    ]
    assert found == [
        ("04-Jul-23", "DATE"),
        ("4.7.23", "DATE"),
        ("4th July 2023", "DATE"),
    ]


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------

_NON_AU_TEXT = (
    "Seen 04-Jul-23, 4.7.23, 4th July 2023, 4th of July 2023, 04/07/2023 and "
    "July 4, 2023. eGFR 4.7.23 trend, v4.7.23 and 4.13.23."
)
# Per-run salted HMAC fields; everything else must be byte-identical.
_SALTED_FIELDS = ("doc_id", "text_hash")
_SALTED_METADATA = ("normalized_text_hash",)


def _snapshot(items):
    return [repr(item) for item in items]


def _span_snapshot(spans):
    snapshot = []
    for span in spans:
        fields = dataclasses.asdict(span)
        for name in _SALTED_FIELDS:
            fields.pop(name)
        for name in _SALTED_METADATA:
            fields["metadata"].pop(name, None)
        snapshot.append(repr(fields))
    return snapshot


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize("policy", ["hipaa_safe_harbor", "strict_no_leak"])
@pytest.mark.parametrize("locale", [None, "en_US", "en_GB"])
def test_non_au_locale_unchanged(monkeypatch, policy, locale):
    def outputs():
        entities = _extract(_NON_AU_TEXT, locale=locale)
        swept = _sweep(_NON_AU_TEXT, locale=locale)
        result = _pipeline(_NON_AU_TEXT, policy=policy, locale=locale)
        return (
            _snapshot(entities),
            _snapshot(swept),
            result.redacted_text,
            _span_snapshot(result.spans),
        )

    with_hook = outputs()

    # The same run with the AU date patterns removed from every pattern table
    # must be byte-identical (apart from per-run salted hashes).
    for table in (LOCALE_PII_PATTERNS, LANGUAGE_PII_PATTERNS):
        for key, patterns in list(table.items()):
            kept = [p for p in patterns if p not in AU_DATE_PII_PATTERNS]
            monkeypatch.setitem(table, key, kept)
    assert outputs() == with_hook
