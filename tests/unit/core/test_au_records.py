"""Tests for labelled Australian hospital record numbers (``AU_UR``).

Every value here is synthetic. The checksum-valid nine-digit value used to
check that a labelled lab number beats the unlabelled TFN/ACN patterns is
computed with the TFN validator, not copied from a real record.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from openmed.core.anonymizer.providers.clinical_ids import validate_australian_tfn
from openmed.core.au_identifiers import validate_australian_acn
from openmed.core.au_records import (
    AU_RECORD_CONTEXT_WORDS,
    AU_RECORD_PII_PATTERNS,
    AU_UR,
)
from openmed.core.pii import extract_pii
from openmed.core.pii_i18n import (
    LANGUAGE_PII_PATTERNS,
    LOCALE_PII_PATTERNS,
    get_patterns_for_language,
)
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult

STORY_14_LABELS = (
    "UR",
    "URN",
    "UR No",
    "U.R.",
    "MRN",
    "Hospital No",
    "Patient ID",
    "Episode",
    "Visit No",
    "Accession",
    "Lab No",
    "Specimen No",
)
VALUES = ("A123456", "1234567", "123-456", "0012345")


def _first_valid(predicate) -> str:
    for candidate in range(123_456_000, 123_457_000):
        digits = str(candidate)
        if predicate(digits):
            return digits
    raise AssertionError("no synthetic value found")


# A nine-digit value that the unlabelled TFN pattern would otherwise claim.
TFN_VALID = _first_valid(validate_australian_tfn)
# A nine-digit value that is checksum-valid as both a TFN and an ACN.
TFN_ACN_VALID = next(
    str(n)
    for n in range(100_000_000, 100_100_000)
    if validate_australian_tfn(str(n)) and validate_australian_acn(str(n))
)
# A nine-digit value that is checksum-valid as an ACN but not as a TFN.
ACN_ONLY_VALID = _first_valid(
    lambda digits: (
        validate_australian_acn(digits) and not validate_australian_tfn(digits)
    )
)


def _spaced(digits: str) -> str:
    return f"{digits[:3]} {digits[3:6]} {digits[6:]}"


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


def _single_ur_span(result, text: str, value: str):
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
    assert span.canonical_label == "ID_NUM"
    assert span.metadata["subtype"] == AU_UR
    assert value not in result.redacted_text
    return span


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_context_words_cover_story_14_labels():
    words = {word.casefold() for word in AU_RECORD_CONTEXT_WORDS}
    assert {label.casefold() for label in STORY_14_LABELS} <= words


def test_record_patterns_registered_for_en_au_only():
    au_patterns = LOCALE_PII_PATTERNS["en_au"]
    assert all(pattern in au_patterns for pattern in AU_RECORD_PII_PATTERNS)
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale=locale)
        assert not any(pattern in patterns for pattern in AU_RECORD_PII_PATTERNS)


def test_record_patterns_keep_labels_outside_the_value_span():
    for pattern in AU_RECORD_PII_PATTERNS:
        compiled = re.compile(pattern.pattern, pattern.flags)
        assert "value" in compiled.groupindex, pattern.pattern
        assert pattern.entity_type == "medical_record_number"
        assert pattern.subtype == AU_UR
        assert pattern.priority == 13
        assert pattern.base_score == 0.9


# ---------------------------------------------------------------------------
# Pipeline (ccia_l3 implies en_AU)
# ---------------------------------------------------------------------------


# ``Episode`` and ``Visit No`` need an explicit separator or ``No`` (see the
# prose table below); a bare ``.`` is never a separator.
_PROSE_LABELS = {"Episode"}


@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("label", STORY_14_LABELS)
@pytest.mark.parametrize("separator", [": ", ":", " #", " ", " : "])
def test_labelled_record_number_is_au_ur(label, value, separator):
    if label in _PROSE_LABELS and separator == " ":
        pytest.skip("Episode needs ':', '#' or 'No' before the value")
    text = f"Ward 4B. {label}{separator}{value} admitted."
    _single_ur_span(_pipeline(text), text, value)


@pytest.mark.parametrize(
    "label",
    [
        "UR No.",
        "UR No.:",
        "Hospital No.",
        "Lab No.:",
        "Specimen No.",
        "Visit No.",
        "Visit:",
        "Visit #",
        "Visit Number",
        "Episode No",
        "Episode No.:",
        "Episode Number:",
        "Episode #",
        "U.R.:",
        "Accession No:",
        "Accession No.",
        "Accession No.:",
        "Accession No",
        "Accession Number:",
        "Accession number",
        "ACCESSION NO:",
        "Accession #",
        "Hospital Number:",
        "Lab Number:",
        "Specimen Number",
        "Specimen No:",
        "Patient ID No:",
        "Patient ID Number",
    ],
)
def test_abbreviated_and_prose_labels_with_explicit_separator(label):
    text = f"{label} 0012345 on file."
    _single_ur_span(_pipeline(text), text, "0012345")


@pytest.mark.parametrize(
    "label", ["UR no", "LAB NO", "patient id", "hospital no:", "accession", "lab no."]
)
def test_multi_word_labels_are_case_insensitive(label):
    text = f"{label} 0012345 on file."
    _single_ur_span(_pipeline(text), text, "0012345")


@pytest.mark.parametrize("label", ["urn", "Urn", "mrn", "u.r.", "ur", "ur no"])
def test_short_labels_are_case_sensitive(label):
    result = _pipeline(f"{label}: 0012345 on file.")
    assert not [span for span in result.spans if _subtype(span) == AU_UR]


@pytest.mark.parametrize(
    "text",
    [
        "Third episode. 2019 was worse.",
        "Prior episode 2019 treated.",
        "Episode 2023-24 review.",
        "ur 2019 results",
        "seen ur 2019",
        "Episode 2",
        "Visit No 3",
        "Episode 12345 noted.",
        "Visit 2019 planned.",
        "UR. 2019 was worse.",
        "Accession. 12345678 was late.",
        "Accession numbers 12345678 pending.",
        "Lab Nos 12345678 pending.",
    ],
)
def test_prose_years_and_counts_are_not_ur(text):
    result = _pipeline(text)
    assert not [span for span in result.spans if _subtype(span) == AU_UR], [
        (s.start, s.end, s.canonical_label) for s in result.spans
    ]


@pytest.mark.parametrize("label", ["Lab No:", "Accession", "Specimen No"])
@pytest.mark.parametrize("form", ["plain", "spaced"])
def test_labelled_lab_number_is_ur_not_tfn(label, form):
    assert validate_australian_tfn(TFN_VALID)
    value = TFN_VALID if form == "plain" else _spaced(TFN_VALID)
    text = f"{label} {value} received."
    span = _single_ur_span(_pipeline(text), text, value)
    assert span.metadata["subtype"] not in {"AU_TFN", "AU_ACN"}


@pytest.mark.parametrize("form", ["plain", "spaced"])
def test_labelled_lab_number_valid_as_tfn_and_acn_is_ur(form):
    assert validate_australian_tfn(TFN_ACN_VALID)
    assert validate_australian_acn(TFN_ACN_VALID)
    value = TFN_ACN_VALID if form == "plain" else _spaced(TFN_ACN_VALID)
    text = f"Accession {value} received."
    _single_ur_span(_pipeline(text), text, value)


@pytest.mark.parametrize("label", ["Lab No:", "Accession", "URN"])
@pytest.mark.parametrize("form", ["plain", "spaced"])
def test_labelled_acn_only_value_is_ur_not_acn(label, form):
    assert validate_australian_acn(ACN_ONLY_VALID)
    assert not validate_australian_tfn(ACN_ONLY_VALID)
    value = ACN_ONLY_VALID if form == "plain" else _spaced(ACN_ONLY_VALID)
    text = f"{label} {value} received."
    _single_ur_span(_pipeline(text), text, value)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("UR 123 456 7890 noted.", "123 456 7890"),
        ("Lab No: 123 456 789 noted.", "123 456 789"),
    ],
)
def test_spaced_labelled_values_are_ur(text, value):
    _single_ur_span(_pipeline(text), text, value)


@pytest.mark.parametrize(
    "text",
    [
        "Reading 0012345 noted.",
        "Code A123456 noted.",
        "Range 123-456 noted.",
        "Your 1234567 noted.",
        "Four 1234 noted.",
    ],
)
def test_bare_number_without_label_is_not_ur(text):
    result = _pipeline(text)
    assert not [span for span in result.spans if _subtype(span) == AU_UR]


@pytest.mark.usefixtures("no_model")
def test_extract_pii_en_au_emits_au_ur():
    text = "URN: 0012345 admitted."
    entities = extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale="en_AU"
    ).entities
    findings = [entity for entity in entities if _subtype(entity) == AU_UR]
    assert len(findings) == 1
    assert text[findings[0].start : findings[0].end] == "0012345"


def test_safety_sweep_en_au_emits_au_ur():
    text = "Lab No: 0012345 received."
    findings = [
        entity
        for entity in safety_sweep(text, [], lang="en", locale="en_AU")
        if _subtype(entity) == AU_UR
    ]
    assert [text[e.start : e.end] for e in findings] == ["0012345"]


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------

_NON_AU_TEXT = (
    "UR No: A123456. Lab No: 1234567. Accession 123-456. MRN 0012345. "
    "Patient ID 7654321."
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
        entities = extract_pii(
            _NON_AU_TEXT, model_name="fixture-pii-model", lang="en", locale=locale
        ).entities
        swept = safety_sweep(_NON_AU_TEXT, [], lang="en", locale=locale)
        result = _pipeline(_NON_AU_TEXT, policy=policy, locale=locale)
        assert not [span for span in result.spans if _subtype(span) == AU_UR]
        return (
            _snapshot(entities),
            _snapshot(swept),
            result.redacted_text,
            _span_snapshot(result.spans),
        )

    with_hook = outputs()

    # The same run with the AU record patterns removed from every pattern
    # table must be byte-identical (apart from per-run salted hashes).
    for table in (LOCALE_PII_PATTERNS, LANGUAGE_PII_PATTERNS):
        for key, patterns in list(table.items()):
            kept = [p for p in patterns if p not in AU_RECORD_PII_PATTERNS]
            monkeypatch.setitem(table, key, kept)
    assert outputs() == with_hook


# ---------------------------------------------------------------------------
# Table header vocabulary (ticket 16)
# ---------------------------------------------------------------------------


def test_table_header_vocabulary_covers_au_labels():
    from openmed.core.au_records import (
        AU_HEADER_CONTEXT_WORDS,
        AU_TABLE_HEADER_LABELS,
        AU_TABLE_HEADER_SUBTYPES,
        table_header_key,
    )

    for word in AU_RECORD_CONTEXT_WORDS:
        assert AU_TABLE_HEADER_LABELS[word] == "ID_NUM", word
        assert AU_TABLE_HEADER_SUBTYPES[table_header_key(word)] == AU_UR, word
    for word in AU_HEADER_CONTEXT_WORDS:
        assert word in AU_TABLE_HEADER_LABELS, word
    for word in ("D.O.B", "DOB", "Date of Birth"):
        assert AU_TABLE_HEADER_LABELS[word] == "DATE_OF_BIRTH"
    assert AU_TABLE_HEADER_LABELS["Surname"] == "LAST_NAME"
    assert AU_TABLE_HEADER_LABELS["Given Name(s)"] == "FIRST_NAME"
    assert AU_TABLE_HEADER_LABELS["NOK"] == "PERSON"
    assert AU_TABLE_HEADER_LABELS["Address"] == "STREET_ADDRESS"
    assert AU_TABLE_HEADER_LABELS["Ph"] == "PHONE"
    assert AU_TABLE_HEADER_LABELS["Email"] == "EMAIL"
    assert AU_TABLE_HEADER_LABELS["Medicare No"] == "MEDICARE_NUMBER"
    # Only record-number columns carry a subtype.
    assert set(AU_TABLE_HEADER_SUBTYPES.values()) == {AU_UR}
    for key in ("name", "dob", "ph", "medicareno"):
        assert key not in AU_TABLE_HEADER_SUBTYPES


def test_table_header_key_matches_tabular_normalisation():
    from openmed.core.au_records import AU_TABLE_HEADER_LABELS, table_header_key
    from openmed.multimodal.tabular_csv import _header_key

    assert table_header_key("U.R.") == "ur"
    assert table_header_key("Given Name(s)") == "givennames"
    for header in AU_TABLE_HEADER_LABELS:
        assert table_header_key(header) == _header_key(header)
