"""Detection over caller-supplied segments for ``POST /v1/detect``.

Each segment runs through the full privacy ``Pipeline`` (safety sweep on) under
the requested policy profile. Findings carry offsets into the exact text sent,
the canonical label, an optional subtype, the score and the policy class from
``labels.LABEL_METADATA``. Span text is never returned or logged.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from typing import Any

from openmed.core.errors import PolicyError
from openmed.core.labels import LABEL_METADATA
from openmed.core.pipeline import ModelDetector, Pipeline
from openmed.core.policy import PolicyProfile, load_policy
from openmed.core.provenance import LEAN_MODEL_ENV

from .errors import LeanError
from .schemas import DetectRequest

logger = logging.getLogger("openmed.service.lean")

__all__ = ["detect_segments", "get_shared_loader"]

_LOADER: Any = None
_LOADER_LOCK = threading.Lock()


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


def _segment_findings(
    pipeline: Pipeline,
    segment_id: str,
    text: str,
    *,
    preserve_whitespace: bool,
) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    # With preserve_whitespace=False the pipeline indexes ``text.strip()``;
    # shift back so offsets always index the text the caller sent.
    shift = 0 if preserve_whitespace else len(text) - len(text.lstrip())
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
    findings.sort(key=lambda f: (f["start"], f["end"], f["label"]))
    return findings


def detect_segments(
    request: DetectRequest,
    *,
    model_detector: ModelDetector | None = None,
) -> list[dict[str, Any]]:
    """Run the full detection pipeline over each segment of ``request``.

    Args:
        request: The validated detect request.
        model_detector: Optional replacement for the PII model stage (tests and
            alternative backends). ``None`` uses the configured model
            (``OPENMED_LEAN_MODEL`` or the openmed default) through the shared
            process-wide loader.

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
        for segment in request.segments:
            findings.extend(
                _segment_findings(
                    pipeline,
                    segment.segment_id,
                    segment.text,
                    preserve_whitespace=request.preserve_whitespace,
                )
            )
    except Exception as exc:
        logger.warning(
            "detect failed policy=%s type=%s", policy.name, type(exc).__name__
        )
        raise LeanError("DETECT_FAILED") from None
    return findings
