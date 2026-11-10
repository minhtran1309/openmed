"""AU regressions from layout-heavy report prints (EMR titles, contact tables).

Covers five detector gaps under the ``en_AU`` locale (``ccia_*`` policies):

* an MRN with a trailing check letter, labelled or in an EMR running title
  line (``SURNAME, Given - <digits>-<letter>``);
* a phone number whose next (or previous) table cell starts with a digit;
* a date that repeats a ``DOB``-labelled value elsewhere in the document
  (``DATE_OF_BIRTH``, not ``DATE``), and an unlabelled table copy of a
  labelled record number;
* ``Participant ID`` / ``Subject ID`` record-number labels;
* the user id in a ``Printed by <NAME> [<USERID>]`` print footer.

Every name, number and date here is synthetic and invented for these tests.
The model is always stubbed.
"""

from __future__ import annotations

import dataclasses

import pytest

from openmed.core import au_records
from openmed.core.au_contact import AU_CONTACT_PII_PATTERNS, AU_PHONE
from openmed.core.au_records import (
    AU_HEADER,
    AU_HEADER_PII_PATTERNS,
    AU_PRINT_FOOTER_PII_PATTERNS,
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


def _pipeline(
    text: str,
    *,
    policy: str = "ccia_l3",
    locale: str | None = None,
    preserve_whitespace: bool = True,
):
    from openmed.core.pipeline import Pipeline

    return Pipeline(
        policy=policy,
        model_detector=_stub_detector,
        preserve_whitespace=preserve_whitespace,
        use_safety_sweep=True,
    ).run(text, locale=locale)


def _extract(text: str, *, locale: str | None = "en_AU"):
    return extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale=locale
    ).entities


def _sweep(text: str, *, locale: str | None = "en_AU"):
    return safety_sweep(text, [], lang="en", locale=locale)


def _subtype(item) -> str | None:
    return (item.metadata or {}).get("subtype")


def _covering(items, start: int, end: int):
    return [item for item in items if item.start < end and item.end > start]


def _nth(text: str, value: str, n: int) -> int:
    start = -1
    for _ in range(n + 1):
        start = text.index(value, start + 1)
    return start


def _span_at(result, start: int, end: int):
    covering = _covering(result.spans, start, end)
    assert len(covering) == 1, [
        (s.start, s.end, s.canonical_label, _subtype(s)) for s in result.spans
    ]
    return covering[0]


# ---------------------------------------------------------------------------
# 1. MRN with a trailing check letter
# ---------------------------------------------------------------------------

_TITLE_TEXT = (
    "MRI Pelvis                                            "
    "QUINLAN, Harriet Joy - 40817263-T\n"
    "* Preliminary Report *\n\n"
    "Findings are stable.\n"
)


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_emr_title_line_mrn_with_check_letter_is_id_num(preserve_whitespace):
    text = _TITLE_TEXT if preserve_whitespace else _TITLE_TEXT.strip()
    value = "40817263-T"
    start = text.index(value)
    span = _span_at(
        _pipeline(text, preserve_whitespace=preserve_whitespace),
        start,
        start + len(value),
    )
    assert (span.start, span.end) == (start, start + len(value))
    assert span.canonical_label == "ID_NUM"
    assert _subtype(span) == AU_UR


@pytest.mark.usefixtures("no_model")
def test_emr_title_line_mrn_in_extract_pii_and_sweep():
    text = "QUINLAN, Harriet - 40817263-T\n"
    value = "40817263-T"
    start = text.index(value)
    for entities in (_extract(text), _sweep(text)):
        found = [
            (e.start, e.end, e.label, _subtype(e))
            for e in _covering(entities, start, start + len(value))
        ]
        assert found == [(start, start + len(value), "medical_record_number", AU_UR)]


@pytest.mark.parametrize(
    "text",
    [
        "MRN: 5093172-K reviewed.",
        "UR No 5093172-K reviewed.",
        "Patient ID 5093172-K reviewed.",
    ],
)
def test_labelled_mrn_keeps_its_check_letter(text):
    value = "5093172-K"
    start = text.index(value)
    span = _span_at(_pipeline(text), start, start + len(value))
    assert (span.start, span.end, span.canonical_label) == (
        start,
        start + len(value),
        "ID_NUM",
    )
    assert value not in _pipeline(text).redacted_text


@pytest.mark.parametrize(
    ("text", "surface"),
    [
        # No ``SURNAME, Given`` before the dash.
        ("Results - 40817263-T pending.", "40817263"),
        ("Stage IV - 40817263-T", "40817263"),
        # Too short to be a record number.
        ("QUINLAN, Harriet - 2019-T", "2019"),
        # Prose, not a title-case name after the comma.
        ("STABLE, see note - 40817263-T", "40817263"),
    ],
)
def test_title_line_rule_needs_the_name_and_a_long_value(text, surface):
    start = text.index(surface)
    result = _pipeline(text)
    assert not [
        s
        for s in _covering(result.spans, start, start + len(surface))
        if s.canonical_label == "ID_NUM"
    ]


# ---------------------------------------------------------------------------
# 2. Phones in contact tables
# ---------------------------------------------------------------------------

_CONTACT_TABLE = (
    "Role                 Name               Phone                Provider No\n"
    "\n"
    "Oncology nurse       Kim TRAN           (03) 5550 7312       284719KT\n"
    "\n"
    "Social worker        Ola BAKER          0491 570 156         391842QW\n"
)


@pytest.mark.parametrize("preserve_whitespace", [True, False])
@pytest.mark.parametrize("value", ["(03) 5550 7312", "0491 570 156"])
def test_phone_before_a_digit_cell_is_detected(value, preserve_whitespace):
    text = _CONTACT_TABLE
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    start = text.index(value) - shift
    span = _span_at(result, start, start + len(value))
    assert (span.start, span.end) == (start, start + len(value))
    assert span.canonical_label == "PHONE"
    assert _subtype(span) == AU_PHONE
    assert value not in result.redacted_text


def test_phone_after_a_digit_cell_is_detected():
    text = "Provider No          Phone\n\n284719KT             (03) 5550 7312\n"
    value = "(03) 5550 7312"
    start = text.index(value)
    span = _span_at(_pipeline(text), start, start + len(value))
    assert (span.start, span.end, span.canonical_label) == (
        start,
        start + len(value),
        "PHONE",
    )


@pytest.mark.parametrize(
    "text",
    [
        # A single space joins the digits into one longer run.
        "Batch 0491 570 156 4 logged.",
        "Lot (03) 5550 7312 8841 logged.",
        "Serial 9 0491 570 156 logged.",
        # The table layout never turns a long run into a phone.
        "Batch                0491570156442          next\n",
    ],
)
def test_longer_digit_runs_are_still_not_phones(text):
    result = _pipeline(text)
    assert not [s for s in result.spans if s.canonical_label == "PHONE"], [
        (text[s.start : s.end], s.canonical_label) for s in result.spans
    ]


@pytest.mark.usefixtures("no_model")
def test_table_phone_rule_is_pipeline_only():
    # Outside a Pipeline run there is no source layout, and the AU phone
    # patterns behave exactly as before on raw text.
    text = "Ph (03) 5550 7312 4 logged."
    assert [e for e in _extract(text) if _subtype(e) == AU_PHONE] == []
    assert [e for e in _sweep(text) if _subtype(e) == AU_PHONE] == []


# ---------------------------------------------------------------------------
# 3. Header DOB repeated without its label
# ---------------------------------------------------------------------------

_RUNNING_HEADER_TEXT = (
    "                 Banksia Imaging                              "
    "Ivy MORALES 17/05/2014\n"
    "                 Radiology\n\n"
    "Patient: Ivy MORALES          DOB: 17/05/2014          UR: 6604182\n\n"
    "Scan performed 03/02/2026. Prior study 17/05/2014 not available.\n"
)


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_date_repeating_a_labelled_dob_is_date_of_birth(preserve_whitespace):
    text = _RUNNING_HEADER_TEXT
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    labels = {}
    for n in range(3):
        start = _nth(text, "17/05/2014", n) - shift
        span = _span_at(result, start, start + 10)
        assert (span.start, span.end) == (start, start + 10)
        labels[n] = span.canonical_label
    assert labels == {0: "DATE_OF_BIRTH", 1: "DATE_OF_BIRTH", 2: "DATE_OF_BIRTH"}
    # A different date stays a plain date.
    other = text.index("03/02/2026") - shift
    assert _span_at(result, other, other + 10).canonical_label == "DATE"


def test_repeated_dob_is_found_when_the_label_comes_later():
    text = "Ivy MORALES 17/05/2014\n\nPatient: Ivy MORALES          DOB: 17/05/2014\n"
    result = _pipeline(text)
    first = text.index("17/05/2014")
    span = _span_at(result, first, first + 10)
    assert span.canonical_label == "DATE_OF_BIRTH"
    assert _subtype(span) == AU_HEADER


def test_header_date_without_a_dob_label_stays_date():
    text = (
        "                 Banksia Imaging                              "
        "Ivy MORALES 17/05/2014\n\n"
        "Scan performed 03/02/2026.\n"
    )
    result = _pipeline(text)
    start = text.index("17/05/2014")
    assert _span_at(result, start, start + 10).canonical_label == "DATE"


def test_dob_is_not_propagated_into_a_longer_value():
    text = "DOB: 7/5/14\n\nRef 17/5/14 and 7/5/145 and v7/5/14 noted.\n"
    result = _pipeline(text)
    dobs = [
        (text[s.start : s.end], s.start)
        for s in result.spans
        if s.canonical_label == "DATE_OF_BIRTH"
    ]
    assert dobs == [("7/5/14", text.index("7/5/14"))]


# ---------------------------------------------------------------------------
# 4. Record numbers: unlabelled table copy, Participant ID
# ---------------------------------------------------------------------------

_MRN_TABLE_TEXT = (
    "Ferris, Noah (MRN# 7302518) DOB: 09/10/2019               Printed 01/02/26\n"
    "\n"
    " Patient Information\n"
    "Name                          MRN                          Description\n"
    "\n"
    "Noah Ferris                   7302518                      6 y male\n"
)


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_table_copy_of_a_labelled_mrn_is_id_num(preserve_whitespace):
    text = _MRN_TABLE_TEXT
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    for n in range(2):
        start = _nth(text, "7302518", n)
        span = _span_at(result, start, start + 7)
        assert (span.start, span.end) == (start, start + 7)
        assert span.canonical_label == "ID_NUM"
        assert _subtype(span) == AU_UR
    assert "7302518" not in result.redacted_text


@pytest.mark.parametrize(
    "text",
    [
        # Too short to propagate: four digits can be a year or a postcode.
        "UR: 2031\n\nRandwick NSW 2031, seen 2031 hrs.\n",
        # Part of a longer number or code.
        "MRN: 730251\n\nBatch 7302518 and 1730251 and A730251 noted.\n",
    ],
)
def test_short_or_embedded_record_numbers_are_not_propagated(text):
    result = _pipeline(text)
    ids = [
        text[s.start : s.end]
        for s in result.spans
        if s.canonical_label == "ID_NUM" and _subtype(s) == AU_UR
    ]
    assert len(ids) == 1, ids


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Participant ID: QRB5-3071\n", "QRB5-3071"),
        ("Participant No. 40219\n", "40219"),
        ("Subject ID   QRB-55031\n", "QRB-55031"),
    ],
)
def test_participant_and_subject_ids_are_record_numbers(text, value):
    start = text.index(value)
    span = _span_at(_pipeline(text), start, start + len(value))
    assert (span.start, span.end, span.canonical_label, _subtype(span)) == (
        start,
        start + len(value),
        "ID_NUM",
        AU_UR,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Protocol: QRB-Alpha-2 open.",
        "Trial ID: NCT04211234 registered.",
        "The participant identified 3 sites.",
    ],
)
def test_protocol_and_registry_ids_are_not_record_numbers(text):
    result = _pipeline(text)
    assert not [s for s in result.spans if _subtype(s) == AU_UR]


# ---------------------------------------------------------------------------
# 5. User id in a print footer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "                Printed by MOSS, GRETA [MOSSG] at 05/03/2026 4:10:02 PM\n",
        "Printed by: Moss, Greta [gmoss01] at 05/03/2026 16:10\n",
        "Printed by Greta MOSS [MOSSG] on 05/03/2026\n",
    ],
)
def test_print_footer_user_id_is_username(text):
    value = text[text.index("[") + 1 : text.index("]")]
    start = text.index(value)
    result = _pipeline(text)
    span = _span_at(result, start, start + len(value))
    assert (span.start, span.end, span.canonical_label) == (
        start,
        start + len(value),
        "USERNAME",
    )
    assert f"[{value}]" not in result.redacted_text


@pytest.mark.parametrize(
    "text",
    [
        "Status                 Final [99]\n",
        "Printed by INTERFACE, IMAGING FEED at 05/03/2026\n",
        "See reference [12] printed by the lab.\n",
        "Printed by MOSS, GRETA [ ] at 05/03/2026\n",
    ],
)
def test_bracketed_values_outside_a_print_footer_are_not_usernames(text):
    result = _pipeline(text)
    assert not [s for s in result.spans if s.canonical_label == "USERNAME"]


def test_print_footer_patterns_registered_for_en_au_only():
    au_patterns = LOCALE_PII_PATTERNS["en_au"]
    assert AU_PRINT_FOOTER_PII_PATTERNS
    assert all(pattern in au_patterns for pattern in AU_PRINT_FOOTER_PII_PATTERNS)
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale=locale)
        assert not any(p in patterns for p in AU_PRINT_FOOTER_PII_PATTERNS)


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------

_NON_AU_TEXT = (
    _TITLE_TEXT
    + _CONTACT_TABLE
    + _RUNNING_HEADER_TEXT
    + _MRN_TABLE_TEXT
    + "Participant ID: QRB5-3071\n"
    + "MRN: 5093172-K reviewed.\n"
    + "Printed by MOSS, GRETA [MOSSG] at 05/03/2026 4:10:02 PM\n"
    + "  NUCLEAR MEDICINE REPORT                         NAME:          "
    + "VARGA, PETRA LOUISE\n"
    + "Report Name: Weekly Summary\nDrug Name: Kerrin Tablets\n"
)
_SALTED_FIELDS = ("doc_id", "text_hash")
_SALTED_METADATA = ("normalized_text_hash",)
_NEW_PATTERNS = (
    *AU_RECORD_PII_PATTERNS,
    *AU_HEADER_PII_PATTERNS,
    *AU_PRINT_FOOTER_PII_PATTERNS,
    *AU_CONTACT_PII_PATTERNS,
)


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

    with_hooks = outputs()

    # The same run with every AU record, header, footer and contact pattern
    # removed and the labelled-value propagation disabled must be
    # byte-identical (apart from per-run salted hashes).
    for table in (LOCALE_PII_PATTERNS, LANGUAGE_PII_PATTERNS):
        for key, patterns in list(table.items()):
            kept = [p for p in patterns if p not in _NEW_PATTERNS]
            monkeypatch.setitem(table, key, kept)
    monkeypatch.setattr(
        au_records,
        "propagate_labelled_values",
        lambda text, entities, patterns: entities,
    )
    assert outputs() == with_hooks


# ---------------------------------------------------------------------------
# Fix round 1: check-letter stems and wider title lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_bare_copy_of_a_check_letter_mrn_is_id_num(preserve_whitespace):
    text = (
        "Name: Leo FARRANT\nUR: 6193057-M\n\n"
        "Name                MRN                 Ward\n\n"
        "Leo Farrant         6193057             4B\n"
    )
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    anchor = text.index("6193057-M") - shift
    assert (_span_at(result, anchor, anchor + 9).start, anchor) == (anchor, anchor)
    assert _span_at(result, anchor, anchor + 9).end == anchor + 9
    copy = _nth(text, "6193057", 1) - shift
    span = _span_at(result, copy, copy + 7)
    assert (span.start, span.end, span.canonical_label, _subtype(span)) == (
        copy,
        copy + 7,
        "ID_NUM",
        AU_UR,
    )


@pytest.mark.parametrize(
    "text",
    [
        # The stem inside a longer digit run, code or date is not a copy.
        "UR: 619305-M\n\nBatch 6193057 and 1619305 and A619305 and 619305/2.\n",
        # A stem shorter than six characters is never propagated.
        "UR: 61930-M\n\nSeen 61930 times.\n",
    ],
)
def test_check_letter_stem_keeps_the_exact_token_guards(text):
    result = _pipeline(text)
    ids = [s for s in result.spans if s.canonical_label == "ID_NUM"]
    assert len(ids) <= 1, [(text[s.start : s.end]) for s in ids]


@pytest.mark.parametrize(
    "line",
    [
        "Report          QUINLAN, HARRIET JOY - 40817263-T",
        "Report          QUINLAN,  Harriet Joy - 40817263-T",
        "Report          QUINLAN, Harriet Joy  -  40817263-T",
        "Report          QUINLAN, HARRIET - 40817263",
        "Report          O'NEILL, MAEVE - 40817263-T",
    ],
)
def test_title_line_accepts_caps_given_names_and_wider_gaps(line):
    text = line + "\n* Preliminary Report *\n"
    value = line.rsplit(" ", 1)[-1]
    start = text.index(value)
    span = _span_at(_pipeline(text), start, start + len(value))
    assert (span.start, span.end, span.canonical_label, _subtype(span)) == (
        start,
        start + len(value),
        "ID_NUM",
        AU_UR,
    )


@pytest.mark.parametrize(
    ("text", "surface"),
    [
        # A phone or a date range after a name and a dash is not a record.
        ("QUINLAN, HARRIET - 0491570156\n", "0491570156"),
        ("QUINLAN, Harriet - 0355501234\n", "0355501234"),
        ("QUINLAN, HARRIET - 20260301-20260315\n", "20260301"),
        ("QUINLAN, HARRIET - 01/03/2026 - 15/03/2026\n", "01/03/2026"),
        ("QUINLAN, HARRIET - 0491 570 156\n", "0491"),
        # Still needs the comma shape: no comma, no record.
        ("STAGE IV CANCER - 40817263-T\n", "40817263"),
    ],
)
def test_title_line_does_not_take_phones_or_date_ranges(text, surface):
    start = text.index(surface)
    result = _pipeline(text)
    assert not [
        s
        for s in _covering(result.spans, start, start + len(surface))
        if s.canonical_label == "ID_NUM"
    ]


# ---------------------------------------------------------------------------
# Fix round 2: a deny-list noun in the previous column does not block ``NAME:``
# ---------------------------------------------------------------------------

_TWO_COLUMN_HEADER = (
    "  NUCLEAR MEDICINE REPORT                         NAME:          "
    "VARGA, PETRA LOUISE\n"
    "  Westbrook Clinic                                DOB:           "
    "11/08/2016\n"
)


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_noun_in_previous_column_does_not_block_a_bare_name_label(
    preserve_whitespace,
):
    text = _TWO_COLUMN_HEADER
    value = "VARGA, PETRA LOUISE"
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    start = text.index(value) - shift
    span = _span_at(result, start, start + len(value))
    assert (span.start, span.end, span.canonical_label, _subtype(span)) == (
        start,
        start + len(value),
        "PERSON",
        AU_HEADER,
    )
    assert value not in result.redacted_text


@pytest.mark.parametrize(
    "text",
    [
        "Report Name: Weekly Summary\n",
        "Drug Name: Kerrin Tablets\n",
        "REPORT NAME:    QUARTERLY AUDIT\n",
        "Report Name:\tMonthly Review\n",
    ],
)
def test_single_space_noun_name_label_still_does_not_tag(text):
    result = _pipeline(text)
    assert not [s for s in result.spans if _subtype(s) == AU_HEADER], [
        (text[s.start : s.end], s.canonical_label) for s in result.spans
    ]
