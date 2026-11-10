"""Signed audit report for one ``apply_spans`` render.

The report reuses :class:`openmed.core.audit.AuditReport`. It is built from the
request document and ``ApplySpansResult.to_dict()``; ``apply_spans`` itself is
unchanged. The report carries offsets, keyed hashes, labels, actions and
provenance only: no span text, surrogate, context, patient key, secret or
date-shift offset is ever recorded.

Join rule: segments are joined with :data:`SEGMENT_JOIN` in document order.
``input_hash`` is ``"hmac-sha256:"`` plus the HMAC-SHA256, under the audit
key, of the joined input texts: an unkeyed hash of the input would let anyone
holding the released output brute-force masked names and the shift offset.
``deidentified_text_hash`` is the plain ``hash_text`` of the joined output
texts, and ``document_length`` is the joined output length. Span
offsets index the joined output. Because a segment may itself contain the join
character, ``resolved_profile["segments"]`` records each segment's id, input
length and output length.

Canonical serialisation: the signature is ``HMAC-SHA256(key, C(audit without
"signature"))`` and ``repro_hash`` is ``"sha256:" + SHA-256(C(audit without
"signature" and "repro_hash"))``, where ``C`` is ``json.dumps(sort_keys=True,
separators=(",", ":"), ensure_ascii=True, allow_nan=False)`` encoded as UTF-8.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from typing import Any, Final

from .audit import AuditReport, AuditSpan, DetectorInfo, hash_text, manifest_hash
from .policy import load_policy
from .provenance import provenance

__all__ = [
    "AUDIT_KEY_ENV",
    "AUDIT_KEY_ID",
    "SEGMENT_JOIN",
    "build_apply_audit",
    "joined_text",
]

SEGMENT_JOIN: Final = "\n"
AUDIT_KEY_ENV: Final = "OPENMED_AUDIT_KEY"
AUDIT_KEY_ID: Final = "openmed-lean"

# apply's ``mark`` is the audit vocabulary's ``mask`` (core/schemas/span.py).
_AUDIT_ACTIONS: Final[Mapping[str, str]] = {
    "mark": "mask",
    "shift": "shift",
    "keep": "keep",
}
_CALLER_SOURCE: Final = "caller"
_SOURCE_ORDER: Final[tuple[str, ...]] = (
    "model",
    "pattern",
    "table-column",
    "manual",
    _CALLER_SOURCE,
)
_RULE_DETECTORS: Final[Mapping[str, tuple[str, str]]] = {
    "pattern": ("openmed.pii_patterns", "rules"),
    "table-column": ("openmed.table_columns", "rules"),
    "manual": ("manual", "manual"),
    _CALLER_SOURCE: ("caller", "caller"),
}


def joined_text(segments: Sequence[Mapping[str, str]]) -> str:
    """Join segment texts with :data:`SEGMENT_JOIN` in document order.

    Args:
        segments: Mappings with a ``text`` key.

    Returns:
        The joined text the audit hashes and indexes.
    """
    return SEGMENT_JOIN.join(segment["text"] for segment in segments)


def build_apply_audit(
    document: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    *,
    policy: str,
    key: str,
    treatment: str = "mark",
) -> AuditReport:
    """Build and sign the audit report for one apply render.

    Args:
        document: The request segments (``segment_id``, ``text``, ``spans``),
            exactly as passed to ``apply_spans``. Spans may carry optional
            ``source`` and ``score``.
        result: ``ApplySpansResult.to_dict()`` for that document.
        policy: The request policy name.
        key: The HMAC key. It signs the report and keys every span
            ``text_hash``.
        treatment: The rendered treatment, recorded in ``resolved_profile``.

    Returns:
        A signed :class:`AuditReport` with ``key_id`` :data:`AUDIT_KEY_ID`.

    Raises:
        ValueError: If ``key`` is empty.
        KeyError: If ``result`` names a span or segment not in ``document``.
    """
    if not isinstance(key, str) or not key:
        raise ValueError("A non-empty audit key is required")
    key_bytes = key.encode("utf-8")
    profile = load_policy(policy)
    out_segments = result["segments"]

    input_texts = {segment["segment_id"]: segment["text"] for segment in document}
    input_spans: dict[str, Mapping[str, Any]] = {
        span["span_id"]: span
        for segment in document
        for span in segment.get("spans") or ()
    }
    merged_ids = {
        merge["kept_span_id"]: list(merge["merged_span_ids"])
        for merge in result["merges"]
    }
    bases: dict[str, int] = {}
    cursor = 0
    for segment in out_segments:
        bases[segment["segment_id"]] = cursor
        cursor += len(segment["text"]) + len(SEGMENT_JOIN)

    spans: list[AuditSpan] = []
    for out in result["spans"]:
        span_id = out["span_id"]
        segment_id = out["segment_id"]
        kept = input_spans[span_id]
        merged = merged_ids.get(span_id, [])
        members = [kept, *(input_spans[member] for member in merged)]
        start = min(member["start"] for member in members)
        end = max(member["end"] for member in members)
        source_text = input_texts[segment_id][start:end]
        text_hash = _keyed_hash(key_bytes, source_text)
        score = kept.get("score")
        base = bases[segment_id]
        spans.append(
            AuditSpan(
                start=base + out["out_start"],
                end=base + out["out_end"],
                label=kept["label"],
                canonical_label=kept["label"],
                sources=[kept.get("source") or _CALLER_SOURCE],
                confidence=1.0 if score is None else float(score),
                threshold=0.0,
                action=_AUDIT_ACTIONS[out["action"]],
                surrogate=None,
                text_hash=text_hash,
                evidence={
                    "span_id": span_id,
                    "segment_id": segment_id,
                    "apply_action": out["action"],
                    "merged_span_ids": merged,
                },
                context={},
            )
        )

    resolved_profile = {
        **profile.to_dict(),
        "treatment": treatment,
        "policy_fingerprint": result["provenance"]["policy_fingerprint"],
        "segments": [
            {
                "segment_id": segment_in["segment_id"],
                "input_length": len(segment_in["text"]),
                "output_length": len(segment_out["text"]),
            }
            for segment_in, segment_out in zip(document, out_segments, strict=True)
        ],
    }
    joined_out = joined_text(out_segments)
    report = AuditReport(
        policy=policy,
        resolved_profile=resolved_profile,
        detectors=_detectors({source for span in spans for source in span.sources}),
        safety_sweep={"enabled": False},
        spans=spans,
        thresholds={},
        residual_risk={
            "keep_span_count": sum(1 for s in spans if s.action == "keep"),
            "shift_span_count": sum(1 for s in spans if s.action == "shift"),
        },
        openmed_version=result["provenance"]["openmed_version"],
        manifest_hash=manifest_hash(),
        document_length=len(joined_out),
        input_hash=_keyed_hash(key_bytes, joined_text(document)),
        deidentified_text_hash=hash_text(joined_out),
    )
    return report.sign(key, key_id=AUDIT_KEY_ID)


def _keyed_hash(key: bytes, text: str) -> str:
    digest = hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def _detectors(sources: set[str]) -> list[DetectorInfo]:
    ordered = [s for s in _SOURCE_ORDER if s in sources]
    ordered += sorted(sources - set(_SOURCE_ORDER))
    detectors: list[DetectorInfo] = []
    for source in ordered:
        if source == "model":
            detectors.extend(
                DetectorInfo(source="model", model_id=model, model_format="model")
                for model in provenance()["models"]
            )
            continue
        model_id, model_format = _RULE_DETECTORS.get(source, (source, source))
        detectors.append(
            DetectorInfo(source=source, model_id=model_id, model_format=model_format)
        )
    return detectors
