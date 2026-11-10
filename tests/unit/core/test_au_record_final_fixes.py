"""AU record-number fixes from the mt.2 final review (I1, I3, Accession Nr).

* I1: an unlabelled copy of a labelled record number is still a record number
  when a space and then a date or another number follow or precede it (a
  running header ``SURNAME, Given 0012345 04/07/1961``).
* I3: after an explicit record-number label, a value with a trailing check
  letter (``0012345A``) or an embedded letter (``23S012345``) is a record
  number.
* ``Accession Nr`` / ``Accession Nr.`` is a record-number label and a table
  header.

Every name, number and date here is synthetic and invented for these tests.
The model is always stubbed.
"""

from __future__ import annotations

import pytest

from openmed.core.au_records import (
    AU_RECORD_CONTEXT_WORDS,
    AU_TABLE_HEADER_LABELS,
    AU_TABLE_HEADER_SUBTYPES,
    AU_UR,
    table_header_key,
)
from openmed.processing.outputs import PredictionResult


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


def _pipeline(text: str, *, policy: str = "ccia_l3", preserve_whitespace=True):
    from openmed.core.pipeline import Pipeline

    return Pipeline(
        policy=policy,
        model_detector=_stub_detector,
        preserve_whitespace=preserve_whitespace,
        use_safety_sweep=True,
    ).run(text)


def _subtype(item) -> str | None:
    return (item.metadata or {}).get("subtype")


def _covering(spans, start: int, end: int):
    return [s for s in spans if s.start < end and s.end > start]


def _nth(text: str, value: str, n: int) -> int:
    start = -1
    for _ in range(n + 1):
        start = text.index(value, start + 1)
    return start


def _assert_record(result, start: int, value: str) -> None:
    covering = _covering(result.spans, start, start + len(value))
    assert [(s.start, s.end, s.canonical_label, _subtype(s)) for s in covering] == [
        (start, start + len(value), "ID_NUM", AU_UR)
    ]


# ---------------------------------------------------------------------------
# I1: a copy next to a date or number, one space away
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preserve_whitespace", [True, False])
@pytest.mark.parametrize(
    "text",
    [
        # MRN then DOB on the running header.
        "UR: 0058213\nPELLOW, Odette 0058213 22/09/1958\n",
        # DOB then MRN.
        "UR: 0058213\n22/09/1958 0058213 PELLOW\n",
        # A number on both sides.
        "UR: 0058213\nPage 2 0058213 3 of 4\n",
    ],
)
def test_copy_one_space_from_a_date_or_number_is_tagged(text, preserve_whitespace):
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    for n in range(2):
        _assert_record(result, _nth(text, "0058213", n) - shift, "0058213")
    assert "0058213" not in result.redacted_text


@pytest.mark.parametrize(
    "text",
    [
        # Directly adjacent letters or digits: part of a longer token.
        "UR: 0058213\nBatch 00582139 and 10058213 and Q0058213 and 0058213B.\n",
        # Joined by a hyphen, slash or dot to digits: a longer number or date.
        "UR: 0058213\nRef 0058213-4 and 7/0058213 and 0058213.5 noted.\n",
    ],
)
def test_copy_inside_a_longer_token_is_not_tagged(text):
    result = _pipeline(text)
    ids = [
        text[s.start : s.end]
        for s in result.spans
        if s.canonical_label == "ID_NUM" and _subtype(s) == AU_UR
    ]
    assert ids == ["0058213"], ids


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_copy_in_a_url_path_is_tagged(preserve_whitespace):
    text = (
        "URN: 0058213\n"
        "                    https://results.example.org/viewer/urn/0058213\n"
    )
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    _assert_record(result, _nth(text, "0058213", 1) - shift, "0058213")
    assert "0058213" not in result.redacted_text


# ---------------------------------------------------------------------------
# I3: labelled alphanumeric record numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("URN: 0047719B\n", "0047719B"),
        ("MRN: 3381046K reviewed.\n", "3381046K"),
        ("Specimen No: 24S077123\n", "24S077123"),
        ("Lab No: 3381046W\n", "3381046W"),
        ("UR: 3381046W\n", "3381046W"),
        ("Accession No: 24S077123\n", "24S077123"),
        ("Hospital No: AB12C3456\n", "AB12C3456"),
        ("Patient ID: 2024-S-0077123\n", "2024-S-0077123"),
    ],
)
def test_labelled_alphanumeric_record_number_is_tagged(text, value):
    result = _pipeline(text)
    _assert_record(result, text.index(value), value)
    assert value not in result.redacted_text


@pytest.mark.parametrize(
    "text",
    [
        # Prose years after episode/ur stay untagged (Task 1 rulings).
        "Seen in a prior episode 2019 for the same complaint.\n",
        "Episode 2019S was reviewed.\n",
        "your 2019 results and ur 2019A note\n",
        # No digit, or too few digits, is not a record number.
        "UR: PENDING\n",
        "MRN: ABC1D\n",
        # Lowercase is prose, not a record value.
        "UR: 0047719b\n",
    ],
)
def test_unlabelled_or_digitless_values_are_not_record_numbers(text):
    result = _pipeline(text)
    assert not [s for s in result.spans if _subtype(s) == AU_UR], [
        (text[s.start : s.end], s.canonical_label) for s in result.spans
    ]


def test_too_long_labelled_value_is_not_a_record_number():
    text = "MRN: 1234567890ABCDEFGHJK\n"
    result = _pipeline(text)
    assert not [s for s in result.spans if _subtype(s) == AU_UR]


@pytest.mark.parametrize("preserve_whitespace", [True, False])
def test_bare_copy_of_an_undashed_check_letter_mrn_is_tagged(preserve_whitespace):
    text = (
        "Name: Odette PELLOW\nURN: 0047719B\n\n"
        "Name                MRN                 Ward\n\n"
        "Odette Pellow       0047719             6C\n"
    )
    result = _pipeline(text, preserve_whitespace=preserve_whitespace)
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    _assert_record(result, text.index("0047719B") - shift, "0047719B")
    _assert_record(result, _nth(text, "0047719", 1) - shift, "0047719")


def test_title_line_accepts_an_undashed_check_letter():
    text = "Report          PELLOW, Odette - 40817263T\n* Preliminary Report *\n"
    _assert_record(_pipeline(text), text.index("40817263T"), "40817263T")


# ---------------------------------------------------------------------------
# Accession Nr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Accession Nr: 24S077123\n",
        "Accession Nr. 24077123\n",
        "ACCESSION NR 24077123\n",
    ],
)
def test_accession_nr_is_a_record_number_label(text):
    value = text.split()[-1]
    _assert_record(_pipeline(text), text.index(value), value)


def test_accession_nr_is_a_table_header():
    for word in ("Accession Nr", "Accession Nr."):
        assert word in AU_RECORD_CONTEXT_WORDS
        assert AU_TABLE_HEADER_LABELS[word] == "ID_NUM"
        assert AU_TABLE_HEADER_SUBTYPES[table_header_key(word)] == AU_UR


# ---------------------------------------------------------------------------
# I2: anchors shared between Pipeline runs
# ---------------------------------------------------------------------------


def test_exchange_reports_anchors_and_propagates_external_ones():
    from openmed.core.au_records import labelled_value_exchange

    labelled = "UR: 0063391\nDOB: 15/03/1972\n"
    copy = "TREVANION, Edith 0063391 15/03/1972\n"
    with labelled_value_exchange() as first:
        _pipeline(labelled)
    assert set(first.found) == {"0063391", "15/03/1972"}
    assert {a.label for a in first.found.values()} == {
        "medical_record_number",
        "date_of_birth",
    }

    without = _pipeline(copy)
    assert not [s for s in without.spans if _subtype(s) == AU_UR]
    with labelled_value_exchange(first.found) as second:
        result = _pipeline(copy)
    assert second.found == {}
    _assert_record(result, copy.index("0063391"), "0063391")
    dob = copy.index("15/03/1972")
    assert [s.canonical_label for s in _covering(result.spans, dob, dob + 10)] == [
        "DATE_OF_BIRTH"
    ]


def test_external_anchors_do_nothing_outside_the_au_locale():
    from openmed.core.au_records import labelled_value_exchange

    with labelled_value_exchange() as first:
        _pipeline("UR: 0063391\nDOB: 15/03/1972\n")
    copy = "TREVANION, Edith 0063391 15/03/1972\n"
    baseline = _pipeline(copy, policy="hipaa_safe_harbor")
    with labelled_value_exchange(first.found):
        shared = _pipeline(copy, policy="hipaa_safe_harbor")
    assert shared.redacted_text == baseline.redacted_text
    assert [(s.start, s.end, s.canonical_label) for s in shared.spans] == [
        (s.start, s.end, s.canonical_label) for s in baseline.spans
    ]
