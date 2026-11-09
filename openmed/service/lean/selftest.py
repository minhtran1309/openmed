"""Consumer probe for ``POST /v1/selftest``.

Runs a fixed synthetic probe through detect (``ccia_l3``) and apply (``mark``,
``ccia_l1``, a fixed day offset) and reports pass/fail per named check. The
response carries check names and values only: no text, spans or counts. The
probe values are never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from openmed.core.pipeline import ModelDetector

from .apply import apply_document
from .detect import detect_segments
from .errors import LeanError, status_for
from .schemas import (
    ApplyRequest,
    ApplySegmentIn,
    ApplySpanIn,
    DetectRequest,
    DetectSegment,
)

logger = logging.getLogger("openmed.service.lean")

__all__ = [
    "CHECK_NAMES",
    "SELFTEST_OFFSET_DAYS",
    "SELFTEST_TEXT",
    "evaluate_checks",
    "expected_shifted_date",
    "run_selftest",
]

# Synthetic and checksum-valid; identical to the consumer's ``SELFTEST_TEXT``.
SELFTEST_TEXT = (
    "Patient Jane Citizen, Medicare 2123 45670 1, ABN 51 824 753 556, "
    "ph 0412 345 678, seen 04/07/2023, diagnosed 2019."
)
SELFTEST_OFFSET_DAYS = 10
_SEGMENT_ID = "probe"
_DETECT_POLICY = "ccia_l3"
_APPLY_POLICY = "ccia_l1"
_FULL_DATE = "04/07/2023"
_YEAR = "2019"
_MEDICARE = "2123 45670 1"
_ABN = "51 824 753 556"
_PHONE = "0412 345 678"

CHECK_NAMES: tuple[str, ...] = (
    "medicare",
    "abn",
    "phone",
    "date_shift",
    "person_tag",
    "year_tag",
)


def expected_shifted_date(offset_days: int) -> str:
    """Return the probe's full date shifted by ``offset_days`` (day-first)."""
    original = datetime.strptime(_FULL_DATE, "%d/%m/%Y")
    return (original + timedelta(days=offset_days)).strftime("%d/%m/%Y")


def evaluate_checks(
    findings: Sequence[Mapping[str, Any]],
    output_text: str,
    *,
    shifted_date: str,
) -> dict[str, str]:
    """Evaluate the six probe checks. Pure: no I/O, no logging.

    Args:
        findings: Detect findings for the probe (``label`` and optional
            ``subtype``).
        output_text: The probe text rendered by apply.
        shifted_date: The exact expected shifted form of the full date.

    Returns:
        ``{check_name: "pass" | "fail"}`` for every name in ``CHECK_NAMES``.
    """
    subtypes = {f.get("subtype") for f in findings}

    def found(subtype: str, raw: str) -> bool:
        # Detected and the raw probe value did not survive into the output.
        return subtype in subtypes and raw not in output_text

    results = {
        "medicare": found("AU_MEDICARE", _MEDICARE),
        "abn": found("AU_ABN", _ABN),
        "phone": found("AU_PHONE", _PHONE),
        "date_shift": shifted_date in output_text and _FULL_DATE not in output_text,
        # The whole name must be tagged in place; a partial tag leaves a part.
        "person_tag": (
            "Patient [PERSON_1]," in output_text
            and "Jane" not in output_text
            and "Citizen" not in output_text
        ),
        # The year (not the full date) must carry the first DATE tag.
        "year_tag": "diagnosed [DATE_1]." in output_text and _YEAR not in output_text,
    }
    return {name: "pass" if results[name] else "fail" for name in CHECK_NAMES}


def run_selftest(model_detector: ModelDetector | None = None) -> dict[str, str]:
    """Run the probe through detect then apply and evaluate the checks.

    Args:
        model_detector: Injected PII model stage, as for ``/v1/detect``. The
            ``person_tag`` and ``year_tag`` checks depend on it.

    Returns:
        ``{check_name: "pass" | "fail"}``.

    Raises:
        LeanError: ``DETECT_FAILED`` or ``APPLY_FAILED`` on unexpected failure.
    """
    findings = _guarded("DETECT_FAILED", _detect, model_detector)
    output_text = _guarded("APPLY_FAILED", _apply, findings)
    return evaluate_checks(
        findings,
        output_text,
        shifted_date=expected_shifted_date(SELFTEST_OFFSET_DAYS),
    )


def _guarded(code: str, stage: Any, *args: Any) -> Any:
    """Run a stage; any non-5xx failure is a server fault, since the probe is fixed."""
    try:
        return stage(*args)
    except LeanError as exc:
        if status_for(exc.error_code) >= 500:
            raise
        logger.warning("selftest %s type=%s", code, type(exc).__name__)
        raise LeanError(code) from None
    except Exception as exc:
        logger.warning("selftest %s type=%s", code, type(exc).__name__)
        raise LeanError(code) from None


def _detect(model_detector: ModelDetector | None) -> list[dict[str, Any]]:
    return detect_segments(
        DetectRequest(
            segments=[DetectSegment(segment_id=_SEGMENT_ID, text=SELFTEST_TEXT)],
            policy=_DETECT_POLICY,
        ),
        model_detector=model_detector,
    )


def _apply(findings: Sequence[Mapping[str, Any]]) -> str:
    spans = [
        ApplySpanIn(
            span_id=f"f{index}",
            start=finding["start"],
            end=finding["end"],
            label=finding["label"],
            subtype=finding.get("subtype"),
        )
        for index, finding in enumerate(findings)
    ]
    rendered = apply_document(
        ApplyRequest(
            document=[
                ApplySegmentIn(segment_id=_SEGMENT_ID, text=SELFTEST_TEXT, spans=spans)
            ],
            treatment="mark",
            policy=_APPLY_POLICY,
            offset_days=SELFTEST_OFFSET_DAYS,
        )
    )
    return rendered["segments"][0]["text"]
