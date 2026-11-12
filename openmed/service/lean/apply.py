"""Span rendering for ``POST /v1/apply``.

A thin adapter over ``openmed.core.apply_spans.apply_spans``: library error
codes are mapped straight through, and any unexpected failure becomes
``APPLY_FAILED`` with only the exception type logged. Segment text, span
text, patient keys and secrets are never logged.
"""

from __future__ import annotations

import logging
from typing import Any

from openmed.core.apply_spans import ApplySpansError, apply_spans
from openmed.core.au_labels import canonical_input_label

from .errors import LeanError
from .schemas import ApplyRequest

logger = logging.getLogger("openmed.service.lean")

__all__ = ["apply_document", "with_canonical_labels"]


def with_canonical_labels(request: ApplyRequest) -> ApplyRequest:
    """Return ``request`` with display labels (``POSTCODE``) made canonical.

    Callers may send back the label ``/v1/detect`` showed them; rendering and
    the audit work on canonical labels (``ZIPCODE``).

    Args:
        request: The validated apply request.

    Returns:
        The same request when no span uses a display label, otherwise a copy
        with those labels replaced.
    """
    if not any(
        canonical_input_label(span.label) != span.label
        for segment in request.document
        for span in segment.spans
    ):
        return request
    document = [
        segment.model_copy(
            update={
                "spans": [
                    span.model_copy(update={"label": canonical_input_label(span.label)})
                    for span in segment.spans
                ]
            }
        )
        for segment in request.document
    ]
    return request.model_copy(update={"document": document})


def apply_document(request: ApplyRequest) -> dict[str, Any]:
    """Render the request's spans with ``apply_spans``.

    Args:
        request: The validated apply request.

    Returns:
        ``segments``, ``spans``, ``merges`` and ``provenance``.

    Raises:
        LeanError: The library's ``error_code`` for fail-closed input, or
            ``APPLY_FAILED`` for any unexpected failure.
    """
    document = [
        {
            "segment_id": segment.segment_id,
            "text": segment.text,
            "spans": [span.model_dump() for span in segment.spans],
        }
        for segment in request.document
    ]
    try:
        result = apply_spans(
            document,
            treatment=request.treatment,
            policy=request.policy,
            offset_days=request.offset_days,
            patient_key=request.patient_key,
            secret=request.secret,
        )
    except ApplySpansError as exc:
        raise LeanError(exc.error_code) from None
    except Exception as exc:
        logger.warning("apply failed type=%s", type(exc).__name__)
        raise LeanError("APPLY_FAILED") from None
    return result.to_dict()
