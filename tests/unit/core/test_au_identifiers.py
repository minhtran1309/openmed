"""Tests for Australian checksum identifiers and the ``subtype`` metadata key.

Every identifier here is synthetic: either a published self-test value or a
value whose check digits are computed by the reference helpers below. None is
asserted to belong to a real person or organisation.
"""

from __future__ import annotations

import re

import pytest

from openmed.core.au_identifiers import (
    AU_IDENTIFIER_PII_PATTERNS,
    validate_australian_abn,
    validate_australian_acn,
    validate_australian_ihi,
)
from openmed.core.pii import _pii_merge_metadata, extract_pii
from openmed.core.pii_entity_merger import (
    PIIPattern,
    merge_entities_with_semantic_units,
)
from openmed.core.pii_i18n import LOCALE_PII_PATTERNS, get_patterns_for_language
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult

# ---------------------------------------------------------------------------
# Reference check-digit helpers (independent of the implementation under test)
# ---------------------------------------------------------------------------

_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def _abn_ok(digits: str) -> bool:
    values = [int(d) for d in digits]
    values[0] -= 1
    return sum(w * v for w, v in zip(_ABN_WEIGHTS, values)) % 89 == 0


def _acn_check(first_eight: str) -> str:
    total = sum(w * int(d) for w, d in zip(range(8, 0, -1), first_eight))
    return str((10 - total % 10) % 10)


def _luhn_check(body: str) -> str:
    total = 0
    for index, char in enumerate(reversed(body)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def _ihi(serial: str) -> str:
    body = "800360" + serial
    return body + _luhn_check(body)


def _flip_last(value: str) -> str:
    digits = value.replace(" ", "")
    flipped = digits[:-1] + str((int(digits[-1]) + 1) % 10)
    return flipped


# Self-test values named by the ticket.
SELFTEST_ABN = "51 824 753 556"
SELFTEST_MEDICARE = "2123 45670 1"

VALID_ACN = "123 456 780"  # ACN-valid, deliberately not TFN-valid.
VALID_ACN_TFN_COLLISION = "000 000 019"  # Valid as ACN *and* as TFN.
VALID_IHI = _ihi("123456789")
VALID_IHI_SPACED = " ".join(VALID_IHI[i : i + 4] for i in range(0, 16, 4))
VALID_TFN = "123 456 782"
# ABN-valid, and its last nine digits are the valid TFN above.
ABN_WITH_TFN_TAIL = "62 123 456 782"

assert _abn_ok(SELFTEST_ABN.replace(" ", ""))
assert _abn_ok(ABN_WITH_TFN_TAIL.replace(" ", ""))
assert _acn_check(VALID_ACN.replace(" ", "")[:8]) == VALID_ACN[-1]


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


def _extract(text: str, *, locale: str | None = "en_AU"):
    return extract_pii(
        text,
        model_name="fixture-pii-model",
        lang="en",
        locale=locale,
    ).entities


# Raw pattern label and canonical label per AU subtype.
_RAW_LABELS = {
    "AU_ABN": "abn",
    "AU_ACN": "acn",
    "AU_IHI": "ihi",
    "AU_MEDICARE": "medicare_number",
    "AU_TFN": "tfn",
}
_CANONICAL_LABELS = {
    "AU_ABN": "ABN",
    "AU_ACN": "ACN",
    "AU_IHI": "IHI",
    "AU_MEDICARE": "MEDICARE_NUMBER",
    "AU_TFN": "TFN",
}


def _au_findings(entities):
    return [
        entity
        for entity in entities
        if (entity.metadata or {}).get("subtype", "").startswith("AU_")
    ]


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestValidators:
    @pytest.mark.parametrize("value", [SELFTEST_ABN, "51824753556", "51 824 753 556"])
    def test_abn_accepts_valid(self, value):
        assert validate_australian_abn(value)

    @pytest.mark.parametrize(
        "value",
        [
            "51 824 753 557",  # check fails
            "12345678901",  # random 11 digits
            "5182475355",  # 10 digits
            "518247535560",  # 12 digits
            "01 824 753 556",  # leading zero
            "ABN 51 824 753 556",  # decorated
        ],
    )
    def test_abn_rejects_invalid(self, value):
        assert not validate_australian_abn(value)

    @pytest.mark.parametrize("value", [VALID_ACN, "123456780", "000 000 019"])
    def test_acn_accepts_valid(self, value):
        assert validate_australian_acn(value)

    @pytest.mark.parametrize(
        "value", ["123 456 781", "000 000 000", "12345678", "1234567801", "ACN1"]
    )
    def test_acn_rejects_invalid(self, value):
        assert not validate_australian_acn(value)

    @pytest.mark.parametrize(
        "value",
        [VALID_IHI, VALID_IHI_SPACED, f"{VALID_IHI[:6]} {VALID_IHI[6:]}"],
    )
    def test_ihi_accepts_valid(self, value):
        assert validate_australian_ihi(value)

    @pytest.mark.parametrize(
        "value",
        [
            _flip_last(VALID_IHI),  # Luhn fails
            "8003611234567890",  # wrong prefix
            "4111111111111111",  # Luhn-valid card, not an IHI prefix
            VALID_IHI[:-1],  # 15 digits
        ],
    )
    def test_ihi_rejects_invalid(self, value):
        assert not validate_australian_ihi(value)


def test_subtype_field_defaults_to_none():
    assert PIIPattern(r"\d", "x").subtype is None


def test_au_patterns_registered_only_for_en_au():
    subtypes = {pattern.subtype for pattern in LOCALE_PII_PATTERNS["en_au"]}
    assert {"AU_ABN", "AU_ACN", "AU_IHI", "AU_MEDICARE", "AU_TFN"} <= subtypes
    for pattern in AU_IDENTIFIER_PII_PATTERNS:
        assert pattern in LOCALE_PII_PATTERNS["en_au"]
    for lang, locale in (("en", None), ("en", "en_IN"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale=locale)
        assert all(pattern.subtype is None for pattern in patterns), (lang, locale)


# ---------------------------------------------------------------------------
# Detection table: extract_pii and the safety sweep
# ---------------------------------------------------------------------------

# (subtype, context sentence template, value, bare-score)
_POSITIVE_TABLE = [
    ("AU_ABN", "Practice ABN: {v} on invoice.", SELFTEST_ABN, 0.45),
    ("AU_ABN", "Practice ABN: {v} on invoice.", "51824753556", 0.45),
    ("AU_ACN", "Company ACN {v} registered.", VALID_ACN, 0.45),
    ("AU_ACN", "Company ACN {v} registered.", "123456780", 0.45),
    ("AU_ACN", "Company ACN {v} registered.", VALID_ACN_TFN_COLLISION, None),
    ("AU_IHI", "Patient IHI {v} confirmed.", VALID_IHI, 0.85),
    ("AU_IHI", "Patient IHI {v} confirmed.", VALID_IHI_SPACED, 0.85),
    (
        "AU_IHI",
        "Patient IHI {v} confirmed.",
        f"{VALID_IHI[:4]} {VALID_IHI[4:10]} {VALID_IHI[10:]}",
        0.85,
    ),
    ("AU_MEDICARE", "Medicare number {v} on card.", SELFTEST_MEDICARE, 0.45),
    ("AU_MEDICARE", "Medicare number {v} on card.", "2123456701", 0.45),
    ("AU_MEDICARE", "Medicare number {v} on card.", "2123 45670 1 / 2", 0.45),
    ("AU_MEDICARE", "Medicare number {v} on card.", "2123456701-2", 0.45),
    ("AU_TFN", "Tax file number {v} lodged.", VALID_TFN, 0.45),
    ("AU_TFN", "Tax file number {v} lodged.", "123456782", 0.45),
]

_BARE_TEMPLATE = "Reference {v} was noted in the letter."


def _single_au_finding(entities, text, value):
    findings = _au_findings(entities)
    assert len(findings) == 1, [(e.text, e.metadata) for e in entities]
    finding = findings[0]
    start = text.index(value)
    assert (finding.start, finding.end) == (start, start + len(value))
    assert finding.text == value
    assert finding.label == _RAW_LABELS[finding.metadata["subtype"]]
    return finding


@pytest.mark.usefixtures("no_model")
class TestExtractPii:
    @pytest.mark.parametrize(("subtype", "template", "value", "bare"), _POSITIVE_TABLE)
    def test_valid_with_context_matches_value_only(
        self, subtype, template, value, bare
    ):
        text = template.format(v=value)
        finding = _single_au_finding(_extract(text), text, value)
        assert finding.metadata["subtype"] == subtype
        assert finding.confidence >= 0.9

    @pytest.mark.parametrize(("subtype", "template", "value", "bare"), _POSITIVE_TABLE)
    def test_valid_without_context_is_tagged_at_base_score(
        self, subtype, template, value, bare
    ):
        if bare is None:
            pytest.skip("context-dependent subtype; covered separately")
        text = _BARE_TEMPLATE.format(v=value)
        finding = _single_au_finding(_extract(text), text, value)
        assert finding.metadata["subtype"] == subtype
        assert finding.confidence == pytest.approx(bare)

    @pytest.mark.parametrize(
        ("template", "value"),
        [
            (_BARE_TEMPLATE, "51 824 753 557"),
            (_BARE_TEMPLATE, "123 456 781"),
            (_BARE_TEMPLATE, _flip_last(VALID_IHI)),
            (_BARE_TEMPLATE, "2123 45671 1"),
            (_BARE_TEMPLATE, "123 456 783"),
        ],
    )
    def test_invalid_checksum_is_not_tagged(self, template, value):
        text = template.format(v=value)
        entities = _extract(text)
        assert _au_findings(entities) == []
        au_labels = {"national_id", *_RAW_LABELS.values()}
        assert all(entity.label not in au_labels for entity in entities)

    @pytest.mark.parametrize(
        ("template", "value", "label"),
        [
            ("Medicare number {v} on card.", "2123 45671 1", "medicare_number"),
            ("Medicare: {v} on card.", "3123456789", "medicare_number"),
            ("Tax file number {v} lodged.", "123 456 783", "tfn"),
            ("TFN {v}.", "123 456 783", "tfn"),
            ("Practice ABN: {v} on invoice.", "51 824 753 557", "abn"),
            # Unspaced: also Medicare+IRN shaped; ABN claims the whole value.
            ("Practice ABN: {v} on invoice.", "51824753557", "abn"),
            # The tail "123 456 782" is a valid TFN; it is not split off.
            ("ABN {v} on invoice.", "98 123 456 782", "abn"),
            ("Company ACN {v} registered.", "123 456 781", "acn"),
            # TFN-valid but ACN-invalid under an ACN label: one unvalidated
            # unit, no subtype claimed by either pattern.
            ("Company ACN {v} registered.", "123 456 782", "acn"),
            ("Patient IHI {v} confirmed.", _flip_last(VALID_IHI), "ihi"),
            ("Patient IHI {v} confirmed.", "8003 6012 3456 7890", "ihi"),
        ],
    )
    def test_labelled_invalid_value_is_kept_without_subtype(
        self, template, value, label
    ):
        # A labelled checksum failure is likely a typo or OCR error of a real
        # identifier: it stays tagged (reduced score, unvalidated) so it is
        # still redacted, but it never claims an AU subtype. Exactly one unit
        # covers the value: no overlapping duplicates from other AU patterns.
        text = template.format(v=value)
        entities = _extract(text)
        assert _au_findings(entities) == []
        start, end = text.index(value), text.index(value) + len(value)
        overlapping = [e for e in entities if e.start < end and e.end > start]
        assert len(overlapping) == 1, [(e.text, e.label) for e in overlapping]
        kept = overlapping[0]
        # The context-named AU label is kept so the value is still redacted.
        assert (kept.start, kept.end, kept.label) == (start, end, label)
        assert kept.confidence < 0.45
        sweep = safety_sweep(text, [], lang="en", locale="en_AU")
        assert _au_findings(sweep) == []

    @pytest.mark.parametrize(
        "text",
        [
            "Medicare number 9 2123 45670 1 on card.",
            "Medicare number 2123 45670 1 23 on card.",
            "Medicare number 12123456701 99 on card.",
        ],
    )
    def test_medicare_is_not_carved_from_a_longer_digit_run(self, text):
        assert _au_findings(_extract(text)) == []

    @pytest.mark.parametrize(
        "value",
        ["2123 45670 1 / 2", "2123 45670 1/2", "2123456701-2", "2123 45670 1 2"],
    )
    def test_medicare_irn_forms_still_match_with_run_guards(self, value):
        text = f"Medicare number {value} on card."
        finding = _single_au_finding(_extract(text), text, value)
        assert finding.metadata["subtype"] == "AU_MEDICARE"

    def test_acn_tfn_collision_is_resolved_by_context(self):
        value = VALID_ACN_TFN_COLLISION
        acn_text = f"Company ACN {value} registered."
        tfn_text = f"Tax file number {value} lodged."
        bare_text = _BARE_TEMPLATE.format(v=value)
        acn = _single_au_finding(_extract(acn_text), acn_text, value)
        tfn = _single_au_finding(_extract(tfn_text), tfn_text, value)
        bare = _single_au_finding(_extract(bare_text), bare_text, value)
        assert acn.metadata["subtype"] == "AU_ACN"
        assert tfn.metadata["subtype"] == "AU_TFN"
        # The more sensitive personal identifier wins when nothing says ACN.
        assert bare.metadata["subtype"] == "AU_TFN"

    @pytest.mark.parametrize(
        "number",
        [
            ABN_WITH_TFN_TAIL,  # ABN-valid: the whole value is the ABN
            "98 123 456 782",  # not an ABN: nothing is tagged at all
        ],
    )
    def test_spaced_number_is_not_split_into_a_tfn_fragment(self, number):
        # The trailing "123 456 782" alone is a valid TFN. It must never be
        # carved out of the longer number, even with TFN context nearby.
        text = f"TFN on file. Number {number}."
        for entities in (
            _extract(text),
            safety_sweep(text, [], lang="en", locale="en_AU"),
        ):
            findings = _au_findings(entities)
            assert all(f.metadata["subtype"] != "AU_TFN" for f in findings)
            if number == ABN_WITH_TFN_TAIL:
                finding = _single_au_finding(entities, text, number)
                assert finding.metadata["subtype"] == "AU_ABN"
            else:
                assert findings == []

    def test_self_test_values(self):
        text = f"Medicare {SELFTEST_MEDICARE}; ABN {SELFTEST_ABN}."
        subtypes = {
            (entity.text, entity.metadata["subtype"])
            for entity in _au_findings(_extract(text))
        }
        assert subtypes == {
            (SELFTEST_MEDICARE, "AU_MEDICARE"),
            (SELFTEST_ABN, "AU_ABN"),
        }

    def test_non_au_locale_carries_no_subtype(self):
        text = f"ABN {SELFTEST_ABN}, IHI {VALID_IHI}, Medicare {SELFTEST_MEDICARE}."
        for entity in _extract(text, locale=None):
            assert "subtype" not in (entity.metadata or {})


# Negatives: random numbers of each identifier length, dates and doses.
_NEGATIVE_VALUES = [
    "123456789",  # 9 digits, neither TFN nor ACN valid
    "987 654 321",
    "3123456789",  # 10 digits, Medicare-shaped, checksum fails
    "1234567890",
    "12345678901",  # 11 digits, not an ABN
    "98 765 432 109",
    "8003601234567890",  # IHI prefix, Luhn fails
    "1234567812345678",  # 16 digits, Luhn fails
    "12/03/2024",
    "2024-03-12",
    "500 mg",
    "paracetamol 1000 mg q6h",
]


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize("value", _NEGATIVE_VALUES)
@pytest.mark.parametrize(
    "template",
    [
        _BARE_TEMPLATE,
        "ABN ACN IHI Medicare TFN {v} listed.",
    ],
)
def test_negatives_are_not_tagged_as_au_identifiers(value, template):
    for sanity in ("123456789", "3123456789", "12345678901"):
        assert not validate_australian_abn(sanity)
    text = template.format(v=value)
    assert _au_findings(_extract(text)) == []
    assert _au_findings(safety_sweep(text, [], lang="en", locale="en_AU")) == []


class TestSafetySweep:
    @pytest.mark.parametrize(("subtype", "template", "value", "bare"), _POSITIVE_TABLE)
    def test_valid_with_context(self, subtype, template, value, bare):
        text = template.format(v=value)
        entities = safety_sweep(text, [], lang="en", locale="en_AU")
        finding = _single_au_finding(entities, text, value)
        assert finding.metadata["subtype"] == subtype
        # The legacy ``id_subtype`` key is only set for ``ID_NUM`` labels, and
        # AU identifiers now carry their own canonical labels.
        assert "id_subtype" not in finding.metadata
        assert finding.confidence >= 0.9

    @pytest.mark.parametrize(("subtype", "template", "value", "bare"), _POSITIVE_TABLE)
    def test_valid_without_context(self, subtype, template, value, bare):
        if bare is None:
            pytest.skip("context-dependent subtype; covered separately")
        text = _BARE_TEMPLATE.format(v=value)
        entities = safety_sweep(text, [], lang="en", locale="en_AU")
        finding = _single_au_finding(entities, text, value)
        assert finding.metadata["subtype"] == subtype
        assert finding.confidence == pytest.approx(bare)

    @pytest.mark.parametrize(
        ("template", "value", "subtype"),
        [
            ("Patient IHI {v} confirmed.", "8003.6012.3456.7894", "AU_IHI"),
            ("Patient IHI {v} confirmed.", "8003 6012  3456 7894", "AU_IHI"),
            ("Patient IHI {v} confirmed.", "8003-6012-3456-7894", "AU_IHI"),
            (_BARE_TEMPLATE, "8003.6012.3456.7894", "AU_IHI"),
            ("Medicare number {v} on card.", "2123.45670.1", "AU_MEDICARE"),
            ("Medicare number {v} on card.", "21 23 4567 01", "AU_MEDICARE"),
            ("Medicare number {v} on card.", "2123  45670  1", "AU_MEDICARE"),
            ("Medicare number {v} on card.", "2·1·2·3·4·5·6·7·0·1", "AU_MEDICARE"),
        ],
    )
    def test_ocr_split_identifiers_are_caught(self, template, value, subtype):
        assert VALID_IHI == "8003601234567894"
        text = template.format(v=value)
        entities = safety_sweep(text, [], lang="en", locale="en_AU")
        finding = _single_au_finding(entities, text, value)
        assert finding.metadata["subtype"] == subtype

    @pytest.mark.parametrize(
        "value",
        [
            "21 23 4567 01",  # split Medicare needs context
            "8003.6012.3456.7890",  # IHI prefix, Luhn fails
            "2123.45671.1",  # Medicare checksum fails
        ],
    )
    def test_ocr_split_rejections(self, value):
        text = _BARE_TEMPLATE.format(v=value)
        entities = safety_sweep(text, [], lang="en", locale="en_AU")
        assert _au_findings(entities) == []

    def test_split_fragment_of_longer_run_is_ignored(self):
        text = "Medicare number 9 2123.45670.1 on card."
        entities = safety_sweep(text, [], lang="en", locale="en_AU")
        assert _au_findings(entities) == []

    def test_non_au_sweep_carries_no_subtype(self):
        text = f"ABN {SELFTEST_ABN}, IHI {VALID_IHI}, Medicare {SELFTEST_MEDICARE}."
        for entity in safety_sweep(text, [], lang="en"):
            assert "subtype" not in entity.metadata


# ---------------------------------------------------------------------------
# Subtype plumbing through smart merging
# ---------------------------------------------------------------------------


def test_merge_carries_subtype_for_pattern_only_and_agreeing_model_spans():
    text = f"Patient IHI {VALID_IHI} confirmed."
    start = text.index(VALID_IHI)
    patterns = LOCALE_PII_PATTERNS["en_au"]

    pattern_only = merge_entities_with_semantic_units([], text, patterns=patterns)
    assert [e.get("subtype") for e in pattern_only] == ["AU_IHI"]
    assert _pii_merge_metadata(pattern_only[0])["subtype"] == "AU_IHI"

    model_entity = {
        "entity_type": "national_id",
        "score": 0.9,
        "start": start,
        "end": start + 8,
        "word": text[start : start + 8],
    }
    merged = merge_entities_with_semantic_units(
        [model_entity], text, patterns=patterns, prefer_model_labels=True
    )
    assert [e.get("subtype") for e in merged] == ["AU_IHI"]

    # A model that disagrees on the label keeps its label; no subtype is
    # claimed for a span the pattern did not label.
    other = dict(model_entity, entity_type="phone_number")
    merged = merge_entities_with_semantic_units(
        [other],
        text,
        patterns=patterns,
        prefer_model_labels=True,
        allow_label_expansion=False,
    )
    assert all("subtype" not in e for e in merged)


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Medicare number 2123 45671 1 on card.", "2123 45671 1"),
        ("TFN 123 456 783.", "123 456 783"),
        ("Practice ABN: 51 824 753 557 on invoice.", "51 824 753 557"),
        ("Company ACN 123 456 781 registered.", "123 456 781"),
        (f"Patient IHI {_flip_last(VALID_IHI)} confirmed.", _flip_last(VALID_IHI)),
    ],
)
def test_deidentify_still_redacts_labelled_invalid_values(text, value):
    from openmed.core.pii import deidentify

    result = deidentify(text, model_name="fixture-pii-model", lang="en", locale="en_AU")
    assert value not in result.deidentified_text


def test_merge_metadata_without_subtype_is_unchanged():
    assert _pii_merge_metadata({"detector_sources": ["ml"]}) == {
        "detector_sources": ["ml"]
    }


# ---------------------------------------------------------------------------
# Pipeline (ccia_l3)
# ---------------------------------------------------------------------------


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


@pytest.mark.parametrize(
    ("subtype", "text", "value"),
    [
        ("AU_ABN", f"Practice ABN: {SELFTEST_ABN} on invoice.", SELFTEST_ABN),
        ("AU_ACN", f"Company ACN {VALID_ACN} registered.", VALID_ACN),
        ("AU_IHI", f"Patient IHI {VALID_IHI_SPACED} confirmed.", VALID_IHI_SPACED),
        ("AU_IHI", "Patient IHI 8003.6012.3456.7894 ok.", "8003.6012.3456.7894"),
        (
            "AU_MEDICARE",
            f"Medicare number {SELFTEST_MEDICARE} on card.",
            SELFTEST_MEDICARE,
        ),
        ("AU_MEDICARE", "Medicare 2123456701-2 on card.", "2123456701-2"),
        ("AU_TFN", f"Tax file number {VALID_TFN} lodged.", VALID_TFN),
        ("AU_ABN", f"Reference {SELFTEST_ABN} noted.", SELFTEST_ABN),
    ],
)
def test_pipeline_ccia_l3_emits_au_label_with_subtype(subtype, text, value):
    from openmed.core.pipeline import Pipeline

    result = Pipeline(policy="ccia_l3", model_detector=_stub_detector).run(text)
    start = text.index(value)
    spans = [
        span
        for span in result.spans
        if (span.start, span.end) == (start, start + len(value))
    ]
    assert len(spans) == 1, [(s.start, s.end, s.metadata) for s in result.spans]
    span = spans[0]
    assert span.canonical_label == _CANONICAL_LABELS[subtype]
    assert span.metadata["subtype"] == subtype
    assert value not in result.redacted_text


def test_au_patterns_never_include_field_labels():
    for pattern in AU_IDENTIFIER_PII_PATTERNS:
        assert not re.search(r"[A-Za-z]{2,}", pattern.pattern.replace(r"\b", "")), (
            pattern.pattern
        )
