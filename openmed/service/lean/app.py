"""FastAPI app factory for the lean service.

Later endpoints are added as ``APIRouter`` instances passed to ``create_app``
(or included in ``_default_routers``); errors flow through ``LeanError`` or any
exception carrying a vocabulary ``error_code``. Request and response bodies are
never logged.

The service refuses to start without ``OPENMED_AUDIT_KEY``: the check runs in
the app lifespan only, so importing this module and calling ``create_app()``
never need the key.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from openmed.compliance.safe_harbor import generate_safe_harbor_attestation
from openmed.core.apply_audit import AUDIT_KEY_ENV, build_apply_audit
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

logger = logging.getLogger("openmed.service.lean")

router = APIRouter()

_AUDIT_KEY_MISSING = "AUDIT_KEY_MISSING"


class AuditKeyMissingError(RuntimeError):
    """Raised at startup when ``OPENMED_AUDIT_KEY`` is unset or blank.

    The message and ``error_code`` are both ``AUDIT_KEY_MISSING``.
    """

    error_code = _AUDIT_KEY_MISSING

    def __init__(self) -> None:
        super().__init__(_AUDIT_KEY_MISSING)


def _configured_audit_key() -> str | None:
    key = os.environ.get(AUDIT_KEY_ENV, "")
    return key if key.strip() else None


def get_audit_key() -> str:
    """Dependency supplying the audit signing key (overridable in tests).

    Reads ``OPENMED_AUDIT_KEY`` on every call. This guards apps started
    without the lifespan check.

    Returns:
        The key exactly as set in the environment.

    Raises:
        LeanError: ``AUDIT_KEY_MISSING`` when the key is unset or blank.
    """
    key = _configured_audit_key()
    if key is None:
        raise LeanError(_AUDIT_KEY_MISSING)
    return key


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    if _configured_audit_key() is None:
        raise AuditKeyMissingError()
    yield


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
def apply(
    request: ApplyRequest,
    audit_key: str = Depends(get_audit_key),
) -> dict[str, Any]:
    """Render caller-recorded spans over a document. Never detects.

    Args:
        request: Segments with spans, treatment, policy and either
            ``offset_days`` or ``patient_key`` with ``secret``.
        audit_key: The ``OPENMED_AUDIT_KEY`` that signs the audit.

    Returns:
        Rendered ``segments``, a ``spans`` report (``action``, ``out_start``,
        ``out_end``) covering every input span (directly or via merges),
        overlap ``merges``, ``provenance``, the signed ``audit`` report and
        the Safe Harbor ``attestation`` built from it. No original span text.

    Raises:
        LeanError: ``UNSUPPORTED_TREATMENT``, ``UNKNOWN_POLICY``,
            ``EMPTY_DOCUMENT``, ``DUPLICATE_SEGMENT_ID``,
            ``UNKNOWN_SEGMENT_ID``, ``SPAN_OUT_OF_RANGE``, ``SPAN_INVERTED``,
            ``UNKNOWN_LABEL``, ``DATE_SHIFT_KEY_REQUIRED``,
            ``INVALID_REQUEST``, ``APPLY_FAILED`` or ``AUDIT_KEY_MISSING``.
    """
    result = apply_document(request)
    document = [segment.model_dump() for segment in request.document]
    try:
        report = build_apply_audit(
            document,
            result,
            policy=request.policy,
            key=audit_key,
            treatment=request.treatment,
        )
        attestation = generate_safe_harbor_attestation(report, any_policy=True)
    except Exception as exc:
        logger.warning("apply audit failed type=%s", type(exc).__name__)
        raise LeanError("APPLY_FAILED") from None
    return {
        **result,
        "audit": report.to_dict(),
        "attestation": attestation.to_dict(),
    }


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
        A FastAPI app with the shared error handlers installed. Its lifespan
        raises ``AuditKeyMissingError`` at startup when
        ``OPENMED_AUDIT_KEY`` is unset or blank; building the app does not.
    """
    app = FastAPI(
        title="OpenMed lean service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    install_error_handlers(app)
    app.include_router(router)
    for extra in extra_routers:
        app.include_router(extra)
    return app


app = create_app()
