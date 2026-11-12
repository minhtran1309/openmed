"""Detection over caller-supplied segments for ``POST /v1/detect``.

Each segment runs through the full privacy ``Pipeline`` (safety sweep on) under
the requested policy profile. Findings carry offsets into the exact text sent,
the canonical label, an optional subtype, the score and the policy class from
``labels.LABEL_METADATA``. Span text is never returned or logged.

Under the AU locale a birth date or record number detected after its label in
one segment also tags its unlabelled copies in every other segment of the same
request (see ``au_records.propagate_labelled_values``). The anchors are
collected in a first pass and only segments that contain a copy of another
segment's anchor are run a second time. Anchor values stay in memory for the
request and are never logged or returned.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Mapping
from typing import Any

from openmed.core.au_labels import display_labels_for
from openmed.core.au_records import LabelledAnchor, labelled_value_exchange
from openmed.core.errors import PolicyError
from openmed.core.labels import LABEL_METADATA
from openmed.core.pipeline import ModelDetector, Pipeline
from openmed.core.policy import PolicyProfile, load_policy
from openmed.core.provenance import LEAN_MODEL_ENV

from .errors import LeanError
from .schemas import DetectCell, DetectRequest
from .table_columns import table_column_findings

logger = logging.getLogger("openmed.service.lean")

__all__ = ["detect_segments", "get_shared_loader"]

_LOADER: Any = None
_LOADER_LOCK = threading.Lock()
# The shared loader's model pipelines and fast tokenizers are not safe to call
# from several threads at once, and sync endpoints run on the FastAPI
# threadpool. Default-path inference is therefore serialised per process;
# throughput scales with ``uvicorn --workers``. An injected detector is the
# caller's responsibility and is not serialised.
_INFER_LOCK = threading.Lock()


def _make_loader() -> Any:
    from openmed.core.models import ModelLoader

    return ModelLoader()


def get_shared_loader() -> Any:
    """Return the process-wide ``ModelLoader``, creating it on first use.

    Reusing one loader keeps model weights and pipelines cached across
    segments and requests instead of reloading them on every call.
    """
    global _LOADER
    if _LOADER is None:
        with _LOADER_LOCK:
            if _LOADER is None:
                _LOADER = _make_loader()
    return _LOADER


def _resolve_policy(name: str) -> PolicyProfile:
    try:
        return load_policy(name)
    except PolicyError:
        raise LeanError("UNKNOWN_POLICY") from None


def _validate_segments(request: DetectRequest) -> None:
    if not request.segments:
        raise LeanError("EMPTY_DOCUMENT")
    seen: set[str] = set()
    for segment in request.segments:
        if segment.segment_id in seen:
            raise LeanError("DUPLICATE_SEGMENT_ID")
        seen.add(segment.segment_id)


def _subtype(metadata: Mapping[str, Any]) -> str | None:
    for key in ("subtype", "id_subtype"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _policy_class(canonical_label: str) -> str:
    return str(LABEL_METADATA[canonical_label]["policy_label"])


class _SegmentRun:
    """One segment's findings plus what cross-segment propagation needs."""

    __slots__ = ("findings", "anchors", "normalized_text")

    def __init__(
        self,
        findings: list[dict[str, Any]],
        anchors: Mapping[str, LabelledAnchor],
        normalized_text: str,
    ) -> None:
        self.findings = findings
        self.anchors = anchors
        self.normalized_text = normalized_text


def _segment_findings(
    pipeline: Pipeline,
    segment_id: str,
    text: str,
    *,
    preserve_whitespace: bool,
    table_header: list[str] | None = None,
    cells: list[DetectCell] | None = None,
    external_anchors: Mapping[str, LabelledAnchor] | None = None,
    display_labels: Mapping[str, str] | None = None,
) -> _SegmentRun:
    if not text.strip():
        return _SegmentRun([], {}, "")
    # With preserve_whitespace=False the pipeline indexes ``text.strip()``;
    # shift back so offsets always index the text the caller sent.
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
    with labelled_value_exchange(external_anchors) as exchange:
        result = pipeline.run(text)
    findings = []
    for span in result.spans:
        finding: dict[str, Any] = {
            "segment_id": segment_id,
            "start": span.start + shift,
            "end": span.end + shift,
            "label": span.canonical_label,
            # Spans without a score are deterministic pattern hits.
            "score": 1.0 if span.score is None else float(span.score),
            "policy_class": _policy_class(span.canonical_label),
        }
        subtype = _subtype(span.metadata or {})
        if subtype is not None:
            finding["subtype"] = subtype
        findings.append(finding)
    if table_header is not None and cells is not None:
        # In addition to, not deduplicated against, model and pattern hits.
        findings.extend(table_column_findings(segment_id, text, table_header, cells))
    if display_labels:
        # en_AU profiles show ZIPCODE as POSTCODE (see ``au_labels``).
        for finding in findings:
            finding["label"] = display_labels.get(finding["label"], finding["label"])
    findings.sort(key=lambda f: (f["start"], f["end"], f["label"]))
    return _SegmentRun(findings, dict(exchange.found), result.normalized_text)


def _needs_document_anchors(
    run: _SegmentRun, document_anchors: Mapping[str, LabelledAnchor]
) -> bool:
    # Rerun only when another segment's anchor surface occurs in this one.
    return any(
        surface not in run.anchors and surface in run.normalized_text
        for surface in document_anchors
    )


def detect_segments(
    request: DetectRequest,
    *,
    model_detector: ModelDetector | None = None,
) -> list[dict[str, Any]]:
    """Run the full detection pipeline over each segment of ``request``.

    Under the AU locale, labelled birth dates and record numbers found in any
    segment also tag their unlabelled copies in every other segment. A
    segment that holds such a copy is run a second time with the request's
    anchors; offsets always index that segment's own text.

    Args:
        request: The validated detect request.
        model_detector: Optional replacement for the PII model stage (tests and
            alternative backends). ``None`` uses the configured model
            (``OPENMED_LEAN_MODEL`` or the openmed default) through the shared
            process-wide loader. Inference on that default path is serialised
            per process by a module lock; an injected detector is not.

    Returns:
        Finding mappings in segment order, then by offset. No span text.

    Raises:
        LeanError: ``UNKNOWN_POLICY``, ``EMPTY_DOCUMENT``,
            ``DUPLICATE_SEGMENT_ID`` for invalid requests, or ``DETECT_FAILED``
            when the pipeline raises. Exception messages are discarded.
    """
    policy = _resolve_policy(request.policy)
    _validate_segments(request)
    try:
        pipeline = Pipeline(
            model_name=os.environ.get(LEAN_MODEL_ENV, "").strip() or None,
            policy=policy,
            model_detector=model_detector,
            loader=get_shared_loader() if model_detector is None else None,
            preserve_whitespace=request.preserve_whitespace,
            use_safety_sweep=True,
        )
        findings: list[dict[str, Any]] = []
        with _INFER_LOCK if model_detector is None else contextlib.nullcontext():

            def run_segment(segment: Any, external: Any = None) -> _SegmentRun:
                return _segment_findings(
                    pipeline,
                    segment.segment_id,
                    segment.text,
                    preserve_whitespace=request.preserve_whitespace,
                    table_header=segment.table_header,
                    cells=segment.cells,
                    external_anchors=external,
                    display_labels=display_labels_for(policy),
                )

            # Pass 1: each segment on its own, collecting labelled anchors.
            runs = [run_segment(segment) for segment in request.segments]
            document_anchors: dict[str, LabelledAnchor] = {}
            for run in runs:
                for surface, anchor in run.anchors.items():
                    document_anchors.setdefault(surface, anchor)
            # Pass 2: a segment holding a copy of another segment's labelled
            # value runs again with the document's anchors.
            for index, segment in enumerate(request.segments):
                if _needs_document_anchors(runs[index], document_anchors):
                    runs[index] = run_segment(segment, document_anchors)
            for run in runs:
                findings.extend(run.findings)
    except Exception as exc:
        logger.warning(
            "detect failed policy=%s type=%s", policy.name, type(exc).__name__
        )
        raise LeanError("DETECT_FAILED") from None
    return findings
