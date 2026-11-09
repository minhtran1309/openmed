"""Request/response models and the JSON Schema registry for the lean service.

Later endpoints register their models with ``register_schema`` so every
request and response is published at ``/v1/schemas/{name}``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .errors import ERROR_CODES

ErrorCode = Literal[
    "INVALID_REQUEST",
    "INTERNAL_ERROR",
    "NOT_FOUND",
    "UNKNOWN_POLICY",
    "EMPTY_DOCUMENT",
    "DUPLICATE_SEGMENT_ID",
    "UNKNOWN_SEGMENT_ID",
    "SPAN_OUT_OF_RANGE",
    "SPAN_INVERTED",
    "UNKNOWN_LABEL",
    "UNSUPPORTED_TREATMENT",
    "DATE_SHIFT_KEY_REQUIRED",
    "DETECT_FAILED",
    "APPLY_FAILED",
]
assert tuple(ErrorCode.__args__) == ERROR_CODES  # type: ignore[attr-defined]


class HealthResponse(BaseModel):
    """Response of ``GET /health``."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


class ProvenanceResponse(BaseModel):
    """Response of ``GET /v1/provenance``."""

    model_config = ConfigDict(extra="forbid")
    openmed_version: str
    fork_tag: str
    models: list[str]
    policies: dict[str, str]


class PolicyCoverageResponse(BaseModel):
    """Response of ``GET /v1/policies/{name}/coverage``."""

    model_config = ConfigDict(extra="forbid")
    covered_labels: list[str]
    coverage_percent: float
    fingerprint: str


class DetectSegment(BaseModel):
    """One segment of a ``POST /v1/detect`` request."""

    model_config = ConfigDict(extra="forbid")
    segment_id: StrictStr = Field(min_length=1)
    text: StrictStr


class DetectRequest(BaseModel):
    """Request of ``POST /v1/detect``.

    ``preserve_whitespace`` controls whether detectors see the segment text
    verbatim (``True``) or stripped. Offsets always index the text sent.
    """

    model_config = ConfigDict(extra="forbid")
    segments: list[DetectSegment]
    policy: StrictStr
    preserve_whitespace: StrictBool = True


class DetectFinding(BaseModel):
    """One detected span. Carries offsets and labels only, never span text."""

    model_config = ConfigDict(extra="forbid")
    segment_id: str
    start: int
    end: int
    label: str
    subtype: str | None = None
    score: float
    policy_class: str


class DetectResponse(BaseModel):
    """Response of ``POST /v1/detect``."""

    model_config = ConfigDict(extra="forbid")
    findings: list[DetectFinding]
    provenance: ProvenanceResponse


class ErrorResponse(BaseModel):
    """Body of every non-200 response."""

    model_config = ConfigDict(extra="forbid")
    error_code: ErrorCode


_REGISTRY: dict[str, type[BaseModel]] = {}


def register_schema(name: str, model: type[BaseModel]) -> None:
    """Publish ``model``'s JSON Schema at ``/v1/schemas/{name}``."""
    _REGISTRY[name] = model


def schema_names() -> tuple[str, ...]:
    """Return the registered schema names."""
    return tuple(sorted(_REGISTRY))


def get_schema(name: str) -> dict[str, Any] | None:
    """Return the JSON Schema for ``name`` or ``None`` if unknown."""
    model = _REGISTRY.get(name)
    return None if model is None else model.model_json_schema()


register_schema("health_response", HealthResponse)
register_schema("provenance_response", ProvenanceResponse)
register_schema("error_response", ErrorResponse)
register_schema("policy_coverage_response", PolicyCoverageResponse)
register_schema("detect_request", DetectRequest)
register_schema("detect_response", DetectResponse)
