"""Tests for the cumulative ``ccia_l1`` / ``ccia_l2`` / ``ccia_l3`` profiles.

Every identifier used here is synthetic; the Medicare number satisfies the
published checksum (validated in the test) and is not issued to anyone.
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from openmed.core.anonymizer.providers.clinical_ids import (
    validate_australian_medicare,
)
from openmed.core.labels import (
    CANONICAL_LABELS,
    CLINICAL_CONCEPT,
    DIRECT_IDENTIFIER,
    QUASI_IDENTIFIER,
    policy_label_for,
)
from openmed.core.pipeline import Pipeline
from openmed.core.policy import (
    compile_policy,
    list_policies,
    load_policy,
    verify_policy_plan,
)
from openmed.core.policy_lint import lint_policy as lint_policy_report
from openmed.core.provenance import policy_fingerprint
from openmed.core.redaction_strength import ACTION_STRENGTH_ORDER
from openmed.core.schemas.span import ACTION_VALUES
from openmed.processing.outputs import PredictionResult

CCIA_POLICIES = ("ccia_l1", "ccia_l2", "ccia_l3")

TRAP_CLINICAL_LABELS = (
    "HISTOLOGIC_FINDING",
    "HISTOLOGIC_GRADE",
    "MARGIN_STATUS",
    "IHC_STAIN",
    "SPECIMEN_TYPE",
    "GROWTH_PARAMETER",
    "GROWTH_PERCENTILE",
    "DEVELOPMENTAL_MILESTONE",
    "GESTATIONAL_AGE",
    "CKD_STAGE",
    "RESPIRATORY_FINDING",
    "DIALYSIS_MODALITY",
    "DYSPNEA_GRADE",
    "FETAL_FINDING",
    "GRAVIDITY_PARITY",
    "OBSTETRIC_EVENT",
    "OXYGEN_SUPPORT",
    "RENAL_FUNCTION_MEASURE",
    "SPIROMETRY_MEASURE",
    "URINE_FINDING",
)

L2_ADDED_LABELS = frozenset(
    {
        "LOCATION",
        "ZIPCODE",
        "ORGANIZATION",
        "OCCUPATION",
        "JOB_TITLE",
        "ETHNICITY",
    }
)

# Pre-change fingerprints of stock profiles: adding the ccia profiles and the
# ``implied_locale`` field must not change any existing profile.
STOCK_FINGERPRINTS = {
    "hipaa_safe_harbor": (
        "sha256:3f0789bc64c9cf15e125f5523b80f5d3fe28fdd3e7abf647df4935e492f5d316"
    ),
    "clinical_minimal_redaction": (
        "sha256:286f7689e5dfef4d1de164c94173c8233823096a974e70cd8b5e4f5c81edadb3"
    ),
    "australia_privacy_act": (
        "sha256:b1a2728a7918dee513fdab82a76d8d783d9957344272d472449eb38da16b973a"
    ),
}

SYNTHETIC_MEDICARE = "2123 45670 1"
SYNTHETIC_NOTE = (
    f"Synthetic note: Medicare number {SYNTHETIC_MEDICARE} recorded at intake."
)


def _empty_detector(text: str, **_kwargs: object) -> PredictionResult:
    return PredictionResult(
        text=text,
        entities=[],
        model_name="synthetic-empty-detector",
        timestamp="2026-01-01T00:00:00Z",
    )


def _redacted_labels(name: str) -> set[str]:
    profile = load_policy(name)
    return {label for label, action in profile.actions.items() if action != "keep"}


# -- shift action vocabulary -------------------------------------------------


def test_shift_is_a_policy_action() -> None:
    assert "shift" in ACTION_VALUES


def test_shift_strength_sits_immediately_after_replace() -> None:
    replace_index = ACTION_STRENGTH_ORDER.index("replace")
    assert ACTION_STRENGTH_ORDER[replace_index + 1] == "shift"


@pytest.mark.parametrize("schema_name", ["action.schema.json", "span.schema.json"])
def test_shift_in_json_schema_action_enums(schema_name: str) -> None:
    resource = resources.files("openmed.core.schemas").joinpath("json", schema_name)
    payload = json.loads(resource.read_text(encoding="utf-8"))
    assert "shift" in json.dumps(payload)


def test_shift_in_attestation_schema() -> None:
    resource = resources.files("openmed.core.schemas").joinpath(
        "json", "attestation.schema.json"
    )
    assert '"shift"' in resource.read_text(encoding="utf-8")


# -- loading, compiling, verifying -------------------------------------------


def test_ccia_profiles_are_listed() -> None:
    assert set(CCIA_POLICIES) <= set(list_policies())


@pytest.mark.parametrize("name", CCIA_POLICIES)
def test_ccia_profile_loads_compiles_and_verifies(name: str) -> None:
    compiled = compile_policy(name)
    proof = verify_policy_plan(name, compiled.plan)

    assert proof.verified
    assert proof.coverage_percent == 100.0


@pytest.mark.parametrize("name", CCIA_POLICIES)
def test_ccia_profile_lints_clean(name: str) -> None:
    report = lint_policy_report(name)
    assert report["error_count"] == 0, report["errors"]
    assert report["warning_count"] == 0, report["warnings"]


def test_ccia_fingerprints_differ() -> None:
    fingerprints = {policy_fingerprint(name) for name in CCIA_POLICIES}
    assert len(fingerprints) == 3
    others = {
        policy_fingerprint(name)
        for name in list_policies()
        if name not in CCIA_POLICIES
    }
    assert not fingerprints & others


@pytest.mark.parametrize("name", sorted(STOCK_FINGERPRINTS))
def test_stock_profile_fingerprints_unchanged(name: str) -> None:
    assert policy_fingerprint(name) == STOCK_FINGERPRINTS[name]


def test_stock_profiles_have_no_implied_locale() -> None:
    for name in list_policies():
        if name in CCIA_POLICIES:
            continue
        profile = load_policy(name)
        assert profile.implied_locale is None
        assert "implied_locale" not in profile.to_dict()


# -- level contents ----------------------------------------------------------


@pytest.mark.parametrize("name", CCIA_POLICIES)
def test_ccia_profile_runtime_settings(name: str) -> None:
    profile = load_policy(name)
    assert profile.safety_sweep_mandatory is True
    assert profile.implied_locale == "en_AU"
    assert profile.to_dict()["implied_locale"] == "en_AU"
    assert profile.metadata.get("ccia_level") == int(name[-1])


@pytest.mark.parametrize("name", CCIA_POLICIES)
@pytest.mark.parametrize("label", TRAP_CLINICAL_LABELS)
def test_oncology_pathology_growth_labels_kept(name: str, label: str) -> None:
    assert load_policy(name).action_for(label) == "keep"


@pytest.mark.parametrize("name", CCIA_POLICIES)
def test_every_clinical_concept_label_kept(name: str) -> None:
    profile = load_policy(name)
    clinical = [
        label
        for label in CANONICAL_LABELS
        if policy_label_for(label) == CLINICAL_CONCEPT
    ]
    assert clinical
    assert {profile.action_for(label) for label in clinical} == {"keep"}


@pytest.mark.parametrize("name", CCIA_POLICIES)
def test_dates_shift_and_age_gender_kept(name: str) -> None:
    profile = load_policy(name)
    assert profile.action_for("DATE") == "shift"
    assert profile.action_for("DATE_OF_BIRTH") == "shift"
    assert profile.action_for("AGE") == "keep"
    assert profile.action_for("GENDER") == "keep"


def test_l1_masks_every_direct_identifier_except_dob() -> None:
    profile = load_policy("ccia_l1")
    for label in CANONICAL_LABELS:
        if policy_label_for(label) != DIRECT_IDENTIFIER:
            continue
        expected = "shift" if label == "DATE_OF_BIRTH" else "mask"
        assert profile.action_for(label) == expected, label


def test_l1_keeps_quasi_identifiers_other_than_date() -> None:
    profile = load_policy("ccia_l1")
    for label in CANONICAL_LABELS:
        if policy_label_for(label) != QUASI_IDENTIFIER or label == "DATE":
            continue
        assert profile.action_for(label) == "keep", label
    assert profile.action_for("ETHNICITY") == "keep"


def test_l2_adds_location_organisation_and_occupation_labels() -> None:
    added = _redacted_labels("ccia_l2") - _redacted_labels("ccia_l1")
    assert added == L2_ADDED_LABELS
    profile = load_policy("ccia_l2")
    assert {profile.action_for(label) for label in L2_ADDED_LABELS} == {"mask"}


def test_l3_masks_remaining_quasi_identifiers() -> None:
    profile = load_policy("ccia_l3")
    for label in CANONICAL_LABELS:
        if policy_label_for(label) != QUASI_IDENTIFIER:
            continue
        if label in {"AGE", "GENDER"}:
            assert profile.action_for(label) == "keep", label
        elif label == "DATE":
            assert profile.action_for(label) == "shift"
        else:
            assert profile.action_for(label) == "mask", label


def test_levels_are_cumulative() -> None:
    l1, l2, l3 = (_redacted_labels(name) for name in CCIA_POLICIES)
    assert l1 < l2 < l3


def test_levels_never_weaken_a_redacted_label() -> None:
    profiles = [load_policy(name) for name in CCIA_POLICIES]
    for lower, higher in zip(profiles, profiles[1:]):
        for label, action in lower.actions.items():
            if action != "keep":
                assert higher.actions[label] == action, (higher.name, label)


# -- implied locale ----------------------------------------------------------


def test_ccia_l3_without_locale_detects_medicare() -> None:
    assert validate_australian_medicare(SYNTHETIC_MEDICARE)
    result = Pipeline(policy="ccia_l3", model_detector=_empty_detector).run(
        SYNTHETIC_NOTE
    )
    start = SYNTHETIC_NOTE.index(SYNTHETIC_MEDICARE)
    assert any(
        span.start == start
        and span.end == start + len(SYNTHETIC_MEDICARE)
        and span.action == "mask"
        for span in result.spans
    )
    assert SYNTHETIC_MEDICARE not in result.redacted_text


def test_non_ccia_policy_without_locale_does_not_detect_medicare() -> None:
    result = Pipeline(
        policy="clinical_minimal_redaction", model_detector=_empty_detector
    ).run(SYNTHETIC_NOTE)
    start = SYNTHETIC_NOTE.index(SYNTHETIC_MEDICARE)
    assert not any(span.start == start for span in result.spans)


def test_deidentify_with_ccia_policy_uses_implied_locale(monkeypatch) -> None:
    import openmed
    from openmed.core.pii import deidentify

    def fake_analyze_text(inference_text, **kwargs):
        return PredictionResult(
            text=inference_text,
            entities=[],
            model_name=kwargs.get("model_name", "stub"),
            timestamp="2026-01-01T00:00:00Z",
        )

    monkeypatch.setattr(openmed, "analyze_text", fake_analyze_text)
    synthetic_date = "2024-03-15"
    text = f"{SYNTHETIC_NOTE} Reviewed on {synthetic_date}."
    result = deidentify(
        text,
        model_name="fixture-pii-model",
        policy="ccia_l1",
    )
    assert SYNTHETIC_MEDICARE not in result.deidentified_text
    # ``shift`` falls back to the call-level method in ``deidentify``; the
    # date never survives verbatim.
    assert synthetic_date not in result.deidentified_text
