"""Tests for values after Australian report header labels (``AU_HEADER``).

Every name, date, address and number here is invented; the shared report
header lives in ``tests/fixtures/au_report_header.py``.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from openmed.core.au_records import (
    AU_HEADER,
    AU_HEADER_CONTEXT_WORDS,
    AU_HEADER_PII_PATTERNS,
)
from openmed.core.pii import extract_pii
from openmed.core.pii_i18n import (
    LANGUAGE_PII_PATTERNS,
    LOCALE_PII_PATTERNS,
    get_patterns_for_language,
)
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult
from tests.fixtures.au_report_header import (
    EXISTING_SUBTYPES,
    EXISTING_VALUES,
    HEADER,
    HEADER_VALUES,
)

HEADER_LABELS = (
    "Patient Name",
    "Name",
    "Surname",
    "Given Name",
    "D.O.B",
    "DOB",
    "Date of Birth",
    "Address",
    "UR",
    "URN",
    "Medicare",
    "Ph",
    "Phone",
    "Mob",
    "Mobile",
    "Fax",
    "NOK",
    "Next of Kin",
    "Referring Doctor",
    "Referred by",
    "Reported by",
    "Requesting Clinician",
    "GP",
    "Dr",
    "Prof",
)
_PERSONAL_LABELS = {
    "PERSON",
    "FIRST_NAME",
    "LAST_NAME",
    "DATE_OF_BIRTH",
    "STREET_ADDRESS",
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


def _found(result, text):
    return {(text[s.start : s.end], s.canonical_label) for s in result.spans}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


# Story 16 labels detected here; UR/URN, Medicare No and the phone labels
# have their own patterns (asserted in the fixture test below).
STORY_16_HEADER_LABELS = (
    "Patient Name",
    "Name",
    "Surname",
    "Given Name(s)",
    "D.O.B",
    "DOB",
    "Date of Birth",
    "Address",
    "NOK",
    "Next of Kin",
    "Referring Doctor",
    "Referred by",
    "Reported by",
    "Requesting Clinician",
    "GP",
)


def test_context_words_cover_story_16_labels():
    words = {word.casefold() for word in AU_HEADER_CONTEXT_WORDS}
    assert {label.casefold() for label in STORY_16_HEADER_LABELS} <= words


def test_header_patterns_registered_for_en_au_only():
    au_patterns = LOCALE_PII_PATTERNS["en_au"]
    assert all(pattern in au_patterns for pattern in AU_HEADER_PII_PATTERNS)
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale=locale)
        assert not any(pattern in patterns for pattern in AU_HEADER_PII_PATTERNS)


def test_header_patterns_keep_labels_outside_the_value_span():
    raw_labels = {"name", "first_name", "last_name", "date_of_birth", "street_address"}
    for pattern in AU_HEADER_PII_PATTERNS:
        compiled = re.compile(pattern.pattern, pattern.flags)
        assert "value" in compiled.groupindex, pattern.pattern
        assert pattern.entity_type in raw_labels
        assert pattern.subtype == AU_HEADER
        assert pattern.validator is None


# ---------------------------------------------------------------------------
# Pipeline (ccia_l3 implies en_AU)
# ---------------------------------------------------------------------------


def test_header_fixture_values_through_pipeline():
    result = _pipeline(HEADER)
    assert _found(result, HEADER) == HEADER_VALUES | EXISTING_VALUES
    for span in result.spans:
        surface = HEADER[span.start : span.end]
        assert surface == surface.strip()
        assert ":" not in surface and "\t" not in surface and "\n" not in surface
        for label in HEADER_LABELS:
            assert not re.search(rf"(?<![A-Za-z]){re.escape(label)}\b", surface), (
                surface,
                label,
            )
        if (surface, span.canonical_label) in HEADER_VALUES:
            assert span.metadata["subtype"] == AU_HEADER
        else:
            assert span.metadata["subtype"] in EXISTING_SUBTYPES
        assert surface not in result.redacted_text


def test_header_dob_stays_date_of_birth_under_mark():
    text = "D.O.B: 04/07/1961\nDOB 12-Mar-58\n"
    result = _pipeline(text)
    assert _found(result, text) == {
        ("04/07/1961", "DATE_OF_BIRTH"),
        ("12-Mar-58", "DATE_OF_BIRTH"),
    }
    assert "1961" not in result.redacted_text


def test_header_address_is_one_span_to_line_end():
    text = "Address: Unit 3, 12 Smith Street, Randwick NSW 2031\nSex: F\n"
    value = "Unit 3, 12 Smith Street, Randwick NSW 2031"
    result = _pipeline(text)
    start = text.index(value)
    covering = [s for s in result.spans if s.start < start + len(value)]
    assert [(s.start, s.end) for s in covering] == [(start, start + len(value))]
    assert covering[0].canonical_label == "STREET_ADDRESS"
    assert covering[0].metadata["subtype"] == AU_HEADER


@pytest.mark.parametrize(
    ("text", "value"),
    [
        # A street type the AU street pattern does not know: the state and
        # postcode patterns must not split the header value.
        ("Address: 4 Wattle Close, Kiama NSW 2533", "4 Wattle Close, Kiama NSW 2533"),
        # The Pipeline joins lines; the value ends at the state and postcode.
        (
            "Address: 12 Smith St, Randwick NSW 2031\nPatient presented with pain.",
            "12 Smith St, Randwick NSW 2031",
        ),
        ("Address: 3/12 Smith St. Next of kin notified.", "3/12 Smith St"),
        ("ADDRESS: PO Box 123\nSex: F", "PO Box 123"),
        ("Address: Sydney N.S.W. 2000.", "Sydney N.S.W. 2000"),
        (
            "Address: 45 St Kilda Rd, St Kilda VIC 3182 UR: 0012345",
            "45 St Kilda Rd, St Kilda VIC 3182",
        ),
    ],
)
def test_header_address_value_ends_cleanly(text, value):
    result = _pipeline(text)
    start = text.index(value)
    covering = [
        s for s in result.spans if s.start < start + len(value) and s.end > start
    ]
    assert [(s.start, s.end) for s in covering] == [(start, start + len(value))]
    assert covering[0].canonical_label == "STREET_ADDRESS"
    assert covering[0].metadata["subtype"] == AU_HEADER


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Name: Jane CITIZEN DOB: 04/07/1961",
            {("Jane CITIZEN", "PERSON"), ("04/07/1961", "DATE_OF_BIRTH")},
        ),
        (
            "Surname: QUANDT  Given Names: Zelphira Mae",
            {("QUANDT", "LAST_NAME"), ("Zelphira Mae", "FIRST_NAME")},
        ),
        (
            "Patient Name: CITIZEN, Jane Mae\tUR: 0012345",
            {("CITIZEN, Jane Mae", "PERSON"), ("0012345", "ID_NUM")},
        ),
        (
            "Address: 12 Smith Street, Randwick NSW 2031 Ph: 0412 345 678",
            {
                ("12 Smith Street, Randwick NSW 2031", "STREET_ADDRESS"),
                ("0412 345 678", "PHONE"),
            },
        ),
        (
            "GP: Dr Wren Pellow said the results were normal.",
            {("Wren Pellow", "PERSON")},
        ),
        ("NEXT OF KIN: ORLAN QUANDT", {("ORLAN QUANDT", "PERSON")}),
        ("Date of Birth  1961-07-04", {("1961-07-04", "DATE_OF_BIRTH")}),
    ],
)
def test_values_stop_at_line_end_or_next_label(text, expected):
    assert _found(_pipeline(text), text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Patient was seen by GP today.",
        "Name of drug: aspirin",
        "Address: see referral",
        "Address: Same as above",
        "the GP said Jane was well",
        "Discussed with NOK today.",
        "Referred by the ward team for review.",
        "Reported by phone to the ward.",
        "Date of birth not recorded.",
        "DOB: unknown",
        "Brand name: Panadol",
        "Requesting clinician to review.",
        "Patient name withheld.",
        "Surname unknown, Given Names unknown.",
        "GP Management Plan reviewed.",
        "NOK Aware of admission.",
        "Surname Unknown",
        "Address 2 concerns at the next visit.",
        # ``<Noun> Name:`` is not a person field.
        "Drug Name: Aspirin",
        "Medication Name: Metformin 500mg",
        "Test Name: FBC",
        "Specimen Name: Blood",
        "Hospital Name: Royal Prince Alfred",
        "DRUG NAME: ASPIRIN",
        "Practice Name: Northgate Medical",
        "Study Name: Heart Trial",
        # Imaging and report-template nouns.
        "Scanner Name: Siemens Magnetom",
        "Contrast Name: Omnipaque",
        "Protocol Name: Brain Routine",
        "Sequence Name: Axial Flair",
        "Modality Name: CT",
        "Equipment Name: Philips Ingenia",
        "Machine Name: Scanner Two",
        "Agent Name: Gadovist",
        "Tracer Name: Fluciclovine",
        "Radiopharmaceutical Name: Technetium Sestamibi",
        "Isotope Name: Gallium",
        "Examination Name: CT Chest",
        "Exam Name: MRI Knee",
        "Series Name: Coronal Reformat",
        "Template Name: Standard Report",
        "Form Name: Request Form",
        "Camera Name: Gamma Camera",
        "Coil Name: Head Coil",
        "Kit Name: Cardiolite",
        "Instrument Name: Analyser Two",
        "Analyser Name: Sysmex Prime",
        "Analyzer Name: Sysmex Prime",
        "Station Name: Reading Room",
        "Scanner name: Siemens Magnetom",
        "SCANNER NAME: SIEMENS MAGNETOM",
        # Placeholders are not names; a ward or bed is not an address.
        "GP: Unknown",
        "NOK: Nil",
        "NOK: N/A",
        "NOK: NA",
        "Surname: None",
        "Given Names: Not stated",
        "Name: Not known",
        "Patient Name: UNKNOWN\nSex: F",
        "Address: Ward 4B",
        "Address: Bed 12",
        "Address: Room 3",
    ],
)
def test_prose_with_header_words_is_not_tagged(no_model, text):
    result = _pipeline(text)
    assert not [
        (text[s.start : s.end], s.canonical_label)
        for s in result.spans
        if s.canonical_label in _PERSONAL_LABELS or _subtype(s) == AU_HEADER
    ]
    entities = extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale="en_AU"
    ).entities
    assert not [e for e in entities if _subtype(e) == AU_HEADER]


# A header value followed by a line break and a capitalised heading (or a tab
# and the next field) stops at the source line end on every entry point, even
# though the Pipeline collapses the line break to a space before detection.
_LINE_END_CASES = [
    ("GP: Dr Wren Pellow\nCLINICAL NOTES: cough", {("Wren Pellow", "PERSON")}),
    (
        "Name: Jane CITIZEN\nPresenting Complaint: chest pain",
        {("Jane CITIZEN", "PERSON")},
    ),
    (
        "Address: 12 Smith Street\nComments: none",
        {("12 Smith Street", "STREET_ADDRESS")},
    ),
    (
        "Address: 12 Smith Street Randwick\nPatient Presented with pain",
        {("12 Smith Street Randwick", "STREET_ADDRESS")},
    ),
    (
        "Surname: QUANDT\r\nClinical History: cough",
        {("QUANDT", "LAST_NAME")},
    ),
    (
        "Address: 12 Smith St\tPh 0412 345 678",
        {("12 Smith St", "STREET_ADDRESS"), ("0412 345 678", "PHONE")},
    ),
    (
        "Address: 12 Smith Street\nUR 0012345",
        {("12 Smith Street", "STREET_ADDRESS"), ("0012345", "ID_NUM")},
    ),
]


@pytest.mark.parametrize(("text", "expected"), _LINE_END_CASES)
def test_values_stop_at_source_line_end_in_pipeline(text, expected):
    result = _pipeline(text)
    assert _found(result, text) == expected
    for span in result.spans:
        surface = text[span.start : span.end]
        assert "\n" not in surface and "\t" not in surface


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize(("text", "expected"), _LINE_END_CASES)
def test_values_stop_at_source_line_end_in_extract_pii_and_sweep(text, expected):
    entities = extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale="en_AU"
    ).entities
    swept = safety_sweep(text, [], lang="en", locale="en_AU")
    for found in (entities, swept):
        assert {text[e.start : e.end] for e in found} == {v for v, _ in expected}
        for e in found:
            surface = text[e.start : e.end]
            assert "\n" not in surface and "\t" not in surface


def test_source_layout_is_dropped_after_a_pipeline_run():
    from openmed.core import au_records

    _pipeline("Name: Jane CITIZEN\nPresenting Complaint: chest pain")
    assert au_records._SOURCE_LAYOUT.get() is None


# Column layout without a ``:`` (tab or two spaces). The Pipeline sees it
# through the bound source layout, the other entry points directly.
_COLUMN_LAYOUT = (
    "Surname\tQUANDT\n"
    "Given Names  Orla\n"
    "Address\t12 Smith Street, Randwick NSW 2031\n"
    "GP\tDr Wren Pellow\n"
    "DOB\t12-Mar-58\n"
)
_COLUMN_VALUES = {
    "QUANDT",
    "Orla",
    "12 Smith Street, Randwick NSW 2031",
    "Wren Pellow",
    "12-Mar-58",
}


@pytest.mark.usefixtures("no_model")
def test_column_layout_without_colon_in_source_text():
    text = _COLUMN_LAYOUT
    entities = extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale="en_AU"
    ).entities
    swept = safety_sweep(text, [], lang="en", locale="en_AU")
    spans = _pipeline(text).spans
    for found in (entities, swept, spans):
        assert {text[e.start : e.end] for e in found if _subtype(e) == AU_HEADER} == (
            _COLUMN_VALUES
        )


@pytest.mark.usefixtures("no_model")
def test_extract_pii_en_au_emits_header_values():
    entities = extract_pii(
        HEADER, model_name="fixture-pii-model", lang="en", locale="en_AU"
    ).entities
    found = {
        (HEADER[e.start : e.end], _subtype(e))
        for e in entities
        if _subtype(e) == AU_HEADER
    }
    assert found == {(value, AU_HEADER) for value, _label in HEADER_VALUES}
    _assert_header_address_not_split(entities)


def test_safety_sweep_en_au_emits_header_values():
    swept = safety_sweep(HEADER, [], lang="en", locale="en_AU")
    found = {HEADER[e.start : e.end] for e in swept if _subtype(e) == AU_HEADER}
    assert found == {value for value, _label in HEADER_VALUES}
    _assert_header_address_not_split(swept)


def _assert_header_address_not_split(entities):
    value = "Unit 3, 12 Smith Street, Randwick NSW 2031"
    start = HEADER.index(value)
    covering = [e for e in entities if e.start < start + len(value) and e.end > start]
    assert [(e.start, e.end, _subtype(e)) for e in covering] == [
        (start, start + len(value), AU_HEADER)
    ]


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------

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
            HEADER, model_name="fixture-pii-model", lang="en", locale=locale
        ).entities
        swept = safety_sweep(HEADER, [], lang="en", locale=locale)
        result = _pipeline(HEADER, policy=policy, locale=locale)
        assert not [span for span in result.spans if _subtype(span) == AU_HEADER]
        return (
            _snapshot(entities),
            _snapshot(swept),
            result.redacted_text,
            _span_snapshot(result.spans),
        )

    with_hook = outputs()

    # The same run with the AU header patterns removed from every pattern
    # table must be byte-identical (apart from per-run salted hashes).
    for table in (LOCALE_PII_PATTERNS, LANGUAGE_PII_PATTERNS):
        for key, patterns in list(table.items()):
            kept = [p for p in patterns if p not in AU_HEADER_PII_PATTERNS]
            monkeypatch.setitem(table, key, kept)
    assert outputs() == with_hook
