"""Tests for the Australian NRP deny list (ticket 07, story 17).

The terms are group descriptors, not identifiers; the surrounding sentences
are synthetic. The model is always stubbed.
"""

from __future__ import annotations

import dataclasses

import pytest

from openmed.core.au_nrp import AU_NRP_PII_PATTERNS, AU_NRP_TERMS, NRP
from openmed.core.pii import extract_pii
from openmed.core.pii_i18n import (
    LANGUAGE_PII_PATTERNS,
    LOCALE_PII_PATTERNS,
    get_patterns_for_language,
)
from openmed.core.policy import load_policy
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult

LONGEST = "Aboriginal and Torres Strait Islander"


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


def _subtype(item) -> str | None:
    return (item.metadata or {}).get("subtype")


def _nrp(items):
    return [item for item in items if _subtype(item) == NRP]


# ---------------------------------------------------------------------------
# Terms and registration
# ---------------------------------------------------------------------------


def test_terms_cover_story_17_longest_first():
    assert set(AU_NRP_TERMS) == {
        "Aboriginal and Torres Strait Islander",
        "Torres Strait Islander",
        "Indigenous Australian",
        "Aboriginal",
    }
    assert list(AU_NRP_TERMS) == sorted(AU_NRP_TERMS, key=len, reverse=True)
    assert AU_NRP_TERMS[0] == LONGEST


def test_nrp_is_one_alternation_with_ethnicity_label_and_nrp_subtype():
    (pattern,) = AU_NRP_PII_PATTERNS
    assert NRP == "NRP"
    assert pattern.entity_type == "ethnicity"
    assert pattern.subtype == NRP


def test_nrp_patterns_registered_for_en_au_only():
    au_patterns = LOCALE_PII_PATTERNS["en_au"]
    assert all(pattern in au_patterns for pattern in AU_NRP_PII_PATTERNS)
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale)
        assert not any(pattern in patterns for pattern in AU_NRP_PII_PATTERNS)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term",
    [
        *AU_NRP_TERMS,
        "aboriginal",
        "TORRES STRAIT ISLANDER",
        "Torres Strait Islanders",
        "Indigenous Australians",
    ],
)
def test_each_term_is_one_ethnicity_span_with_nrp_subtype(term):
    text = f"Patient identifies as {term} today."
    start = text.index(term)
    result = _pipeline(text)
    assert [(s.start, s.end, s.canonical_label, _subtype(s)) for s in result.spans] == [
        (start, start + len(term), "ETHNICITY", NRP)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Aboriginality assessed.",
        "Seen at Aboriginalville clinic.",
        "Non-Indigenous patient.",
        "Torres Strait ferry delayed.",
        "Strait Islander route.",
    ],
)
def test_partial_words_are_not_nrp(no_model, text):
    assert not _nrp(_pipeline(text).spans)
    assert not _nrp(_extract(text))
    assert not _nrp(_sweep(text))


def test_longest_phrase_is_one_span(no_model):
    text = f"Patient identifies as {LONGEST}; Aboriginal liaison officer present."
    start = text.index(LONGEST)
    expected_long = (start, start + len(LONGEST))
    short_start = text.index("Aboriginal liaison")
    expected_short = (short_start, short_start + len("Aboriginal"))

    result = _pipeline(text)
    assert [(s.start, s.end, s.canonical_label) for s in result.spans] == [
        (*expected_long, "ETHNICITY"),
        (*expected_short, "ETHNICITY"),
    ]
    assert LONGEST not in result.redacted_text
    assert "Torres" not in result.redacted_text

    entities = _extract(text)
    assert [(e.start, e.end, e.label) for e in entities] == [
        (*expected_long, "ethnicity"),
        (*expected_short, "ethnicity"),
    ]
    assert all(_subtype(e) == NRP for e in entities)

    swept = _sweep(text)
    assert [(e.start, e.end, e.label) for e in swept] == [
        (*expected_long, "ethnicity"),
        (*expected_short, "ethnicity"),
    ]
    assert all(_subtype(e) == NRP for e in swept)


def test_longest_phrase_across_a_line_break_is_one_span_in_pipeline():
    text = "Identifies as Aboriginal and\nTorres Strait Islander.\n"
    value = "Aboriginal and\nTorres Strait Islander"
    start = text.index(value)
    result = _pipeline(text)
    assert [(s.start, s.end, _subtype(s)) for s in result.spans] == [
        (start, start + len(value), NRP)
    ]


# ---------------------------------------------------------------------------
# Policy: ccia_l1 keeps the term, ccia_l2 and ccia_l3 mask it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "action"),
    [("ccia_l1", "keep"), ("ccia_l2", "mask"), ("ccia_l3", "mask")],
)
def test_policy_action_for_nrp(policy, action):
    assert load_policy(policy).actions["ETHNICITY"] == action
    text = f"Patient identifies as {LONGEST} today."
    (span,) = _pipeline(text, policy=policy).spans
    assert (span.canonical_label, _subtype(span), span.action) == (
        "ETHNICITY",
        NRP,
        action,
    )


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------

_NON_AU_TEXT = (
    f"Patient identifies as {LONGEST}. Aboriginal liaison present; "
    "Indigenous Australian health service; Torres Strait Islander family."
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
        assert not _nrp(result.spans)
        return (
            _snapshot(entities),
            _snapshot(swept),
            result.redacted_text,
            _span_snapshot(result.spans),
        )

    with_hook = outputs()

    # The same run with the AU NRP pattern removed from every pattern table
    # must be byte-identical (apart from per-run salted hashes).
    for table in (LOCALE_PII_PATTERNS, LANGUAGE_PII_PATTERNS):
        for key, patterns in list(table.items()):
            kept = [p for p in patterns if p not in AU_NRP_PII_PATTERNS]
            monkeypatch.setitem(table, key, kept)
    assert outputs() == with_hook
