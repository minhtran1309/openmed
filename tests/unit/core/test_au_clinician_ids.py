"""Tests for AHPRA, DVA and Medicare provider number recognisers.

Every value here is synthetic. ``2447781L`` is the widely circulated sample
provider number and ``123456AF`` is constructed in ticket 05 (stem 123456,
PLC ``A``); both are checked by the mod-11 algorithm in the ticket.
"""

from __future__ import annotations

import dataclasses
import random
import string

import pytest

from openmed.core.au_clinician_ids import (
    AU_AHPRA,
    AU_CLINICIAN_ID_PII_PATTERNS,
    AU_DVA,
    AU_PROVIDER,
    validate_medicare_provider_number,
)
from openmed.core.pii import extract_pii
from openmed.core.pii_i18n import (
    LANGUAGE_PII_PATTERNS,
    LOCALE_PII_PATTERNS,
    get_patterns_for_language,
)
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult

_SUBTYPES = {AU_AHPRA, AU_DVA, AU_PROVIDER}
_LABELS = {
    AU_AHPRA: "AHPRA_NUMBER",
    AU_DVA: "DVA_NUMBER",
    AU_PROVIDER: "MEDICARE_PROVIDER_NUMBER",
}


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


def _subtype(entity) -> str | None:
    return (entity.metadata or {}).get("subtype")


def _tagged(result) -> list:
    return [span for span in result.spans if _subtype(span) in _SUBTYPES]


def _assert_single(result, text: str, value: str, subtype: str):
    start = text.index(value)
    covering = [
        span
        for span in result.spans
        if span.start < start + len(value) and span.end > start
    ]
    assert len(covering) == 1, [
        (s.start, s.end, s.canonical_label, s.metadata) for s in result.spans
    ]
    span = covering[0]
    assert text[span.start : span.end] == value
    assert span.canonical_label == _LABELS[subtype]
    assert span.metadata["subtype"] == subtype
    assert value not in result.redacted_text


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["2447781L", "123456AF", "244778 1L"])
def test_valid_provider_numbers(value):
    assert validate_medicare_provider_number(value)


@pytest.mark.parametrize(
    "value",
    ["123456AY", "2447781Y", "2447782L", "000000AF", "1234567", "12345AF", ""],
)
def test_invalid_provider_numbers(value):
    assert not validate_medicare_provider_number(value)


@pytest.mark.parametrize("plc", ["I", "O", "S", "Z"])
def test_excluded_practice_location_characters_are_invalid(plc):
    for check in "YXWTLKJHFBA":
        assert not validate_medicare_provider_number(f"123456{plc}{check}")


def test_every_valid_plc_has_exactly_one_check_character():
    plcs = "0123456789ABCDEFGHJKLMNPQRTUVWXY"
    for plc in plcs:
        valid = [
            check
            for check in "YXWTLKJHFBA"
            if validate_medicare_provider_number(f"123456{plc}{check}")
        ]
        assert len(valid) == 1, plc


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_patterns_registered_for_en_au_only():
    assert all(p in LOCALE_PII_PATTERNS["en_au"] for p in AU_CLINICIAN_ID_PII_PATTERNS)
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale=locale)
        assert not any(p in patterns for p in AU_CLINICIAN_ID_PII_PATTERNS)


def test_patterns_are_value_only_and_outrank_record_numbers():
    import re

    for pattern in AU_CLINICIAN_ID_PII_PATTERNS:
        assert "value" in re.compile(pattern.pattern, pattern.flags).groupindex
        assert pattern.priority >= 14
        assert pattern.subtype in _SUBTYPES


# ---------------------------------------------------------------------------
# AHPRA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "AHPRA reg MED0001234567 verified.",
        "AHPRA: MED0001234567 verified.",
        "AHPRA registration number: MED0001234567.",
        "Registration No. MED0001234567 on file.",
        "Reg no MED0001234567 on file.",
        "Practitioner number NMW0009876543 on file.",
        "AHPRA no PHY0001234567 on file.",
        "AHPRA reg DEN0001234567 verified.",
        "AHPRA reg ZZZ0001234567 verified.",
    ],
)
def test_labelled_ahpra_is_tagged(template):
    value = next(w for w in template.replace(".", " ").split() if w[3:].isdigit())
    value = value.strip(":.")
    _assert_single(_pipeline(template), template, value, AU_AHPRA)


@pytest.mark.parametrize(
    "text",
    [
        "Number MED0001234567 on file.",
        "MED0001234567",
        "Seen by the practitioner MED0001234567 today.",
        "AHPRA reg MED000123456 verified.",
        "AHPRA reg MED00012345678 verified.",
        "AHPRA reg med0001234567 verified.",
        "AHPRA reg XMED0001234567 verified.",
    ],
)
def test_unlabelled_or_malformed_ahpra_is_not_tagged(text):
    assert not _tagged(_pipeline(text))


# ---------------------------------------------------------------------------
# DVA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "DVA file no QAB12345 noted.",
        "DVA file no: VXY54321B noted.",
        "DVA number NBUR9080 noted.",
        "DVA: NBUR9080A noted.",
        "Gold Card NSM12345 noted.",
        "White Card: V123456 noted.",
        "Veteran file number QSS12345 noted.",
        "DVA file no WA123456 noted.",
        "DVA file no N 123456 noted.",
    ],
)
def test_labelled_dva_is_tagged(template):
    value = template.split("noted")[0].split(" ", 2)[-1].strip()
    for label in ("file no: ", "file no ", "number ", ": ", "Card: ", "Card "):
        if label in template:
            value = template.split(label, 1)[1].split(" noted")[0]
            break
    _assert_single(_pipeline(template), template, value, AU_DVA)


@pytest.mark.parametrize(
    "text",
    [
        "DVA file no QAB1234 noted.",
        "DVA file no QAB12345678 noted.",
        "DVA file no XAB12345 noted.",
        "DVA file no qab12345 noted.",
        "QAB12345 noted.",
        "File number VXY54321B noted.",
        "Gold Card holder QAB12345 noted.",
        "DVA file no WSR111 noted.",
    ],
)
def test_unlabelled_or_malformed_dva_is_not_tagged(text):
    assert not _tagged(_pipeline(text))


# ---------------------------------------------------------------------------
# Medicare provider number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["2447781L", "123456AF", "244778 1L"])
@pytest.mark.parametrize(
    "label",
    [
        "Provider number",
        "Provider no:",
        "Medicare provider number:",
        "Medicare provider",
        "Prescriber no",
        "Prescriber",
        "Referrer number",
    ],
)
def test_labelled_valid_provider_number_is_tagged(label, value):
    text = f"{label} {value} on script."
    _assert_single(_pipeline(text), text, value, AU_PROVIDER)


@pytest.mark.parametrize(
    "text",
    [
        "Provider number 123456AY on script.",
        "Provider number 123456IF on script.",
        "Provider number 123456OF on script.",
        "Provider number 123456SF on script.",
        "Provider number 000000AF on script.",
        "Script 2447781L on file.",
        "2447781L",
        "Prescribed by a provider, 2447781L.",
    ],
)
def test_unlabelled_or_invalid_provider_number_is_not_tagged(text):
    assert not _tagged(_pipeline(text))


# ---------------------------------------------------------------------------
# Random-string guard
# ---------------------------------------------------------------------------


def test_random_alphanumerics_without_context_are_never_tagged():
    rng = random.Random(7)
    alphabet = string.ascii_uppercase + string.digits
    for length in (8, 13):
        for _ in range(300):
            value = "".join(rng.choice(alphabet) for _ in range(length))
            text = f"Ref {value} noted."
            assert not _tagged(_pipeline(text)), value


def test_shaped_random_values_without_context_are_never_tagged():
    rng = random.Random(11)
    for _ in range(200):
        values = [
            "".join(rng.choice(string.ascii_uppercase) for _ in range(3))
            + "".join(rng.choice(string.digits) for _ in range(10)),
            rng.choice("NVQWST")
            + "".join(rng.choice(string.ascii_uppercase) for _ in range(2))
            + "".join(rng.choice(string.digits) for _ in range(5)),
            "".join(rng.choice(string.digits) for _ in range(7))
            + rng.choice("YXWTLKJHFBA"),
        ]
        for value in values:
            assert not _tagged(_pipeline(f"Ref {value} noted.")), value


# ---------------------------------------------------------------------------
# Priority over generic matches
# ---------------------------------------------------------------------------


def test_labelled_value_beats_overlapping_generic_id_match():
    text = "UR: AB0001234567 and AHPRA reg MED0001234567 verified."
    result = _pipeline(text)
    _assert_single(result, text, "MED0001234567", AU_AHPRA)


# ---------------------------------------------------------------------------
# Other detection paths
# ---------------------------------------------------------------------------

_CASES = [
    ("AHPRA reg MED0001234567 ok.", "MED0001234567", AU_AHPRA),
    ("DVA file no QAB12345 ok.", "QAB12345", AU_DVA),
    ("Provider number 2447781L ok.", "2447781L", AU_PROVIDER),
]


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize(("text", "value", "subtype"), _CASES)
def test_extract_pii_en_au(text, value, subtype):
    entities = extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale="en_AU"
    ).entities
    found = [e for e in entities if _subtype(e) == subtype]
    assert [text[e.start : e.end] for e in found] == [value]
    assert _LABELS[subtype] == _canonical(found[0].label)


def _canonical(label: str) -> str:
    from openmed.core.labels import normalize_label

    return normalize_label(label)


@pytest.mark.parametrize(("text", "value", "subtype"), _CASES)
def test_safety_sweep_en_au(text, value, subtype):
    found = [
        e
        for e in safety_sweep(text, [], lang="en", locale="en_AU")
        if _subtype(e) == subtype
    ]
    assert [text[e.start : e.end] for e in found] == [value]


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------

_NON_AU_TEXT = (
    "AHPRA reg MED0001234567. DVA file no QAB12345. "
    "Provider number 2447781L. Prescriber 123456AF."
)
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
        entities = extract_pii(
            _NON_AU_TEXT, model_name="fixture-pii-model", lang="en", locale=locale
        ).entities
        swept = safety_sweep(_NON_AU_TEXT, [], lang="en", locale=locale)
        result = _pipeline(_NON_AU_TEXT, policy=policy, locale=locale)
        assert not _tagged(result)
        return (
            _snapshot(entities),
            _snapshot(swept),
            result.redacted_text,
            _span_snapshot(result.spans),
        )

    with_hook = outputs()
    for table in (LOCALE_PII_PATTERNS, LANGUAGE_PII_PATTERNS):
        for key, patterns in list(table.items()):
            kept = [p for p in patterns if p not in AU_CLINICIAN_ID_PII_PATTERNS]
            monkeypatch.setitem(table, key, kept)
    assert outputs() == with_hook
