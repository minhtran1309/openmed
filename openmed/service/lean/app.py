"""FastAPI app factory for the lean service.

Later endpoints are added as ``APIRouter`` instances passed to ``create_app``
(or included in ``_default_routers``); errors flow through ``LeanError`` or any
exception carrying a vocabulary ``error_code``. Request and response bodies are
never logged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from openmed.core.errors import PolicyError
from openmed.core.pipeline import ModelDetector
from openmed.core.policy import compile_policy, verify_policy_plan
from openmed.core.provenance import policy_fingerprint, provenance

from .apply import apply_document
from .detect import detect_segments
from .errors import LeanError, install_error_handlers
from .schemas import (
    ApplyRequest,
    ApplyResponse,
    DetectRequest,
    DetectResponse,
    HealthResponse,
    PolicyCoverageResponse,
    ProvenanceResponse,
    SelftestRequest,
    SelftestResponse,
    get_schema,
)
from .selftest import run_selftest

router = APIRouter()


def get_provenance_payload() -> dict[str, Any]:
    """Dependency supplying the provenance payload (overridable in tests)."""
    return provenance()


def get_model_detector() -> ModelDetector | None:
    """Dependency supplying the PII model stage (overridable in tests).

    ``None`` means the pipeline loads the configured model
    (``OPENMED_LEAN_MODEL`` or the openmed default).
    """
    return None


@router.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/v1/provenance", response_model=ProvenanceResponse)
def get_provenance(
    payload: dict[str, Any] = Depends(get_provenance_payload),
) -> dict[str, Any]:
    """Return version, fork tag, configured models and policy fingerprints."""
    return payload


@router.get("/v1/policies/{name}/coverage", response_model=PolicyCoverageResponse)
def get_policy_coverage(name: str) -> dict[str, Any]:
    """Return the coverage proof summary and fingerprint for a policy.

    Args:
        name: A policy name or alias from ``list_policies()``.

    Returns:
        ``covered_labels`` (sorted labels with a verified detector witness and
        a sufficient action), ``coverage_percent`` and the profile
        ``fingerprint``.

    Raises:
        LeanError: ``UNKNOWN_POLICY`` when ``name`` is not a policy.
    """
    try:
        compiled = compile_policy(name)
    except PolicyError:
        raise LeanError("UNKNOWN_POLICY") from None
    proof = verify_policy_plan(name, compiled.plan)
    covered = sorted(
        {
            witness.label
            for witness in proof.witnesses
            if witness.detector_covers_label and witness.action_meets_requirement
        }
    )
    return {
        "covered_labels": covered,
        "coverage_percent": proof.coverage_percent,
        "fingerprint": policy_fingerprint(name),
    }


@router.post(
    "/v1/detect",
    response_model=DetectResponse,
    response_model_exclude_none=True,
)
def detect(
    request: DetectRequest,
    model_detector: ModelDetector | None = Depends(get_model_detector),
    provenance_payload: dict[str, Any] = Depends(get_provenance_payload),
) -> dict[str, Any]:
    """Detect identifiers in each segment under a policy profile.

    Runs the full pipeline with the safety sweep on. Offsets index the exact
    segment text sent; no span text is returned.

    Args:
        request: Segments, policy name and ``preserve_whitespace``.
        model_detector: Injected PII model stage (``None`` for the default).
        provenance_payload: Version, fork tag, models and fingerprints.

    Returns:
        ``findings`` (``segment_id``, ``start``, ``end``, ``label``, optional
        ``subtype``, ``score``, ``policy_class``) and ``provenance``.

    Raises:
        LeanError: ``UNKNOWN_POLICY``, ``EMPTY_DOCUMENT``,
            ``DUPLICATE_SEGMENT_ID`` or ``DETECT_FAILED``.
    """
    findings = detect_segments(request, model_detector=model_detector)
    return {"findings": findings, "provenance": provenance_payload}


@router.post("/v1/apply", response_model=ApplyResponse)
def apply(request: ApplyRequest) -> dict[str, Any]:
    """Render caller-recorded spans over a document. Never detects.

    Args:
        request: Segments with spans, treatment, policy and either
            ``offset_days`` or ``patient_key`` with ``secret``.

    Returns:
        Rendered ``segments``, a ``spans`` report (``action``, ``out_start``,
        ``out_end``) covering every input span (directly or via merges),
        overlap ``merges`` and
        ``provenance``. No original span text.

    Raises:
        LeanError: ``UNSUPPORTED_TREATMENT``, ``UNKNOWN_POLICY``,
            ``EMPTY_DOCUMENT``, ``DUPLICATE_SEGMENT_ID``,
            ``UNKNOWN_SEGMENT_ID``, ``SPAN_OUT_OF_RANGE``, ``SPAN_INVERTED``,
            ``UNKNOWN_LABEL``, ``DATE_SHIFT_KEY_REQUIRED``,
            ``INVALID_REQUEST`` or ``APPLY_FAILED``.
    """
    return apply_document(request)


@router.post("/v1/selftest", response_model=SelftestResponse)
def selftest(
    request: SelftestRequest | None = None,
    model_detector: ModelDetector | None = Depends(get_model_detector),
) -> dict[str, Any]:
    """Run the consumer's synthetic probe and report pass/fail per check.

    The probe runs through detect (``ccia_l3``) then apply (``mark``,
    ``ccia_l1``, fixed offset). The ``person_tag`` and ``year_tag`` checks need
    the PII model stage; the other four come from patterns and apply.

    Args:
        request: Optional empty body; extra fields are ``INVALID_REQUEST``.
        model_detector: Injected PII model stage (``None`` for the default).

    Returns:
        ``{"checks": {name: "pass" | "fail"}}``: names and values only.

    Raises:
        LeanError: ``DETECT_FAILED`` or ``APPLY_FAILED`` on unexpected failure.
    """
    return {"checks": run_selftest(model_detector)}


@router.get("/v1/schemas/{name}")
def get_schema_by_name(name: str) -> dict[str, Any]:
    """Return a published JSON Schema or NOT_FOUND."""
    schema = get_schema(name)
    if schema is None:
        raise LeanError("NOT_FOUND")
    return schema


def create_app(extra_routers: tuple[APIRouter, ...] = ()) -> FastAPI:
    """Create the lean service app.

    Args:
        extra_routers: Additional routers to mount (later endpoints).

    Returns:
        A FastAPI app with the shared error handlers installed.
    """
    app = FastAPI(
        title="OpenMed lean service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_error_handlers(app)
    app.include_router(router)
    for extra in extra_routers:
        app.include_router(extra)
    return app


app = create_app()
