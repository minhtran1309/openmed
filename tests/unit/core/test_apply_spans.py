"""Tests for the public ``openmed.core.apply_spans`` seam.

Every value here is synthetic: names are invented, dates are arbitrary and the
patient key / secret are test-only material. ``apply_spans`` never detects, so
no model, regex stage or safety sweep is stubbed: none may run.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from openmed.__about__ import __version__
from openmed.core.apply_spans import ApplySpansError, apply_spans
from openmed.core.date_shift import stable_offset_for
from openmed.core.errors import InputError
from openmed.core.locale_formats import LOCALE_DATE_ORDER, date_order_for_locale
from openmed.core.policy import load_policy
from openmed.core.provenance import policy_fingerprint

SYNTHETIC_KEY = "synthetic-patient-key-7781"
SYNTHETIC_SECRET = "synthetic-secret-material-0042"


def _span(span_id, text, value, label, *, occurrence=0, **extra):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(value, start + 1)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(value),
        "label": label,
        **extra,
    }


def _seg(segment_id, text, spans=()):
    return {"segment_id": segment_id, "text": text, "spans": list(spans)}


def _apply(document, *, policy="ccia_l1", treatment="mark", **kwargs):
    return apply_spans(document, treatment=treatment, policy=policy, **kwargs)


def _out(result, span_id):
    matches = [s for s in result.spans if s["span_id"] == span_id]
    assert len(matches) == 1, result.spans
    return matches[0]


def _seg_text(result, segment_id):
    return next(s["text"] for s in result.segments if s["segment_id"] == segment_id)


def _sliced(result, span_id):
    span = _out(result, span_id)
    return _seg_text(result, span["segment_id"])[span["out_start"] : span["out_end"]]


# ---------------------------------------------------------------------------
# D8: en-au is day-first
# ---------------------------------------------------------------------------


def test_en_au_is_registered_day_first() -> None:
    assert LOCALE_DATE_ORDER["en-au"] == "dmy"
    assert date_order_for_locale("en_AU") == "dmy"
    assert date_order_for_locale("en") == "mdy"


# ---------------------------------------------------------------------------
# mark: numbering
# ---------------------------------------------------------------------------


def test_mark_numbers_per_label_across_segments_with_variants() -> None:
    t1 = "Seen by Jane Roe and Ari Volk today."
    t2 = "JANE  ROE reviewed; jane roe signed."
    doc = [
        _seg(
            "s1",
            t1,
            [
                _span("a", t1, "Jane Roe", "PERSON"),
                _span("b", t1, "Ari Volk", "PERSON"),
            ],
        ),
        _seg(
            "s2",
            t2,
            [
                _span("c", t2, "JANE  ROE", "PERSON"),
                _span("d", t2, "jane roe", "PERSON"),
            ],
        ),
    ]
    result = _apply(doc)

    assert _seg_text(result, "s1") == "Seen by [PERSON_1] and [PERSON_2] today."
    assert _seg_text(result, "s2") == "[PERSON_1] reviewed; [PERSON_1] signed."
    for span_id, tag in {
        "a": "[PERSON_1]",
        "b": "[PERSON_2]",
        "c": "[PERSON_1]",
        "d": "[PERSON_1]",
    }.items():
        assert _sliced(result, span_id) == tag
        assert _out(result, span_id)["action"] == "mark"


def test_numbering_is_per_label() -> None:
    text = "Ari Volk phoned 0400 000 000 then Bo Teal phoned 0400 000 001."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("n1", text, "Ari Volk", "PERSON"),
                _span("p1", text, "0400 000 000", "PHONE"),
                _span("n2", text, "Bo Teal", "PERSON"),
                _span("p2", text, "0400 000 001", "PHONE"),
            ],
        )
    ]
    result = _apply(doc)
    assert result.segments[0]["text"] == (
        "[PERSON_1] phoned [PHONE_1] then [PERSON_2] phoned [PHONE_2]."
    )


def test_numbering_restarts_per_call() -> None:
    first_text = "Ari Volk and Bo Teal."
    second_text = "Bo Teal alone."
    first = _apply(
        [
            _seg(
                "s",
                first_text,
                [
                    _span("x", first_text, "Ari Volk", "PERSON"),
                    _span("y", first_text, "Bo Teal", "PERSON"),
                ],
            )
        ]
    )
    second = _apply(
        [_seg("s", second_text, [_span("y", second_text, "Bo Teal", "PERSON")])]
    )
    assert first.segments[0]["text"] == "[PERSON_1] and [PERSON_2]."
    assert second.segments[0]["text"] == "[PERSON_1] alone."


def test_spans_are_ordered_and_input_order_does_not_change_numbering() -> None:
    text = "Ari Volk then Bo Teal."
    spans = [
        _span("late", text, "Bo Teal", "PERSON"),
        _span("early", text, "Ari Volk", "PERSON"),
    ]
    result = _apply([_seg("s", text, spans)])
    assert result.segments[0]["text"] == "[PERSON_1] then [PERSON_2]."
    assert [s["span_id"] for s in result.spans] == ["early", "late"]


# ---------------------------------------------------------------------------
# keep, unspanned text and never-detect
# ---------------------------------------------------------------------------


def test_unspanned_identifier_passes_through_unchanged() -> None:
    text = "Ari Volk, Medicare 2123 45670 1, phone 0412 345 678, on 04/07/2023."
    result = _apply([_seg("s", text, [_span("n", text, "Ari Volk", "PERSON")])])
    assert result.segments[0]["text"] == (
        "[PERSON_1], Medicare 2123 45670 1, phone 0412 345 678, on 04/07/2023."
    )


def test_segment_without_spans_is_unchanged() -> None:
    text = "Plain synthetic text with Bo Teal."
    result = _apply([_seg("s", text)])
    assert result.segments == [{"segment_id": "s", "text": text}]
    assert result.spans == []
    assert result.merges == []


def test_apply_never_runs_detection(monkeypatch) -> None:
    import openmed
    from openmed.core import safety_sweep

    def boom(*args, **kwargs):
        raise AssertionError("detection must not run")

    monkeypatch.setattr(openmed, "analyze_text", boom)
    monkeypatch.setattr(safety_sweep, "safety_sweep", boom)
    text = "Ari Volk 0412 345 678"
    result = _apply([_seg("s", text, [_span("n", text, "Ari Volk", "PERSON")])])
    assert result.segments[0]["text"] == "[PERSON_1] 0412 345 678"


def test_keep_span_is_unchanged_but_reported() -> None:
    text = "Ari Volk, aged 42, female."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("n", text, "Ari Volk", "PERSON"),
                _span("a", text, "42", "AGE"),
                _span("g", text, "female", "GENDER"),
            ],
        )
    ]
    result = _apply(doc)
    assert result.segments[0]["text"] == "[PERSON_1], aged 42, female."
    assert _out(result, "a")["action"] == "keep"
    assert _sliced(result, "a") == "42"
    assert _out(result, "g")["action"] == "keep"
    assert _sliced(result, "g") == "female"


def test_policy_level_changes_action() -> None:
    text = "Lives in Randwick."
    doc = [_seg("s", text, [_span("l", text, "Randwick", "LOCATION")])]
    assert _apply(doc, policy="ccia_l1").segments[0]["text"] == text
    assert _apply(doc, policy="ccia_l2").segments[0]["text"] == "Lives in [LOCATION_1]."


def test_replacement_is_ignored_under_mark() -> None:
    text = "Ari Volk"
    span = _span("n", text, "Ari Volk", "PERSON", replacement="Someone Else")
    result = _apply([_seg("s", text, [span])])
    assert result.segments[0]["text"] == "[PERSON_1]"


# ---------------------------------------------------------------------------
# shift
# ---------------------------------------------------------------------------


def test_full_date_shifts_day_first_with_explicit_offset() -> None:
    text = "Admitted 04/07/2023."
    result = _apply(
        [_seg("s", text, [_span("d", text, "04/07/2023", "DATE")])], offset_days=10
    )
    assert result.segments[0]["text"] == "Admitted 14/07/2023."
    assert _out(result, "d")["action"] == "shift"
    assert _sliced(result, "d") == "14/07/2023"


@pytest.mark.parametrize(
    ("value", "offset", "expected"),
    [
        ("04/07/2023", 10, "14/07/2023"),
        ("4/7/2023", 10, "14/7/2023"),
        ("28/02/2024", 2, "01/03/2024"),
        ("04-07-2023", -5, "29-06-2023"),
        ("04.07.2023", 30, "03.08.2023"),
        ("04/07/23", 10, "14/07/23"),
        ("2023-07-04", 10, "2023-07-14"),
        ("4 July 2023", 10, "14 July 2023"),
        ("4th July 2023", 10, "14th July 2023"),
        ("21 Jul 2023", 1, "22 Jul 2023"),
        ("30 Jun 2023", 2, "2 Jul 2023"),
        ("1 JULY 2023", 1, "2 JULY 2023"),
        ("July 4, 2023", 10, "July 14, 2023"),
        ("04-Jul-2023", 10, "14-Jul-2023"),
        ("31 Dec 2023", 1, "1 Jan 2024"),
        ("1st March 2023", 1, "2nd March 2023"),
    ],
)
def test_full_date_formats_are_preserved(value, offset, expected) -> None:
    text = f"On {value} seen."
    result = _apply(
        [_seg("s", text, [_span("d", text, value, "DATE")])], offset_days=offset
    )
    assert result.segments[0]["text"] == f"On {expected} seen."
    assert _out(result, "d")["action"] == "shift"


def test_date_of_birth_is_tagged_under_mark_while_date_shifts() -> None:
    # spec.md Treatment fallbacks: "DOB -> tag under mark".
    text = "DOB 04/07/2023, seen 04/07/2023."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("b", text, "04/07/2023", "DATE_OF_BIRTH"),
                _span("d", text, "04/07/2023", "DATE", occurrence=1),
            ],
        )
    ]
    result = _apply(doc, offset_days=10)
    assert result.segments[0]["text"] == "DOB [DATE_OF_BIRTH_1], seen 14/07/2023."
    assert _out(result, "b")["action"] == "mark"
    assert _out(result, "d")["action"] == "shift"


def test_date_of_birth_alone_needs_no_offset_under_mark() -> None:
    text = "DOB 04/07/2023."
    result = _apply(
        [_seg("s", text, [_span("b", text, "04/07/2023", "DATE_OF_BIRTH")])]
    )
    assert result.segments[0]["text"] == "DOB [DATE_OF_BIRTH_1]."


def test_patient_key_and_secret_give_stable_offset() -> None:
    offset = stable_offset_for(SYNTHETIC_KEY, max_days=365, secret=SYNTHETIC_SECRET)
    assert offset != 0
    text = "Seen 2023-07-04."
    doc = [_seg("s", text, [_span("d", text, "2023-07-04", "DATE")])]
    keyed = _apply(doc, patient_key=SYNTHETIC_KEY, secret=SYNTHETIC_SECRET)
    explicit = _apply(doc, offset_days=offset)
    assert keyed.segments == explicit.segments
    assert keyed.segments[0]["text"] != text


def test_partial_and_unparseable_dates_share_the_date_counter() -> None:
    text = "In 2019, then July 2023, then 04/07/2023, then sometime-ish, then 2019."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("y", text, "2019", "DATE"),
                _span("my", text, "July 2023", "DATE"),
                _span("full", text, "04/07/2023", "DATE"),
                _span("junk", text, "sometime-ish", "DATE"),
                _span("y2", text, "2019", "DATE", occurrence=1),
            ],
        )
    ]
    result = _apply(doc, offset_days=10)
    assert result.segments[0]["text"] == (
        "In [DATE_1], then [DATE_2], then 14/07/2023, then [DATE_3], then [DATE_1]."
    )
    assert _out(result, "y")["action"] == "mark"
    assert _out(result, "my")["action"] == "mark"
    assert _out(result, "full")["action"] == "shift"


@pytest.mark.parametrize(
    "value",
    ["31/02/2023", "Tuesday 4 July 2023", "04/07/2023 10:30", "13/13/2023", "Q3 2023"],
)
def test_invalid_or_extended_dates_fall_back_to_tag(value) -> None:
    text = f"On {value}."
    result = _apply(
        [_seg("s", text, [_span("d", text, value, "DATE")])], offset_days=10
    )
    assert result.segments[0]["text"] == "On [DATE_1]."
    assert value not in result.segments[0]["text"]


def test_dob_fallback_uses_its_own_label_tag() -> None:
    text = "DOB 1980."
    result = _apply(
        [_seg("s", text, [_span("b", text, "1980", "DATE_OF_BIRTH")])],
        offset_days=10,
    )
    assert result.segments[0]["text"] == "DOB [DATE_OF_BIRTH_1]."


def test_shift_on_non_date_label_is_masked_never_passed_through() -> None:
    # The DIRECT_IDENTIFIER class floor is ``shift``: a direct identifier with
    # no explicit action resolves to ``shift``, which must render as a tag.
    base = load_policy("ccia_l1")
    actions = dict(base.actions)
    del actions["PERSON"]
    profile = dataclasses.replace(base, actions=actions)
    assert profile.action_for("PERSON") == "shift"

    text = "Ari Volk seen."
    result = _apply(
        [_seg("s", text, [_span("n", text, "Ari Volk", "PERSON")])],
        policy=profile,
        offset_days=10,
    )
    assert result.segments[0]["text"] == "[PERSON_1] seen."
    assert _out(result, "n")["action"] == "mark"


def test_shift_on_non_date_label_needs_no_offset() -> None:
    base = load_policy("ccia_l1")
    actions = dict(base.actions)
    del actions["PERSON"]
    profile = dataclasses.replace(base, actions=actions)
    text = "Ari Volk seen."
    result = _apply(
        [_seg("s", text, [_span("n", text, "Ari Volk", "PERSON")])], policy=profile
    )
    assert result.segments[0]["text"] == "[PERSON_1] seen."


def test_shift_needed_without_offset_or_key_raises() -> None:
    text = "Seen 04/07/2023."
    doc = [_seg("s", text, [_span("d", text, "04/07/2023", "DATE")])]
    with pytest.raises(ApplySpansError) as info:
        _apply(doc)
    assert info.value.error_code == "DATE_SHIFT_KEY_REQUIRED"


def test_unparseable_date_still_requires_offset() -> None:
    text = "In 2019."
    doc = [_seg("s", text, [_span("d", text, "2019", "DATE")])]
    with pytest.raises(ApplySpansError) as info:
        _apply(doc)
    assert info.value.error_code == "DATE_SHIFT_KEY_REQUIRED"


def test_no_shift_needed_means_no_offset_required() -> None:
    text = "Ari Volk"
    result = _apply([_seg("s", text, [_span("n", text, "Ari Volk", "PERSON")])])
    assert result.segments[0]["text"] == "[PERSON_1]"


# ---------------------------------------------------------------------------
# Reporting, merges, provenance
# ---------------------------------------------------------------------------


def test_out_offsets_slice_the_tag_after_length_changes() -> None:
    t1 = "Ari Volk met Bo Teal on 04/07/2023 at home."
    t2 = "A"
    doc = [
        _seg(
            "s1",
            t1,
            [
                _span("a", t1, "Ari Volk", "PERSON"),
                _span("b", t1, "Bo Teal", "PERSON"),
                _span("d", t1, "04/07/2023", "DATE"),
            ],
        ),
        _seg("s2", t2, [_span("c", t2, "A", "PERSON")]),
    ]
    result = _apply(doc, offset_days=10)
    assert _sliced(result, "a") == "[PERSON_1]"
    assert _sliced(result, "b") == "[PERSON_2]"
    assert _sliced(result, "d") == "14/07/2023"
    assert _sliced(result, "c") == "[PERSON_3]"
    assert _out(result, "c")["segment_id"] == "s2"


def test_overlapping_spans_merge_deterministically() -> None:
    text = "Dr Ari Volk Jr attended."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("inner", text, "Ari Volk", "PERSON"),
                _span("outer", text, "Dr Ari Volk", "PERSON"),
                _span("tail", text, "Volk Jr", "LAST_NAME"),
            ],
        )
    ]
    result = _apply(doc)
    # Earliest start wins; the cluster is rendered over its full extent so no
    # part of a merged span survives.
    assert result.segments[0]["text"] == "[PERSON_1] attended."
    assert [s["span_id"] for s in result.spans] == ["outer"]
    assert result.merges == [
        {"kept_span_id": "outer", "merged_span_ids": ["inner", "tail"]}
    ]


def test_merge_tiebreak_longest_then_strongest() -> None:
    text = "Ari Volk here."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("short", text, "Ari", "FIRST_NAME"),
                _span("keepish", text, "Ari Volk", "AGE"),
                _span("strong", text, "Ari Volk", "PERSON"),
            ],
        )
    ]
    result = _apply(doc)
    assert result.merges == [
        {"kept_span_id": "strong", "merged_span_ids": ["keepish", "short"]}
    ]
    assert result.segments[0]["text"] == "[PERSON_1] here."


def test_merge_never_leaks_a_redacted_member() -> None:
    # The kept span (earliest start) resolves to keep, but a merged member is
    # masked: the cluster renders with the strongest action.
    text = "Age 42 Ari Volk."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("age", text, "Age 42 Ari", "AGE"),
                _span("name", text, "Ari Volk", "PERSON"),
            ],
        )
    ]
    result = _apply(doc)
    assert "Ari" not in result.segments[0]["text"]
    assert "Volk" not in result.segments[0]["text"]
    assert _out(result, "age")["action"] == "mark"


def test_every_input_span_reported_exactly_once() -> None:
    text = "Dr Ari Volk Jr on 04/07/2023 and 2019, aged 42."
    spans = [
        _span("inner", text, "Ari Volk", "PERSON"),
        _span("outer", text, "Dr Ari Volk", "PERSON"),
        _span("d", text, "04/07/2023", "DATE"),
        _span("y", text, "2019", "DATE"),
        _span("a", text, "42", "AGE"),
    ]
    result = _apply([_seg("s", text, spans)], offset_days=3)
    reported = [s["span_id"] for s in result.spans]
    for merge in result.merges:
        reported.extend(merge["merged_span_ids"])
    assert sorted(reported) == sorted(s["span_id"] for s in spans)


def test_adjacent_spans_do_not_merge() -> None:
    text = "AriVolk"
    doc = [
        _seg(
            "s",
            text,
            [
                {"span_id": "f", "start": 0, "end": 3, "label": "FIRST_NAME"},
                {"span_id": "l", "start": 3, "end": 7, "label": "LAST_NAME"},
            ],
        )
    ]
    result = _apply(doc)
    assert result.segments[0]["text"] == "[PERSON_FN_1][PERSON_LN_1]"
    assert result.merges == []


def test_name_parts_render_as_person_part_tags_numbered_per_part() -> None:
    text = "Jane Citizen, John Q Smith, Mary Citizen, Jane, Ari Volk"
    doc = [
        _seg(
            "s",
            text,
            [
                _span("f1", text, "Jane", "FIRST_NAME"),
                _span("l1", text, "Citizen", "LAST_NAME"),
                _span("f2", text, "John", "FIRST_NAME"),
                _span("m2", text, "Q", "MIDDLE_NAME"),
                _span("l2", text, "Smith", "LAST_NAME"),
                _span("f3", text, "Mary", "FIRST_NAME"),
                _span("l3", text, "Citizen", "LAST_NAME", occurrence=1),
                _span("f4", text, "Jane", "FIRST_NAME", occurrence=1),
                _span("p5", text, "Ari Volk", "PERSON"),
            ],
        )
    ]
    result = _apply(doc)
    assert result.segments[0]["text"] == (
        "[PERSON_FN_1] [PERSON_LN_1], [PERSON_FN_2] [PERSON_MN_1] [PERSON_LN_2], "
        "[PERSON_FN_3] [PERSON_LN_1], [PERSON_FN_1], [PERSON_1]"
    )


def test_provenance_carries_version_and_fingerprint() -> None:
    result = _apply([_seg("s", "x")], policy="ccia_l2")
    assert result.provenance == {
        "openmed_version": __version__,
        "policy_fingerprint": policy_fingerprint("ccia_l2"),
    }


def test_result_contains_no_original_span_text() -> None:
    text = "Ari Volk on 04/07/2023 in 2019."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("n", text, "Ari Volk", "PERSON"),
                _span("d", text, "04/07/2023", "DATE"),
                _span("y", text, "2019", "DATE"),
            ],
        )
    ]
    result = _apply(doc, patient_key=SYNTHETIC_KEY, secret=SYNTHETIC_SECRET)
    blob = repr(result.to_dict())
    for value in ("Ari", "Volk", "04/07/2023", "2019", SYNTHETIC_KEY, SYNTHETIC_SECRET):
        assert value not in blob
    assert set(result.to_dict()) == {"segments", "spans", "merges", "provenance"}


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def _ok_doc():
    text = "Ari Volk"
    return [_seg("s", text, [_span("n", text, "Ari Volk", "PERSON")])]


@pytest.mark.parametrize(
    ("document", "kwargs", "code"),
    [
        ([], {}, "EMPTY_DOCUMENT"),
        ([_seg("s", "a"), _seg("s", "b")], {}, "DUPLICATE_SEGMENT_ID"),
        (
            [
                _seg(
                    "s",
                    "Ari",
                    [{"span_id": "x", "start": 0, "end": 4, "label": "PERSON"}],
                )
            ],
            {},
            "SPAN_OUT_OF_RANGE",
        ),
        (
            [
                _seg(
                    "s",
                    "Ari",
                    [{"span_id": "x", "start": -1, "end": 2, "label": "PERSON"}],
                )
            ],
            {},
            "SPAN_OUT_OF_RANGE",
        ),
        (
            [
                _seg(
                    "s",
                    "Ari",
                    [{"span_id": "x", "start": 2, "end": 1, "label": "PERSON"}],
                )
            ],
            {},
            "SPAN_INVERTED",
        ),
        (
            [
                _seg(
                    "s",
                    "Ari",
                    [{"span_id": "x", "start": 1, "end": 1, "label": "PERSON"}],
                )
            ],
            {},
            "SPAN_INVERTED",
        ),
        (
            [
                _seg(
                    "s",
                    "Ari",
                    [{"span_id": "x", "start": 0, "end": 3, "label": "NAME"}],
                )
            ],
            {},
            "UNKNOWN_LABEL",
        ),
        (
            [
                _seg(
                    "s",
                    "Ari",
                    [
                        {
                            "span_id": "x",
                            "start": 0,
                            "end": 3,
                            "label": "PERSON",
                            "segment_id": "other",
                        }
                    ],
                )
            ],
            {},
            "UNKNOWN_SEGMENT_ID",
        ),
        (
            [
                _seg(
                    "s",
                    "Ari Volk",
                    [
                        {"span_id": "x", "start": 0, "end": 3, "label": "PERSON"},
                        {"span_id": "x", "start": 4, "end": 8, "label": "PERSON"},
                    ],
                )
            ],
            {},
            "INVALID_REQUEST",
        ),
        (_ok_doc(), {"treatment": "generalise"}, "UNSUPPORTED_TREATMENT"),
        (_ok_doc(), {"treatment": "keep"}, "UNSUPPORTED_TREATMENT"),
        (_ok_doc(), {"treatment": "scramble"}, "UNSUPPORTED_TREATMENT"),
        (_ok_doc(), {"policy": "no_such_policy"}, "UNKNOWN_POLICY"),
        (_ok_doc(), {"offset_days": 0}, "INVALID_REQUEST"),
        (_ok_doc(), {"offset_days": True}, "INVALID_REQUEST"),
        (
            _ok_doc(),
            {
                "offset_days": 3,
                "patient_key": SYNTHETIC_KEY,
                "secret": SYNTHETIC_SECRET,
            },
            "INVALID_REQUEST",
        ),
        (_ok_doc(), {"patient_key": SYNTHETIC_KEY}, "INVALID_REQUEST"),
        (_ok_doc(), {"secret": SYNTHETIC_SECRET}, "INVALID_REQUEST"),
        (_ok_doc(), {"patient_key": "", "secret": SYNTHETIC_SECRET}, "INVALID_REQUEST"),
    ],
)
def test_fail_closed_errors(document, kwargs, code) -> None:
    params = {"treatment": "mark", "policy": "ccia_l1", **kwargs}
    with pytest.raises(ApplySpansError) as info:
        apply_spans(document, **params)
    error = info.value
    assert error.error_code == code
    assert isinstance(error, InputError)
    assert error.__cause__ is None
    message = str(error)
    for value in ("Ari", "Volk", SYNTHETIC_KEY, SYNTHETIC_SECRET):
        assert value not in message
    assert error.details == {"error_code": code}


def test_errors_and_logs_never_contain_secret_or_key(caplog) -> None:
    text = "Ari Volk on 04/07/2023."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("n", text, "Ari Volk", "PERSON"),
                _span("d", text, "04/07/2023", "DATE"),
            ],
        )
    ]
    with caplog.at_level(logging.DEBUG):
        _apply(doc, patient_key=SYNTHETIC_KEY, secret=SYNTHETIC_SECRET)
        for kwargs in (
            {"patient_key": SYNTHETIC_KEY},
            {"secret": SYNTHETIC_SECRET},
            {
                "patient_key": SYNTHETIC_KEY,
                "secret": SYNTHETIC_SECRET,
                "offset_days": 1,
            },
        ):
            with pytest.raises(ApplySpansError) as info:
                _apply(doc, **kwargs)
            rendered = repr(info.value) + str(info.value) + repr(info.value.details)
            assert SYNTHETIC_KEY not in rendered
            assert SYNTHETIC_SECRET not in rendered
    blob = caplog.text + "".join(str(r.args) for r in caplog.records)
    for value in (SYNTHETIC_KEY, SYNTHETIC_SECRET, "Ari Volk", "04/07/2023"):
        assert value not in blob


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", [("d", "b"), ("b", "d")])
def test_date_cluster_with_merged_dob_is_tagged_not_shifted(order) -> None:
    text = "04/07/2023"
    labels = {"d": "DATE", "b": "DATE_OF_BIRTH"}
    spans = [
        {"span_id": sid, "start": 0, "end": 10, "label": labels[sid]} for sid in order
    ]
    result = _apply([_seg("s", text, spans)], offset_days=10)
    rendered = result.segments[0]["text"]
    assert rendered.startswith("[") and rendered.endswith("_1]")
    assert "2023" not in rendered
    assert result.spans[0]["action"] == "mark"
    assert len(result.merges) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [("04/7/2023", "14/7/2023"), ("4/07/2023", "14/07/2023")],
)
def test_numeric_day_and_month_keep_their_own_width(value, expected) -> None:
    text = f"On {value}."
    result = _apply(
        [_seg("s", text, [_span("d", text, value, "DATE")])], offset_days=10
    )
    assert result.segments[0]["text"] == f"On {expected}."


@pytest.mark.parametrize(
    "value",
    [
        "٠٤/٠٧/٢٠٢٣",
        "٤ July ٢٠٢٣",
        "０４/０７/２０２３",
    ],
)
def test_non_ascii_digit_dates_fall_back_to_tag(value) -> None:
    text = f"On {value}."
    result = _apply(
        [_seg("s", text, [_span("d", text, value, "DATE")])], offset_days=10
    )
    assert result.segments[0]["text"] == "On [DATE_1]."
    assert _out(result, "d")["action"] == "mark"
