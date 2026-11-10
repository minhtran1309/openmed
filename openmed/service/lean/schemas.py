"""Request/response models and the JSON Schema registry for the lean service.

Later endpoints register their models with ``register_schema`` so every
request and response is published at ``/v1/schemas/{name}``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

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


class DetectCell(BaseModel):
    """One table cell of a detect segment: code-point offsets into its text."""

    model_config = ConfigDict(extra="forbid")
    start: StrictInt
    end: StrictInt


class DetectSegment(BaseModel):
    """One segment of a ``POST /v1/detect`` request.

    A segment may be one table row: ``table_header`` names the columns and
    ``cells[i]`` gives the offsets of column ``i``'s cell in ``text``. Both
    are sent together, with equal lengths, and every cell must satisfy
    ``0 <= start <= end <= len(text)``; anything else is ``INVALID_REQUEST``.
    """

    model_config = ConfigDict(extra="forbid")
    segment_id: StrictStr = Field(min_length=1)
    text: StrictStr
    table_header: list[StrictStr] | None = None
    cells: list[DetectCell] | None = None

    @model_validator(mode="after")
    def _check_table(self) -> DetectSegment:
        if self.table_header is None and self.cells is None:
            return self
        if self.table_header is None or self.cells is None:
            raise ValueError("table_header and cells must be sent together")
        if len(self.table_header) != len(self.cells):
            raise ValueError("table_header and cells differ in length")
        for cell in self.cells:
            if not 0 <= cell.start <= cell.end <= len(self.text):
                raise ValueError("cell offsets are outside the segment text")
        return self


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
    #: Set only on findings from table column classification.
    source: Literal["model", "pattern", "table-column"] | None = None


class DetectResponse(BaseModel):
    """Response of ``POST /v1/detect``."""

    model_config = ConfigDict(extra="forbid")
    findings: list[DetectFinding]
    provenance: ProvenanceResponse


class SelftestRequest(BaseModel):
    """Optional (empty) body of ``POST /v1/selftest``; no fields are accepted."""

    model_config = ConfigDict(extra="forbid")


class SelftestResponse(BaseModel):
    """Response of ``POST /v1/selftest``: check names and values only."""

    model_config = ConfigDict(extra="forbid")
    checks: dict[str, Literal["pass", "fail"]]


Treatment = Literal["mark", "replace", "generalise", "keep"]


class ApplySpanIn(BaseModel):
    """One caller-recorded span of an apply segment.

    Offsets are Python code-point offsets into the segment text (not graphemes
    or UTF-16 units); callers must supply grapheme-aligned spans, since an edge
    inside a grapheme cluster is rendered as given. ``segment_id`` is
    optional and, when given, must name the enclosing segment. ``subtype`` and
    ``replacement`` are accepted; ``replacement`` is ignored under ``mark``.
    """

    model_config = ConfigDict(extra="forbid")
    span_id: StrictStr = Field(min_length=1)
    start: StrictInt
    end: StrictInt
    label: StrictStr
    subtype: StrictStr | None = None
    replacement: StrictStr | None = None
    segment_id: StrictStr | None = None


class ApplySegmentIn(BaseModel):
    """One segment of a ``POST /v1/apply`` document."""

    model_config = ConfigDict(extra="forbid")
    segment_id: StrictStr = Field(min_length=1)
    text: StrictStr
    spans: list[ApplySpanIn] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    """Request of ``POST /v1/apply``.

    ``treatment`` is validated against the full vocabulary (anything else is
    ``INVALID_REQUEST``); only ``mark`` is implemented, the rest return
    ``UNSUPPORTED_TREATMENT``. Pass ``offset_days`` or ``patient_key`` with
    ``secret`` when a date shift may be needed. Keys and secrets are never
    logged or returned.
    """

    model_config = ConfigDict(extra="forbid")
    document: list[ApplySegmentIn]
    treatment: Treatment
    policy: StrictStr
    offset_days: StrictInt | None = None
    patient_key: StrictStr | None = Field(default=None, min_length=1)
    secret: StrictStr | None = Field(default=None, min_length=1)


class ApplySegmentOut(BaseModel):
    """One rendered segment."""

    model_config = ConfigDict(extra="forbid")
    segment_id: str
    text: str


class ApplySpanOut(BaseModel):
    """Report for one span, in output coordinates.

    Together with ``merges`` this covers every input span (directly or via
    merges).
    """

    model_config = ConfigDict(extra="forbid")
    span_id: str
    segment_id: str
    action: Literal["mark", "shift", "keep"]
    out_start: int
    out_end: int


class ApplyMerge(BaseModel):
    """Spans merged into a kept span because they overlapped."""

    model_config = ConfigDict(extra="forbid")
    kept_span_id: str
    merged_span_ids: list[str]


class ApplyProvenance(BaseModel):
    """Build and policy identification for an apply result."""

    model_config = ConfigDict(extra="forbid")
    openmed_version: str
    policy_fingerprint: str


class ApplyResponse(BaseModel):
    """Response of ``POST /v1/apply``. Carries no original span text."""

    model_config = ConfigDict(extra="forbid")
    segments: list[ApplySegmentOut]
    spans: list[ApplySpanOut]
    merges: list[ApplyMerge]
    provenance: ApplyProvenance


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
register_schema("selftest_response", SelftestResponse)
register_schema("apply_request", ApplyRequest)
register_schema("apply_response", ApplyResponse)
