"""AU identifiers carry their own canonical labels next to ``SSN``.

Every identifier here is synthetic and checksum-valid (asserted against the
validators below); none is asserted to belong to a real person or company.
"""

from __future__ import annotations

import pytest

from openmed.core.anonymizer.providers.clinical_ids import (
    validate_australian_medicare,
    validate_australian_tfn,
)
from openmed.core.au_identifiers import (
    validate_australian_abn,
    validate_australian_acn,
    validate_australian_ihi,
)
from openmed.core.labels import (
    CANONICAL_LABELS,
    DIRECT_IDENTIFIER,
    LABEL_METADATA,
    SSN,
    normalize_label,
    policy_label_for,
)
from openmed.core.pii import extract_pii
from openmed.core.pipeline import Pipeline
from openmed.core.policy import list_policies, load_policy
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult

MEDICARE = "2123 45670 1"
TFN = "123 456 782"
IHI = "8003601234567894"
ABN = "11 345 678 919"
ACN = "123 456 780"

assert validate_australian_medicare(MEDICARE)
assert validate_australian_tfn(TFN)
assert validate_australian_ihi(IHI)
assert validate_australian_abn(ABN)
assert validate_australian_acn(ACN)

AU_LABELS = {
    "AU_MEDICARE": "MEDICARE_NUMBER",
    "AU_TFN": "TFN",
    "AU_IHI": "IHI",
    "AU_ABN": "ABN",
    "AU_ACN": "ACN",
}

# (subtype, text, value)
AU_CASES = [
    ("AU_MEDICARE", f"Medicare number {MEDICARE} on card.", MEDICARE),
    ("AU_TFN", f"Tax file number {TFN} lodged.", TFN),
    ("AU_IHI", f"Patient IHI {IHI} confirmed.", IHI),
    ("AU_ABN", f"Practice ABN: {ABN} on invoice.", ABN),
    ("AU_ACN", f"Company ACN {ACN} registered.", ACN),
]


def _empty(text, **kwargs):
    return PredictionResult(
        text=text,
        entities=[],
        model_name=kwargs.get("model_name", "stub"),
        timestamp="2026-01-01T00:00:00",
    )


@pytest.fixture
def no_model(monkeypatch):
    import openmed

    monkeypatch.setattr(openmed, "analyze_text", _empty)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(AU_LABELS.values()))
def test_au_label_is_canonical_direct_identifier(label):
    assert label in CANONICAL_LABELS
    assert LABEL_METADATA[label] == LABEL_METADATA[SSN]
    assert policy_label_for(label) == DIRECT_IDENTIFIER


def test_ssn_is_unchanged():
    assert SSN == "SSN"
    assert SSN in CANONICAL_LABELS
    assert normalize_label("ssn") == "SSN"
    assert normalize_label("social_security_number") == "SSN"
    assert policy_label_for("SSN") == DIRECT_IDENTIFIER


@pytest.mark.parametrize(
    ("alias", "label"),
    [
        ("MEDICARE_NUMBER", "MEDICARE_NUMBER"),
        ("medicare", "MEDICARE_NUMBER"),
        ("medicare_number", "MEDICARE_NUMBER"),
        ("Medicare No", "MEDICARE_NUMBER"),
        ("medicare_no", "MEDICARE_NUMBER"),
        ("B-MEDICARE_NUMBER", "MEDICARE_NUMBER"),
        ("TFN", "TFN"),
        ("tfn", "TFN"),
        ("tax_file_number", "TFN"),
        ("Tax-File-Number", "TFN"),
        ("IHI", "IHI"),
        ("ihi", "IHI"),
        ("individual_healthcare_identifier", "IHI"),
        ("ABN", "ABN"),
        ("abn", "ABN"),
        ("australian_business_number", "ABN"),
        ("ACN", "ACN"),
        ("acn", "ACN"),
        ("australian_company_number", "ACN"),
    ],
)
def test_aliases_normalize_case_insensitively(alias, label):
    assert normalize_label(alias) == label


def test_generic_id_aliases_still_map_to_id_num():
    for alias in ("ID_NUM", "national_id", "id", "passport"):
        assert normalize_label(alias) == "ID_NUM"


# ---------------------------------------------------------------------------
# Emission: extract_pii, safety sweep, Pipeline
# ---------------------------------------------------------------------------


def _at(entities, text, value):
    start = text.index(value)
    return [e for e in entities if (e.start, e.end) == (start, start + len(value))]


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize(("subtype", "text", "value"), AU_CASES)
def test_extract_pii_emits_au_label_and_keeps_subtype(subtype, text, value):
    entities = extract_pii(
        text, model_name="fixture", lang="en", locale="en_AU"
    ).entities
    found = _at(entities, text, value)
    assert len(found) == 1, [(e.text, e.label, e.metadata) for e in entities]
    assert normalize_label(found[0].label) == AU_LABELS[subtype]
    assert found[0].metadata["subtype"] == subtype


@pytest.mark.parametrize(("subtype", "text", "value"), AU_CASES)
def test_safety_sweep_emits_au_label(subtype, text, value):
    found = _at(safety_sweep(text, [], lang="en", locale="en_AU"), text, value)
    assert len(found) == 1
    assert normalize_label(found[0].label) == AU_LABELS[subtype]
    assert found[0].metadata["subtype"] == subtype


@pytest.mark.parametrize(
    ("subtype", "text", "value"),
    [
        ("AU_IHI", "Patient IHI 8003.6012.3456.7894 ok.", "8003.6012.3456.7894"),
        ("AU_MEDICARE", "Medicare number 2123.45670.1 on card.", "2123.45670.1"),
    ],
)
def test_split_sweep_rules_emit_au_label(subtype, text, value):
    found = _at(safety_sweep(text, [], lang="en", locale="en_AU"), text, value)
    assert len(found) == 1
    assert normalize_label(found[0].label) == AU_LABELS[subtype]
    assert found[0].metadata["subtype"] == subtype


@pytest.mark.parametrize("policy", ["ccia_l1", "ccia_l3", "australia_privacy_act"])
@pytest.mark.parametrize(("subtype", "text", "value"), AU_CASES)
def test_pipeline_emits_au_canonical_label(policy, subtype, text, value):
    result = Pipeline(policy=policy, model_detector=_empty).run(text, locale="en_AU")
    found = _at(result.spans, text, value)
    assert len(found) == 1, [(s.start, s.end, s.canonical_label) for s in result.spans]
    assert found[0].canonical_label == AU_LABELS[subtype]
    assert found[0].metadata["subtype"] == subtype
    assert value not in result.redacted_text


def test_model_id_num_span_does_not_override_au_label():
    text = f"Medicare number {MEDICARE} on card."
    start = text.index(MEDICARE)

    def model(inner_text, **kwargs):
        from openmed.processing.outputs import EntityPrediction

        return PredictionResult(
            text=inner_text,
            entities=[
                EntityPrediction(
                    text=MEDICARE,
                    label="ID_NUM",
                    start=start,
                    end=start + len(MEDICARE),
                    confidence=0.95,
                )
            ],
            model_name="stub",
            timestamp="2026-01-01T00:00:00",
        )

    result = Pipeline(policy="ccia_l3", model_detector=model).run(text)
    found = _at(result.spans, text, MEDICARE)
    assert [s.canonical_label for s in found] == ["MEDICARE_NUMBER"]
    assert found[0].metadata["subtype"] == "AU_MEDICARE"


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ccia_l1", "ccia_l2", "ccia_l3"])
def test_ccia_profiles_mask_au_labels_explicitly(name):
    profile = load_policy(name)
    for label in AU_LABELS.values():
        assert profile.actions[label] == "mask"


def _explicit_label_policies():
    names = []
    for name in list_policies():
        profile = load_policy(name)
        if "SSN" in profile.actions:
            names.append(name)
    return names


@pytest.mark.parametrize("name", _explicit_label_policies())
def test_every_policy_treats_au_labels_like_ssn(name):
    profile = load_policy(name)
    for label in AU_LABELS.values():
        assert profile.actions[label] == profile.actions["SSN"]
        assert profile.action_for(label) != "keep"


# ---------------------------------------------------------------------------
# Non-AU locales: no AU labels, SSN unchanged
# ---------------------------------------------------------------------------

_AU_VALUES_TEXT = (
    f"Medicare {MEDICARE}, TFN {TFN}, IHI {IHI}, ABN {ABN}, ACN {ACN}; SSN 078-05-1120."
)


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize(
    ("lang", "locale"),
    [
        ("en", None),
        ("en", "en_US"),
        ("en", "en_GB"),
        ("en", "en_IN"),
        ("de", "de_DE"),
        ("fr", "fr_FR"),
    ],
)
def test_non_au_locales_never_emit_au_labels(lang, locale):
    au_labels = set(AU_LABELS.values())
    entities = extract_pii(
        _AU_VALUES_TEXT, model_name="fixture", lang=lang, locale=locale
    ).entities
    swept = safety_sweep(_AU_VALUES_TEXT, [], lang=lang, locale=locale)
    for entity in [*entities, *swept]:
        assert normalize_label(entity.label) not in au_labels
        assert not (entity.metadata or {}).get("subtype", "").startswith("AU_")


def test_ssn_still_detected_as_ssn_in_en_us():
    text = "Patient SSN 078-05-1120 on file."
    result = Pipeline(policy="hipaa_safe_harbor", model_detector=_empty).run(
        text, locale="en_US"
    )
    found = _at(result.spans, text, "078-05-1120")
    assert [span.canonical_label for span in found] == ["SSN"]
    assert "078-05-1120" not in result.redacted_text


_EN_US_PROBE = (
    "Patient SSN 078-05-1120, phone 617-555-0142, seen 03/04/2021, DOB 1980-02-03."
)
# Pinned before the AU labels were added; the new labels must not change it.
_EN_US_REDACTED = "Patient SSN [ssn], phone [phone_number], seen [date], DOB [date]."


@pytest.mark.usefixtures("no_model")
def test_en_us_deidentify_output_is_pinned():
    from openmed.core.pii import deidentify

    result = deidentify(
        _EN_US_PROBE, method="mask", model_name="fixture", lang="en", locale="en_US"
    )
    assert result.deidentified_text == _EN_US_REDACTED
    assert sorted((e.start, e.end, e.label) for e in result.pii_entities) == [
        (12, 23, "ssn"),
        (31, 43, "phone_number"),
        (50, 60, "date"),
        (66, 76, "date"),
    ]


def test_en_us_pipeline_output_is_pinned():
    result = Pipeline(policy="hipaa_safe_harbor", model_detector=_empty).run(
        _EN_US_PROBE, locale="en_US"
    )
    assert result.redacted_text == _EN_US_REDACTED
    assert sorted((s.start, s.end, s.canonical_label) for s in result.spans) == [
        (12, 23, "SSN"),
        (31, 43, "PHONE"),
        (50, 60, "DATE"),
        (66, 76, "DATE"),
    ]


@pytest.mark.parametrize("profile", ["balanced", "strict_no_leak"])
@pytest.mark.parametrize("language", ["en", "de", "*"])
@pytest.mark.parametrize("label", sorted(AU_LABELS.values()))
def test_au_label_thresholds_equal_id_num(label, language, profile):
    from openmed.core.thresholds import lookup_threshold

    au = dict(lookup_threshold(label, language, profile))
    id_num = dict(lookup_threshold("ID_NUM", language, profile))
    assert au.pop("canonical_label") == label
    assert id_num.pop("canonical_label") == "ID_NUM"
    assert au == id_num
    assert au["source"] != "profile_default"
