"""Request/response models and the JSON Schema registry for the lean service.

Later endpoints register their models with ``register_schema`` so every
request and response is published at ``/v1/schemas/{name}``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
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
    "AUDIT_KEY_MISSING",
    "PDF_UNREADABLE",
    "PAGE_COUNT_MISMATCH",
    "REGION_OUT_OF_BOUNDS",
    "VERIFY_FAILED",
    "SURROGATE_KEY_REQUIRED",
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
    ``replacement`` (1 to 256 characters) are accepted. Under ``replace`` and
    ``generalise`` a single (unmerged) span's ``replacement`` is rendered
    verbatim, ahead of any built-in rendering, unless it normalises to or
    contains its source, which renders the tag; under ``keep`` the span's
    source is kept only for a non-date quasi-identifier (direct identifiers
    are still marked, dates still shift); under ``mark`` it is ignored.
    Otherwise its content is the caller's responsibility.
    ``source`` and ``score`` are copied from the detect findings into the
    audit (``sources`` and ``confidence``); rendering ignores them.
    """

    model_config = ConfigDict(extra="forbid")
    span_id: StrictStr = Field(min_length=1)
    start: StrictInt
    end: StrictInt
    label: StrictStr
    subtype: StrictStr | None = None
    replacement: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    segment_id: StrictStr | None = None
    source: Literal["model", "pattern", "table-column", "manual"] | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)


class ApplySegmentIn(BaseModel):
    """One segment of a ``POST /v1/apply`` document."""

    model_config = ConfigDict(extra="forbid")
    segment_id: StrictStr = Field(min_length=1)
    text: StrictStr
    spans: list[ApplySpanIn] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    """Request of ``POST /v1/apply``.

    ``treatment`` is one of ``mark``, ``replace``, ``generalise`` and
    ``keep``, all implemented (anything else is ``INVALID_REQUEST``). Pass
    ``offset_days`` or ``patient_key`` with ``secret`` when a date shift may
    be needed under ``mark``, ``generalise`` or ``keep`` (``generalise``
    reduces a date of birth to the year of the shifted date, which needs the
    shift too); a document with no dates needs no key.
    ``replace`` always needs ``patient_key`` with ``secret``
    (``SURROGATE_KEY_REQUIRED`` otherwise, ``offset_days`` or a half key
    included; ``offset_days`` with any key part is ``INVALID_REQUEST``): they
    derive both the date shift and the per-patient surrogates. The service
    keeps no state. Keys, secrets, surrogates, replacement texts and
    generalised values are never logged or returned outside the rendered
    segments.
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
    action: Literal["mark", "shift", "keep", "replace", "replacement", "generalise"]
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
    """Response of ``POST /v1/apply``. Carries no original span text.

    ``audit`` is the signed ``AuditReport`` for this render and
    ``attestation`` the Safe Harbor attestation built from it; neither
    carries span text, surrogates, context, keys, secrets or offset days.
    """

    model_config = ConfigDict(extra="forbid")
    segments: list[ApplySegmentOut]
    spans: list[ApplySpanOut]
    merges: list[ApplyMerge]
    provenance: ApplyProvenance
    audit: dict[str, Any]
    attestation: dict[str, Any]


class VerifyRegion(BaseModel):
    """One redaction region of a ``POST /v1/verify/pdf`` request.

    ``page`` is 1-based. ``bbox`` is ``[x0, top, x1, bottom]`` in PDF points
    with a top-left origin (pdfplumber's native coordinates).
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    page: StrictInt = Field(ge=1)
    bbox: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(
        min_length=4, max_length=4
    )

    @model_validator(mode="after")
    def _check_bbox(self) -> VerifyRegion:
        x0, top, x1, bottom = self.bbox
        if x0 >= x1 or top >= bottom:
            raise ValueError("bbox must have x0 < x1 and top < bottom")
        return self


#: Most regions one ``POST /v1/verify/pdf`` request may carry.
MAX_VERIFY_REGIONS = 256


class VerifyPdfRegions(RootModel[list[VerifyRegion]]):
    """The ``regions`` part of ``POST /v1/verify/pdf``: 1 to 256 regions."""

    root: list[VerifyRegion] = Field(min_length=1, max_length=MAX_VERIFY_REGIONS)


class VerifyRegionResult(BaseModel):
    """Verification status of one region. Counts and boxes only.

    ``status`` is ``unverifiable`` when the region has no extractable source
    words (scanned pages, images), ``text_leak`` when a selected word is still
    extractable, ``no_box`` when no opaque box covers the region, and
    ``passed`` otherwise.
    """

    model_config = ConfigDict(extra="forbid")
    page: int
    bbox: list[float]
    status: Literal["passed", "text_leak", "no_box", "unverifiable"]
    residual_word_count: int


class VerifyTextRemoval(BaseModel):
    """Selected source words and how many of them remain extractable."""

    model_config = ConfigDict(extra="forbid")
    selected: int
    residual: int


class VerifyPdfResponse(BaseModel):
    """Response of ``POST /v1/verify/pdf``. Counts, boxes and hashes only.

    ``passed`` is true only when every region passed. ``report_sha256`` is
    the SHA-256 hex digest of the canonical JSON of the body without it.
    """

    model_config = ConfigDict(extra="forbid")
    passed: bool
    regions: list[VerifyRegionResult]
    text_removal: VerifyTextRemoval
    report_sha256: str


class ErrorResponse(BaseModel):
    """Body of every non-200 response.

    ``page`` (1-based) is present only for ``REGION_OUT_OF_BOUNDS``.
    """

    model_config = ConfigDict(extra="forbid")
    error_code: ErrorCode
    page: int | None = None


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
register_schema("verify_pdf_regions", VerifyPdfRegions)
register_schema("verify_pdf_response", VerifyPdfResponse)
