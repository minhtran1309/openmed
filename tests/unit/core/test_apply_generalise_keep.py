"""Tests for the ``generalise`` and ``keep`` apply treatments (mt.3 ticket 10).

Each matrix row asserts both the rendered text and the reported ``action``,
so every fallback reports what was actually rendered (mt.3 E2, E12). Every
value is synthetic: names are invented, the Medicare number comes from the
repo validator fixtures, phones are from the ACMA fictional ranges, and the
patient keys and secrets are test-only material. OncoTree names are
terminology, not PHI.
"""

from __future__ import annotations

import pytest

from openmed.core.apply_spans import (
    CANCER_LABELS,
    ONCOTREE_LOOKUP_LABELS,
    SUPPORTED_TREATMENTS,
    TREATMENTS,
    ApplySpansError,
    apply_spans,
)

OFFSET = 10
PATIENT_KEY = "synthetic-patient-key-generalise-41"
SECRET = "synthetic-secret-material-generalise"
REPLACEMENT = "Synthetic Group Wording"


def _span(span_id, text, value, label, **extra):
    start = text.index(value)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(value),
        "label": label,
        **extra,
    }


def _render_one(
    label, source, treatment, *, policy="ccia_l3", keyed=False, **span_extra
):
    text = f"Note: {source}."
    document = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [_span("x1", text, source, label, **span_extra)],
        }
    ]
    shift = (
        {"patient_key": PATIENT_KEY, "secret": SECRET}
        if keyed
        else {"offset_days": OFFSET}
    )
    result = apply_spans(document, treatment=treatment, policy=policy, **shift)
    (out,) = result.spans
    rendered = result.segments[0]["text"][out["out_start"] : out["out_end"]]
    return rendered, out["action"]


def test_supported_treatments_are_the_full_vocabulary() -> None:
    assert (
        SUPPORTED_TREATMENTS == TREATMENTS == ("mark", "replace", "generalise", "keep")
    )
    assert CANCER_LABELS == frozenset({"CANCER"})
    assert ONCOTREE_LOOKUP_LABELS == frozenset({"DISEASE", "CONDITION"})


# (id, label, source, span extras, policy, generalise result, keep result)
MATRIX = [
    # Names, IDs and email: tag under both (ticket 10 row 1).
    (
        "person",
        "PERSON",
        "Zelphira Quandt",
        {},
        "ccia_l3",
        ("[PERSON_1]", "mark"),
        ("[PERSON_1]", "mark"),
    ),
    (
        "first-name",
        "FIRST_NAME",
        "Zelphira",
        {},
        "ccia_l3",
        ("[PERSON_FN_1]", "mark"),
        ("[PERSON_FN_1]", "mark"),
    ),
    (
        "medicare",
        "MEDICARE_NUMBER",
        "3664 73786 3",
        {},
        "ccia_l3",
        ("[MEDICARE_NUMBER_1]", "mark"),
        ("[MEDICARE_NUMBER_1]", "mark"),
    ),
    (
        "id-num",
        "ID_NUM",
        "UR448812",
        {},
        "ccia_l3",
        ("[ID_NUM_1]", "mark"),
        ("[ID_NUM_1]", "mark"),
    ),
    (
        "email",
        "EMAIL",
        "zq.synthetic@example.com",
        {},
        "ccia_l3",
        ("[EMAIL_1]", "mark"),
        ("[EMAIL_1]", "mark"),
    ),
    (
        "url",
        "URL",
        "https://example.com/zq",
        {},
        "ccia_l3",
        ("[URL_1]", "mark"),
        ("[URL_1]", "mark"),
    ),
    # Phone: format-preserving under generalise, tag under keep.
    (
        "mobile",
        "PHONE",
        "0491 570 156",
        {},
        "ccia_l3",
        ("04xx xxx xxx", "generalise"),
        ("[PHONE_1]", "mark"),
    ),
    (
        "landline",
        "PHONE",
        "(02) 5550 1234",
        {},
        "ccia_l3",
        ("(02) xxxx xxxx", "generalise"),
        ("[PHONE_1]", "mark"),
    ),
    (
        "phone-not-au",
        "PHONE",
        "555-0199",
        {},
        "ccia_l3",
        ("[PHONE_1]", "mark"),
        ("[PHONE_1]", "mark"),
    ),
    # Full dates shift under both.
    (
        "date",
        "DATE",
        "04/07/2023",
        {},
        "ccia_l3",
        ("14/07/2023", "shift"),
        ("14/07/2023", "shift"),
    ),
    # DOB: the shifted year under generalise (28/12/1960 + 10 days is in
    # 1961, so the year shown is the shifted one), shifted under keep.
    (
        "dob",
        "DATE_OF_BIRTH",
        "28/12/1960",
        {},
        "ccia_l3",
        ("1961", "generalise"),
        ("07/01/1961", "shift"),
    ),
    (
        "dob-unparseable",
        "DATE_OF_BIRTH",
        "March 1961",
        {},
        "ccia_l3",
        ("[DATE_OF_BIRTH_1]", "mark"),
        ("[DATE_OF_BIRTH_1]", "mark"),
    ),
    (
        "date-year-only",
        "DATE",
        "2019",
        {},
        "ccia_l3",
        ("[DATE_1]", "mark"),
        ("[DATE_1]", "mark"),
    ),
    # Address / location: suburb and state under generalise, tag under keep.
    (
        "address",
        "STREET_ADDRESS",
        "12 Smith St, Carlton VIC 3053",
        {},
        "ccia_l3",
        ("Carlton VIC", "generalise"),
        ("[STREET_ADDRESS_1]", "mark"),
    ),
    (
        "address-no-state",
        "STREET_ADDRESS",
        "12 Smith St, Carlton",
        {},
        "ccia_l3",
        ("[STREET_ADDRESS_1]", "mark"),
        ("[STREET_ADDRESS_1]", "mark"),
    ),
    (
        "location-state",
        "LOCATION",
        "VIC",
        {},
        "ccia_l3",
        ("VIC", "generalise"),
        ("[LOCATION_1]", "mark"),
    ),
    (
        "zipcode",
        "ZIPCODE",
        "3053",
        {},
        "ccia_l3",
        ("[ZIPCODE_1]", "mark"),
        ("[ZIPCODE_1]", "mark"),
    ),
    # Organisation: its kind under generalise, tag under keep.
    (
        "organisation",
        "ORGANIZATION",
        "Royal Synthetic Hospital",
        {},
        "ccia_l3",
        ("hospital", "generalise"),
        ("[ORGANIZATION_1]", "mark"),
    ),
    (
        "organisation-unknown",
        "ORGANIZATION",
        "Acme Widgets",
        {},
        "ccia_l3",
        ("[ORGANIZATION_1]", "mark"),
        ("[ORGANIZATION_1]", "mark"),
    ),
    # Policy keep wins over the generalisers (E2 row 2): ccia_l1 keeps
    # organisations.
    (
        "organisation-policy-keep",
        "ORGANIZATION",
        "Royal Synthetic Hospital",
        {},
        "ccia_l1",
        ("Royal Synthetic Hospital", "keep"),
        ("Royal Synthetic Hospital", "keep"),
    ),
    # A caller replacement: that text under generalise. Under keep only a
    # non-date quasi-identifier is kept (user ruling, fix round 1); direct
    # identifiers are marked and dates shift.
    (
        "replacement",
        "PERSON",
        "Zelphira Quandt",
        {"replacement": REPLACEMENT},
        "ccia_l3",
        (REPLACEMENT, "replacement"),
        ("[PERSON_1]", "mark"),
    ),
    (
        "replacement-dob",
        "DATE_OF_BIRTH",
        "28/12/1960",
        {"replacement": REPLACEMENT},
        "ccia_l3",
        (REPLACEMENT, "replacement"),
        ("07/01/1961", "shift"),
    ),
    (
        "replacement-location",
        "LOCATION",
        "Quokkaville",
        {"replacement": REPLACEMENT},
        "ccia_l3",
        (REPLACEMENT, "replacement"),
        ("Quokkaville", "keep"),
    ),
    # Ages keep their policy action; no 90+ grouping (ticket 10 Decisions).
    ("age", "AGE", "93", {}, "ccia_l3", ("93", "keep"), ("93", "keep")),
    ("age-l1", "AGE", "93", {}, "ccia_l1", ("93", "keep"), ("93", "keep")),
]


def _matrix_params(treatment):
    column = 5 if treatment == "generalise" else 6
    return [
        pytest.param(row[1], row[2], row[3], row[4], row[column], id=row[0])
        for row in MATRIX
    ]


@pytest.mark.parametrize(
    ("label", "source", "extra", "policy", "expected"),
    _matrix_params("generalise"),
)
def test_generalise_matrix(label, source, extra, policy, expected) -> None:
    assert _render_one(label, source, "generalise", policy=policy, **extra) == (
        expected
    )


@pytest.mark.parametrize(
    ("label", "source", "extra", "policy", "expected"),
    _matrix_params("keep"),
)
def test_keep_matrix(label, source, extra, policy, expected) -> None:
    assert _render_one(label, source, "keep", policy=policy, **extra) == expected


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("PERSON", "Zelphira Quandt", ("[PERSON_1]", "mark")),
        ("FIRST_NAME", "Zelphira", ("[PERSON_FN_1]", "mark")),
        ("MEDICARE_NUMBER", "3664 73786 3", ("[MEDICARE_NUMBER_1]", "mark")),
        ("PHONE", "0491 570 156", ("[PHONE_1]", "mark")),
        (
            "STREET_ADDRESS",
            "12 Smith St, Carlton VIC 3053",
            ("[STREET_ADDRESS_1]", "mark"),
        ),
        ("DATE_OF_BIRTH", "28/12/1960", ("07/01/1961", "shift")),
        ("DATE", "04/07/2023", ("14/07/2023", "shift")),
        ("LOCATION", "Quokkaville", ("Quokkaville", "keep")),
        ("ZIPCODE", "3053", ("3053", "keep")),
        ("ORGANIZATION", "Acme Widgets", ("Acme Widgets", "keep")),
    ],
)
def test_keep_replacement_keeps_only_non_date_quasi_identifiers(
    label, source, expected
) -> None:
    """Under keep, a replacement keeps a quasi-identifier but never un-redacts a
    direct identifier or an unshifted date (user ruling, fix round 1)."""
    assert _render_one(label, source, "keep", replacement=REPLACEMENT) == expected


@pytest.mark.parametrize("treatment", ["generalise", "keep"])
def test_merged_cluster_ignores_replacement(treatment) -> None:
    text = "Seen by Zelphira Quandt today."
    document = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [
                _span("n1", text, "Zelphira Quandt", "PERSON", replacement=REPLACEMENT),
                _span("n2", text, "Quandt", "LAST_NAME"),
            ],
        }
    ]
    result = apply_spans(document, treatment=treatment, policy="ccia_l3")
    assert result.segments[0]["text"] == "Seen by [PERSON_1] today."
    assert result.spans[0]["action"] == "mark"
    assert result.merges == [{"kept_span_id": "n1", "merged_span_ids": ["n2"]}]


def test_mixed_label_cluster_is_tagged_under_generalise() -> None:
    text = "Call 0491 570 156 now."
    document = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [
                _span("p1", text, "0491 570 156", "PHONE"),
                _span("n1", text, "570", "PERSON"),
            ],
        }
    ]
    result = apply_spans(document, treatment="generalise", policy="ccia_l3")
    assert result.segments[0]["text"] == "Call [PHONE_1] now."
    assert result.spans[0]["action"] == "mark"


def test_numbering_shared_with_fallback_tags() -> None:
    text = "Royal Synthetic Hospital, Acme Widgets, Acme Widgets."
    document = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [
                _span("o1", text, "Royal Synthetic Hospital", "ORGANIZATION"),
                {"span_id": "o2", "start": 26, "end": 38, "label": "ORGANIZATION"},
                {"span_id": "o3", "start": 40, "end": 52, "label": "ORGANIZATION"},
            ],
        }
    ]
    result = apply_spans(document, treatment="generalise", policy="ccia_l3")
    assert result.segments[0]["text"] == (
        "hospital, [ORGANIZATION_1], [ORGANIZATION_1]."
    )
    assert [s["action"] for s in result.spans] == ["generalise", "mark", "mark"]


# ---------------------------------------------------------------------------
# OncoTree (mt.3 E11)
# ---------------------------------------------------------------------------


def test_cancer_subtype_generalised_to_parent() -> None:
    # Lung Adenocarcinoma (LUAD, level 3) -> its level-2 ancestor.
    assert _render_one("CANCER", "Lung Adenocarcinoma", "generalise") == (
        "Non-Small Cell Lung Cancer",
        "generalise",
    )


def test_cancer_code_generalised_for_cancer_label() -> None:
    assert _render_one("CANCER", "LUAD", "generalise") == (
        "Non-Small Cell Lung Cancer",
        "generalise",
    )


def test_cancer_main_type_generalised_to_tissue() -> None:
    assert _render_one("CANCER", "Melanoma", "generalise") == ("Skin", "generalise")


@pytest.mark.parametrize("label", ["DISEASE", "CONDITION"])
def test_disease_resolving_in_oncotree_is_generalised(label) -> None:
    assert _render_one(label, "Lung Adenocarcinoma", "generalise") == (
        "Non-Small Cell Lung Cancer",
        "generalise",
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("DISEASE", "asthma"),
        ("CONDITION", "asthma"),
        # Codes match only for CANCER (E11).
        ("DISEASE", "LUAD"),
        # A level-1 tissue name is not a cancer.
        ("DISEASE", "Lung"),
    ],
)
def test_non_cancer_disease_not_generalised(label, source) -> None:
    assert _render_one(label, source, "generalise") == (source, "keep")


def test_unmapped_cancer_falls_back_to_mark() -> None:
    assert _render_one("CANCER", "Synthetic Mystery Neoplasm", "generalise") == (
        "[CANCER_1]",
        "mark",
    )


@pytest.mark.parametrize("label", ["CANCER", "DISEASE"])
def test_ambiguous_cancer_falls_back_to_mark(label) -> None:
    # ``Choriocarcinoma`` names nodes under several tissues: a cancer, but
    # with no single generalisation, so it is tagged, never kept.
    assert _render_one(label, "Choriocarcinoma", "generalise") == (
        f"[{label}_1]",
        "mark",
    )


@pytest.mark.parametrize("label", ["CANCER", "DISEASE"])
def test_caller_replacement_beats_oncotree(label) -> None:
    assert _render_one(
        label, "Lung Adenocarcinoma", "generalise", replacement=REPLACEMENT
    ) == (REPLACEMENT, "replacement")


@pytest.mark.parametrize("treatment", ["mark", "replace", "keep"])
@pytest.mark.parametrize(
    "source", ["Lung Adenocarcinoma", "Synthetic Mystery Neoplasm", "Choriocarcinoma"]
)
def test_cancer_kept_under_other_treatments(treatment, source) -> None:
    assert _render_one("CANCER", source, treatment, keyed=True) == (source, "keep")


def test_cancer_generalised_in_document_context() -> None:
    text = "Dx: Lung Adenocarcinoma, asthma; Synthetic Mystery Neoplasm."
    document = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [
                _span("c1", text, "Lung Adenocarcinoma", "CANCER"),
                _span("c2", text, "asthma", "DISEASE"),
                _span("c3", text, "Synthetic Mystery Neoplasm", "CANCER"),
            ],
        }
    ]
    result = apply_spans(document, treatment="generalise", policy="ccia_l1")
    assert result.segments[0]["text"] == (
        "Dx: Non-Small Cell Lung Cancer, asthma; [CANCER_1]."
    )
    assert [s["action"] for s in result.spans] == ["generalise", "keep", "mark"]


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("treatment", ["generalise", "keep"])
def test_generalise_needs_key_only_for_shift(treatment) -> None:
    text = "Zelphira Quandt, 0491 570 156, Royal Synthetic Hospital."
    no_dates = [
        {
            "segment_id": "s1",
            "text": text,
            "spans": [
                _span("n1", text, "Zelphira Quandt", "PERSON"),
                _span("p1", text, "0491 570 156", "PHONE"),
                _span("o1", text, "Royal Synthetic Hospital", "ORGANIZATION"),
            ],
        }
    ]
    result = apply_spans(no_dates, treatment=treatment, policy="ccia_l3")
    assert result.segments[0]["text"].startswith("[PERSON_1], ")

    for label in ("DATE", "DATE_OF_BIRTH"):
        dated_text = "Seen 28/12/1960."
        dated = [
            {
                "segment_id": "s1",
                "text": dated_text,
                "spans": [_span("d1", dated_text, "28/12/1960", label)],
            }
        ]
        with pytest.raises(ApplySpansError) as excinfo:
            apply_spans(dated, treatment=treatment, policy="ccia_l3")
        assert excinfo.value.error_code == "DATE_SHIFT_KEY_REQUIRED"
        keyed = apply_spans(
            dated,
            treatment=treatment,
            policy="ccia_l3",
            patient_key=PATIENT_KEY,
            secret=SECRET,
        )
        assert keyed.spans[0]["action"] in {"shift", "generalise"}


def test_dob_generalise_is_deterministic_per_patient() -> None:
    first = _render_one("DATE_OF_BIRTH", "28/12/1960", "generalise", keyed=True)
    second = _render_one("DATE_OF_BIRTH", "28/12/1960", "generalise", keyed=True)
    assert first == second
    assert first[1] == "generalise"
    assert first[0].isdigit() and len(first[0]) == 4


def test_scramble_still_unsupported() -> None:
    with pytest.raises(ApplySpansError) as excinfo:
        apply_spans(
            [{"segment_id": "s", "text": "x", "spans": []}],
            treatment="scramble",
            policy="ccia_l3",
        )
    assert excinfo.value.error_code == "UNSUPPORTED_TREATMENT"


# ---------------------------------------------------------------------------
# Final review M3: a replacement containing its source is a tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "source", "replacement"),
    [
        ("PERSON", "Jane Citizen", "Ms Jane Citizen (pt)"),
        ("LOCATION", "Quokkaville Reserve", "near QUOKKAVILLE  reserve"),
        ("PHONE", "0491 570 156", "ph 0491570156"),
    ],
)
def test_generalise_replacement_containing_source_renders_mark(
    label, source, replacement
) -> None:
    rendered, action = _render_one(
        label, source, "generalise", keyed=True, replacement=replacement
    )
    assert (rendered, action) == (f"[{label}_1]", "mark")


@pytest.mark.parametrize(
    ("label", "source", "replacement", "expected"),
    [
        # A direct identifier ignores the replacement and is tagged.
        ("PERSON", "Jane Citizen", "Ms Jane Citizen (pt)", ("[PERSON_1]", "mark")),
        # A quasi-identifier keeps its source by the keep ruling; the
        # replacement text is never rendered.
        (
            "LOCATION",
            "Quokkaville Reserve",
            "near Quokkaville Reserve",
            ("Quokkaville Reserve", "keep"),
        ),
    ],
)
def test_keep_replacement_containing_source_never_renders_replacement(
    label, source, replacement, expected
) -> None:
    rendered, action = _render_one(
        label, source, "keep", keyed=True, replacement=replacement
    )
    assert (rendered, action) == expected
