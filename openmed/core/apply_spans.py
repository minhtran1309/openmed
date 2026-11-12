"""Render caller-supplied spans over a document: the public apply operation.

``apply_spans`` never detects. It takes a document (ordered segments, each
with its text and the spans the caller recorded), resolves each span's action
from a policy profile and renders the treatment: ``mark``, ``replace``,
``generalise`` or ``keep``. Any other treatment fails closed with
``UNSUPPORTED_TREATMENT``.

Rendering rules under ``mark``:

* ``keep`` spans are left unchanged and reported.
* ``shift`` on ``DATE`` moves a full date by
  the explicit ``offset_days`` or by
  ``date_shift.stable_offset_for(patient_key, 365, secret)``. Numeric dates
  are parsed day-first (``LOCALE_DATE_ORDER["en-au"]``); the original format
  is preserved. Year-only, month-year and unparseable dates fall back to a tag.
* ``DATE_OF_BIRTH`` is tagged, never shifted, under ``mark`` (spec: "DOB ->
  tag under mark").
* Every other action, including ``shift`` on a non-date label, renders a
  numbered tag ``[LABEL_n]``. Numbers are assigned per label by first
  appearance across the whole document, matching values casefolded with
  whitespace collapsed, and restart on every call. Name parts render under a
  ``PERSON`` prefix with their own counters: ``FIRST_NAME`` as
  ``[PERSON_FN_n]``, ``MIDDLE_NAME`` as ``[PERSON_MN_n]`` and ``LAST_NAME`` as
  ``[PERSON_LN_n]``; ``PERSON`` stays ``[PERSON_n]``.

Overlapping spans in a segment form a cluster. The kept span has the earliest
start, then the greatest length, then the strongest action, then the earliest
input position. The cluster is rendered over its full extent with the
strongest action of any member, so no part of a merged span survives; the
others are reported in ``merges``.

Rendering rules under ``replace`` (mt.3 E2, first match wins per cluster):

1. A single-span cluster whose span carries a caller ``replacement`` renders
   that text verbatim (action ``replacement``). A merged cluster ignores
   member replacements.
2. Policy ``keep`` is kept.
3. ``shift`` on ``DATE`` and ``DATE_OF_BIRTH`` shifts as under ``mark``; a
   date that cannot be shifted renders its tag (action ``mark``).
4. A cluster whose labels are all in
   :data:`~openmed.core.apply_surrogates.SURROGATE_LABELS` renders the
   patient's surrogate (action ``replace``), or its tag when none can be
   given (action ``mark``).
5. Anything else renders a tag.

Rendering rules under ``generalise`` and ``keep`` (mt.3 E2, first match wins):

1. A single-span cluster with a caller ``replacement`` renders it under
   ``generalise`` (action ``replacement``). Under ``keep`` it keeps the source
   (action ``keep``) only when the label is a quasi-identifier
   (``policy_label_for``) outside :data:`DATE_LABELS`; any other label
   resolves by the rows below.
2. Policy ``keep`` is kept, except a cancer cluster under ``generalise``.
3. Under ``generalise``, a cancer cluster (kept label in
   :data:`CANCER_LABELS`, or in :data:`ONCOTREE_LOOKUP_LABELS` with text that
   :func:`~openmed.core.oncotree.is_cancer_match` accepts) renders its
   OncoTree group (action ``generalise``), or its tag when there is none
   (action ``mark``).
4. ``shift`` on ``DATE`` shifts under both; a ``DATE_OF_BIRTH`` shifts under
   ``keep`` and renders the year of the shifted date under ``generalise``.
5. Under ``generalise``, ``PHONE``, ``STREET_ADDRESS``/``LOCATION`` and
   ``ORGANIZATION`` render their :mod:`~openmed.core.apply_generalise` value.
6. Everything else, and every generaliser that returns ``None``, renders a
   tag (action ``mark``).

``generalise`` and ``keep`` need ``patient_key`` with ``secret`` (or
``offset_days``) only when a date shift is needed (``DATE_SHIFT_KEY_REQUIRED``).
``replace`` always requires ``patient_key`` with ``secret``
(``SURROGATE_KEY_REQUIRED``); the service keeps no state, and cross-document
consistency is derived from them on every call (see
:mod:`openmed.core.apply_surrogates`).

The result carries no original span text, and errors carry only a fixed code.
Patient keys, secrets, surrogates and replacement texts are never logged,
stored or returned outside the rendered segments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from openmed.__about__ import __version__

from .apply_surrogates import SURROGATE_LABELS, PatientSurrogates, restates_source
from .au_labels import display_labels_for
from .date_shift import DEFAULT_DATE_SHIFT_MAX_DAYS, stable_offset_for
from .errors import InputError, PolicyError
from .labels import LABEL_METADATA, QUASI_IDENTIFIER, policy_label_for
from .locale_formats import parse_date
from .oncotree import generalise_cancer, is_cancer_match
from .policy import PolicyProfile, load_policy
from .provenance import policy_fingerprint
from .redaction_strength import ACTION_STRENGTH

__all__ = [
    "CANCER_LABELS",
    "DATE_LABELS",
    "MARK_SHIFT_LABELS",
    "ONCOTREE_LOOKUP_LABELS",
    "SHIFT_LABELS_BY_TREATMENT",
    "SUPPORTED_TREATMENTS",
    "TREATMENTS",
    "ApplySpansError",
    "ApplySpansResult",
    "apply_spans",
]

TREATMENTS: Final[tuple[str, ...]] = ("mark", "replace", "generalise", "keep")
SUPPORTED_TREATMENTS: Final[tuple[str, ...]] = TREATMENTS
DATE_LABELS: Final[frozenset[str]] = frozenset({"DATE", "DATE_OF_BIRTH"})
# spec.md Treatment fallbacks: "dates shift under every treatment; DOB -> tag
# under mark", and "DOB ... shifted under replace" (mt.3 E7). These labels
# render ``shift`` under each treatment.
SHIFT_LABELS_BY_TREATMENT: Final[Mapping[str, frozenset[str]]] = {
    "mark": frozenset({"DATE"}),
    "replace": DATE_LABELS,
    "generalise": frozenset({"DATE"}),
    "keep": DATE_LABELS,
}
MARK_SHIFT_LABELS: Final[frozenset[str]] = SHIFT_LABELS_BY_TREATMENT["mark"]
# mt.3 E11: a cluster is a cancer cluster when its kept label is ``CANCER``, or
# ``DISEASE``/``CONDITION`` whose text resolves in OncoTree.
CANCER_LABELS: Final[frozenset[str]] = frozenset({"CANCER"})
ONCOTREE_LOOKUP_LABELS: Final[frozenset[str]] = frozenset({"DISEASE", "CONDITION"})
DATE_LOCALE: Final = "en-au"
MAX_OFFSET_DAYS: Final = 36500

_ACTION_KEEP: Final = "keep"
_ACTION_MARK: Final = "mark"
_ACTION_SHIFT: Final = "shift"
_ACTION_REPLACE: Final = "replace"
_ACTION_REPLACEMENT: Final = "replacement"
_ACTION_GENERALISE: Final = "generalise"
# Treatments under which a single span's caller ``replacement`` is rendered.
_REPLACEMENT_TREATMENTS: Final[frozenset[str]] = frozenset({"replace", "generalise"})
# Treatments that render surrogates and so always need a patient key.
_SURROGATE_TREATMENTS: Final[frozenset[str]] = frozenset({"replace"})

_MESSAGES: Final[Mapping[str, str]] = {
    "INVALID_REQUEST": "The apply request is malformed. Correct it and retry.",
    "UNKNOWN_POLICY": "The policy is not known. Pass a name from list_policies().",
    "EMPTY_DOCUMENT": "The document has no segments. Pass at least one segment.",
    "DUPLICATE_SEGMENT_ID": "Segment ids must be unique within a document.",
    "UNKNOWN_SEGMENT_ID": "A span names a segment id other than its own segment.",
    "SPAN_OUT_OF_RANGE": "A span offset lies outside its segment text.",
    "SPAN_INVERTED": "A span must have start < end.",
    "UNKNOWN_LABEL": "A span label is not a canonical OpenMed label.",
    "UNSUPPORTED_TREATMENT": (
        "The treatment is not supported. Use 'mark', 'replace', 'generalise' or 'keep'."
    ),
    "DATE_SHIFT_KEY_REQUIRED": (
        "A date shift is needed. Pass offset_days or patient_key with secret."
    ),
    "SURROGATE_KEY_REQUIRED": (
        "The replace treatment needs patient_key with secret, not offset_days."
    ),
}


class ApplySpansError(InputError):
    """Fail-closed apply error carrying a fixed ``error_code``.

    The message and details are fixed per code and never include span text,
    segment text, patient keys or secrets.

    Attributes:
        error_code: A member of the lean service error vocabulary.
    """

    code = "apply_spans_error"
    reason = "apply_rejected"

    def __init__(self, error_code: str) -> None:
        if error_code not in _MESSAGES:
            raise ValueError("error_code is not an apply_spans error code")
        super().__init__(_MESSAGES[error_code], details={"error_code": error_code})
        self.error_code = error_code


@dataclass(frozen=True)
class ApplySpansResult:
    """Rendered document and per-span report from :func:`apply_spans`.

    Attributes:
        segments: ``[{segment_id, text}]`` in input order.
        spans: ``[{span_id, segment_id, action, out_start, out_end}]``
            covering every input span (directly or via merges), in segment
            order then output offset. ``action`` is the rendering actually
            applied: ``mark``, ``shift``, ``keep``, ``replace`` (an openmed
            surrogate), ``replacement`` (the caller's text) or
            ``generalise`` (an openmed coarser value).
        merges: ``[{kept_span_id, merged_span_ids}]`` for each overlap cluster.
        provenance: ``{openmed_version, policy_fingerprint}``.
    """

    segments: list[dict[str, str]]
    spans: list[dict[str, Any]]
    merges: list[dict[str, Any]]
    provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping of the result."""
        return {
            "segments": [dict(s) for s in self.segments],
            "spans": [dict(s) for s in self.spans],
            "merges": [
                {
                    "kept_span_id": m["kept_span_id"],
                    "merged_span_ids": list(m["merged_span_ids"]),
                }
                for m in self.merges
            ],
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class _Span:
    span_id: str
    start: int
    end: int
    label: str
    policy_action: str
    index: int
    replacement: str | None = None


@dataclass(frozen=True)
class _Cluster:
    kept: _Span
    merged: tuple[_Span, ...]
    start: int
    end: int
    policy_action: str


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_spans(
    document: Sequence[Mapping[str, Any]],
    *,
    treatment: str,
    policy: str | PolicyProfile,
    offset_days: int | None = None,
    patient_key: str | None = None,
    secret: str | None = None,
) -> ApplySpansResult:
    """Render caller-supplied spans over a document. Never detects.

    Args:
        document: Ordered segments, each a mapping with ``segment_id`` (non-empty
            str), ``text`` (str) and ``spans`` (list). Each span has
            ``span_id``, ``start``, ``end`` (code-point offsets into ``text``)
            and a canonical ``label``; optional ``segment_id`` (must equal the
            enclosing segment's), ``subtype`` and ``replacement`` (a non-empty
            str; rendered verbatim for a single-span cluster under
            ``replace`` and ``generalise``, unless it normalises to or
            contains its source, which renders the tag; under ``keep`` the
            source is kept for a non-date quasi-identifier only; ignored
            under ``mark``). Offsets are Python code points, not graphemes or
            UTF-16 units; callers must supply grapheme-aligned spans, since a
            span edge inside a grapheme cluster is rendered as given.
        treatment: ``"mark"``, ``"replace"``, ``"generalise"`` or
            ``"keep"``. Any other value raises ``UNSUPPORTED_TREATMENT``.
        policy: Policy name or loaded ``PolicyProfile`` resolving each label's
            action.
        offset_days: Explicit non-zero day offset for date shifting.
        patient_key: Stable patient key; requires ``secret`` and excludes
            ``offset_days``. Required under ``replace``, where it also keys
            the per-patient surrogates; there a missing or half key (only
            one of ``patient_key`` and ``secret``) is
            ``SURROGATE_KEY_REQUIRED``.
        secret: HMAC secret paired with ``patient_key``.

    Returns:
        The rendered segments, per-span report, merges and provenance.

    Raises:
        ApplySpansError: With ``error_code`` ``UNSUPPORTED_TREATMENT``,
            ``UNKNOWN_POLICY``, ``INVALID_REQUEST``, ``EMPTY_DOCUMENT``,
            ``DUPLICATE_SEGMENT_ID``, ``UNKNOWN_SEGMENT_ID``,
            ``SPAN_OUT_OF_RANGE``, ``SPAN_INVERTED``, ``UNKNOWN_LABEL``,
            ``DATE_SHIFT_KEY_REQUIRED`` or ``SURROGATE_KEY_REQUIRED``.
    """
    if not isinstance(treatment, str) or treatment not in SUPPORTED_TREATMENTS:
        raise ApplySpansError("UNSUPPORTED_TREATMENT")
    profile = _resolve_policy(policy)
    if (
        treatment in _SURROGATE_TREATMENTS
        and offset_days is None
        and (patient_key is None) != (secret is None)
    ):
        # A half key under ``replace`` is a missing key, not a malformed one.
        raise ApplySpansError("SURROGATE_KEY_REQUIRED")
    _validate_shift_params(offset_days, patient_key, secret)
    surrogates: PatientSurrogates | None = None
    if treatment in _SURROGATE_TREATMENTS:
        if patient_key is None or secret is None:
            raise ApplySpansError("SURROGATE_KEY_REQUIRED")
    segments = _validate_document(document, profile)

    clustered = [(seg_id, text, _cluster(spans)) for seg_id, text, spans in segments]
    shift_needed = any(
        _needs_offset(
            cluster,
            treatment,
            _render_action(cluster, treatment, text[cluster.start : cluster.end]),
        )
        for _, text, clusters in clustered
        for cluster in clusters
    )
    offset: int | None = None
    if shift_needed:
        offset = _resolve_offset(offset_days, patient_key, secret)
    if treatment in _SURROGATE_TREATMENTS:
        # Request-local: dropped when this call returns (mt.3 E5).
        assert patient_key is not None and secret is not None
        surrogates = PatientSurrogates(patient_key=patient_key, secret=secret)
        # No surrogate may restate any source of the call (spans and merged
        # cluster extents), under any label.
        surrogates.reserve(
            (text[span.start : span.end], span.label)
            for _, text, spans in segments
            for span in spans
        )
        surrogates.reserve(
            (text[cluster.start : cluster.end], cluster.kept.label)
            for _, text, clusters in clustered
            for cluster in clusters
        )

    numbering: dict[str, dict[str, int]] = {}
    # en_AU profiles tag ZIPCODE as [POSTCODE_n] (see ``au_labels``).
    tag_names = {**_TAG_NAMES, **display_labels_for(profile)}
    out_segments: list[dict[str, str]] = []
    out_spans: list[dict[str, Any]] = []
    merges: list[dict[str, Any]] = []
    for segment_id, text, clusters in clustered:
        pieces: list[str] = []
        cursor = 0
        out_len = 0
        for cluster in clusters:
            before = text[cursor : cluster.start]
            pieces.append(before)
            out_len += len(before)
            source = text[cluster.start : cluster.end]
            action, rendered = _render(
                cluster, source, offset, numbering, treatment, surrogates, tag_names
            )
            pieces.append(rendered)
            out_spans.append(
                {
                    "span_id": cluster.kept.span_id,
                    "segment_id": segment_id,
                    "action": action,
                    "out_start": out_len,
                    "out_end": out_len + len(rendered),
                }
            )
            out_len += len(rendered)
            cursor = cluster.end
            if cluster.merged:
                merges.append(
                    {
                        "kept_span_id": cluster.kept.span_id,
                        "merged_span_ids": [s.span_id for s in cluster.merged],
                    }
                )
        pieces.append(text[cursor:])
        out_segments.append({"segment_id": segment_id, "text": "".join(pieces)})

    return ApplySpansResult(
        segments=out_segments,
        spans=out_spans,
        merges=merges,
        provenance={
            "openmed_version": __version__,
            "policy_fingerprint": policy_fingerprint(profile),
        },
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _resolve_policy(policy: Any) -> PolicyProfile:
    if not isinstance(policy, (str, PolicyProfile)):
        raise ApplySpansError("INVALID_REQUEST")
    try:
        return load_policy(policy)
    except PolicyError:
        raise ApplySpansError("UNKNOWN_POLICY") from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _validate_shift_params(offset_days: Any, patient_key: Any, secret: Any) -> None:
    if offset_days is not None:
        if not _is_int(offset_days) or offset_days == 0:
            raise ApplySpansError("INVALID_REQUEST")
        if abs(offset_days) > MAX_OFFSET_DAYS:
            raise ApplySpansError("INVALID_REQUEST")
        if patient_key is not None or secret is not None:
            raise ApplySpansError("INVALID_REQUEST")
        return
    if patient_key is None and secret is None:
        return
    if not (_is_nonempty_str(patient_key) and _is_nonempty_str(secret)):
        raise ApplySpansError("INVALID_REQUEST")


def _validate_document(
    document: Any, profile: PolicyProfile
) -> list[tuple[str, str, list[_Span]]]:
    if isinstance(document, (str, bytes)) or not isinstance(document, Sequence):
        raise ApplySpansError("INVALID_REQUEST")
    if not document:
        raise ApplySpansError("EMPTY_DOCUMENT")
    seen_segments: set[str] = set()
    seen_spans: set[str] = set()
    validated: list[tuple[str, str, list[_Span]]] = []
    for segment in document:
        if not isinstance(segment, Mapping):
            raise ApplySpansError("INVALID_REQUEST")
        segment_id = segment.get("segment_id")
        text = segment.get("text")
        raw_spans = segment.get("spans", [])
        if not _is_nonempty_str(segment_id) or not isinstance(text, str):
            raise ApplySpansError("INVALID_REQUEST")
        if isinstance(raw_spans, (str, bytes)) or not isinstance(raw_spans, Sequence):
            raise ApplySpansError("INVALID_REQUEST")
        if segment_id in seen_segments:
            raise ApplySpansError("DUPLICATE_SEGMENT_ID")
        seen_segments.add(segment_id)
        spans = [
            _validate_span(raw, segment_id, text, profile, seen_spans)
            for raw in raw_spans
        ]
        validated.append((segment_id, text, spans))
    return validated


def _validate_span(
    raw: Any,
    segment_id: str,
    text: str,
    profile: PolicyProfile,
    seen_spans: set[str],
) -> _Span:
    if not isinstance(raw, Mapping):
        raise ApplySpansError("INVALID_REQUEST")
    span_id = raw.get("span_id")
    start = raw.get("start")
    end = raw.get("end")
    label = raw.get("label")
    if not _is_nonempty_str(span_id) or not isinstance(label, str):
        raise ApplySpansError("INVALID_REQUEST")
    if not (_is_int(start) and _is_int(end)):
        raise ApplySpansError("INVALID_REQUEST")
    for optional in ("subtype", "replacement", "segment_id"):
        value = raw.get(optional)
        if value is not None and not isinstance(value, str):
            raise ApplySpansError("INVALID_REQUEST")
    replacement = raw.get("replacement")
    if replacement is not None and not replacement:
        raise ApplySpansError("INVALID_REQUEST")
    if span_id in seen_spans:
        raise ApplySpansError("INVALID_REQUEST")
    seen_spans.add(span_id)
    declared_segment = raw.get("segment_id")
    if declared_segment is not None and declared_segment != segment_id:
        raise ApplySpansError("UNKNOWN_SEGMENT_ID")
    if start < 0 or end < 0 or start > len(text) or end > len(text):
        raise ApplySpansError("SPAN_OUT_OF_RANGE")
    if start >= end:
        raise ApplySpansError("SPAN_INVERTED")
    if label not in LABEL_METADATA:
        raise ApplySpansError("UNKNOWN_LABEL")
    return _Span(
        span_id=span_id,
        start=start,
        end=end,
        label=label,
        policy_action=profile.action_for(label),
        index=len(seen_spans),
        replacement=replacement,
    )


def _resolve_offset(
    offset_days: int | None, patient_key: str | None, secret: str | None
) -> int:
    if offset_days is not None:
        return offset_days
    if patient_key is None or secret is None:
        raise ApplySpansError("DATE_SHIFT_KEY_REQUIRED")
    try:
        return stable_offset_for(
            patient_key, max_days=DEFAULT_DATE_SHIFT_MAX_DAYS, secret=secret
        )
    except InputError:
        raise ApplySpansError("INVALID_REQUEST") from None


# ---------------------------------------------------------------------------
# Merging and rendering
# ---------------------------------------------------------------------------


def _strength(action: str) -> int:
    # Unknown actions are treated as the strongest so they can never leak.
    return ACTION_STRENGTH.get(action, len(ACTION_STRENGTH))


def _priority(span: _Span) -> tuple[int, int, int, int]:
    return (
        span.start,
        -(span.end - span.start),
        -_strength(span.policy_action),
        span.index,
    )


def _cluster(spans: list[_Span]) -> list[_Cluster]:
    ordered = sorted(spans, key=_priority)
    groups: list[list[_Span]] = []
    group_end = -1
    for span in ordered:
        if groups and span.start < group_end:
            groups[-1].append(span)
            group_end = max(group_end, span.end)
        else:
            groups.append([span])
            group_end = span.end
    clusters = []
    for group in groups:
        strongest = max(group, key=lambda s: _strength(s.policy_action))
        clusters.append(
            _Cluster(
                kept=group[0],
                merged=tuple(group[1:]),
                start=group[0].start,
                end=max(s.end for s in group),
                policy_action=strongest.policy_action,
            )
        )
    return clusters


def _cluster_labels(cluster: _Cluster) -> frozenset[str]:
    return frozenset({cluster.kept.label, *(span.label for span in cluster.merged)})


def _keeps_replacement_source(span: _Span) -> bool:
    return (
        span.label not in DATE_LABELS
        and policy_label_for(span.label) == QUASI_IDENTIFIER
    )


def _is_cancer_cluster(cluster: _Cluster, source: str) -> bool:
    """mt.3 E11: a ``CANCER`` kept label, or a diagnosis found in OncoTree."""
    label = cluster.kept.label
    if label in CANCER_LABELS:
        return True
    return label in ONCOTREE_LOOKUP_LABELS and is_cancer_match(source, label)


def _is_dob_generalise(cluster: _Cluster, treatment: str) -> bool:
    # E2 row 5 under ``generalise``: a date cluster with a DOB that is not
    # shifted (``DATE``-only clusters shift, row 4) renders the shifted year.
    return (
        treatment == _ACTION_GENERALISE
        and cluster.policy_action == _ACTION_SHIFT
        and _cluster_labels(cluster) <= DATE_LABELS
    )


def _needs_offset(cluster: _Cluster, treatment: str, action: str) -> bool:
    if action == _ACTION_SHIFT:
        return True
    return action == _ACTION_GENERALISE and _is_dob_generalise(cluster, treatment)


def _label_generaliser(label: str) -> Any:
    # E10 per label; imported here because ``apply_generalise`` imports this
    # module for the date shift.
    from . import apply_generalise

    return {
        "PHONE": apply_generalise.generalise_phone,
        "STREET_ADDRESS": apply_generalise.generalise_address,
        "LOCATION": apply_generalise.generalise_address,
        "ORGANIZATION": apply_generalise.generalise_organisation,
    }.get(label)


def _cluster_generaliser(cluster: _Cluster) -> Any:
    """The E10 generaliser shared by every label of ``cluster``, or ``None``."""
    generalisers = {_label_generaliser(label) for label in _cluster_labels(cluster)}
    if len(generalisers) != 1:
        return None
    (generaliser,) = generalisers
    return generaliser


def _render_action(
    cluster: _Cluster, treatment: str = _ACTION_MARK, source: str | None = None
) -> str:
    """Return the planned rendering for ``cluster`` (mt.3 E2, first match wins).

    ``source`` is the cluster's text; without it no cluster is treated as a
    cancer cluster (E11). The planned ``shift``, ``replace`` and
    ``generalise`` can still fall back to a tag when the date cannot be
    shifted, no surrogate can be given or the value cannot be generalised;
    :func:`_render` reports the action actually applied.
    """
    # Row 1: a single span's caller replacement (ignored under ``mark``).
    # Under ``keep`` the source is kept only for a non-date quasi-identifier
    # (user ruling): direct identifiers fall through and are marked, and
    # dates fall through and shift.
    if not cluster.merged and cluster.kept.replacement is not None:
        if treatment in _REPLACEMENT_TREATMENTS:
            return _ACTION_REPLACEMENT
        if treatment == _ACTION_KEEP and _keeps_replacement_source(cluster.kept):
            return _ACTION_KEEP
    cancer = (
        treatment == _ACTION_GENERALISE
        and source is not None
        and _is_cancer_cluster(cluster, source)
    )
    # Row 2: policy keep, except a cancer cluster under ``generalise``.
    if cluster.policy_action == _ACTION_KEEP and not cancer:
        return _ACTION_KEEP
    # Row 3: OncoTree group under ``generalise``.
    if cancer:
        return _ACTION_GENERALISE
    # Rows 4-5: spec.md "DOB -> tag under mark"; only DATE shifts under
    # ``mark`` and ``generalise``, DOB also shifts under ``replace`` and
    # ``keep``. Every member must be shiftable: under ``mark`` a DOB merged
    # into a DATE cluster is tagged.
    labels = _cluster_labels(cluster)
    shift_labels = SHIFT_LABELS_BY_TREATMENT.get(treatment, MARK_SHIFT_LABELS)
    if cluster.policy_action == _ACTION_SHIFT and labels <= shift_labels:
        return _ACTION_SHIFT
    # Row 5 under ``generalise``: the year of the shifted DOB.
    if _is_dob_generalise(cluster, treatment):
        return _ACTION_GENERALISE
    # Row 6: surrogate-capable labels under ``replace``.
    if treatment == _ACTION_REPLACE and labels <= SURROGATE_LABELS:
        return _ACTION_REPLACE
    # Row 6 under ``generalise``: the E10 generaliser of the labels.
    if treatment == _ACTION_GENERALISE and _cluster_generaliser(cluster) is not None:
        return _ACTION_GENERALISE
    # Row 7.
    return _ACTION_MARK


def _generalise(
    cluster: _Cluster, source: str, offset: int | None, treatment: str
) -> str | None:
    """Render a planned ``generalise`` cluster, or ``None`` to fall back."""
    if _is_dob_generalise(cluster, treatment):
        if offset is None:
            return None
        from .apply_generalise import generalise_dob

        return generalise_dob(source, offset)
    if _is_cancer_cluster(cluster, source):
        # A True gate with no single OncoTree group (unmapped or ambiguous)
        # is tagged, never kept (E11).
        return generalise_cancer(source, allow_code=cluster.kept.label in CANCER_LABELS)
    generaliser = _cluster_generaliser(cluster)
    return None if generaliser is None else generaliser(source)


def _render(
    cluster: _Cluster,
    source: str,
    offset: int | None,
    numbering: dict[str, dict[str, int]],
    treatment: str = _ACTION_MARK,
    surrogates: PatientSurrogates | None = None,
    tag_names: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    action = _render_action(cluster, treatment, source)
    if action == _ACTION_REPLACEMENT:
        replacement = cluster.kept.replacement
        assert replacement is not None
        # A replacement that restates its own source removes nothing: tag it
        # so the audit never records it as replaced.
        if not restates_source(replacement, source, cluster.kept.label):
            return _ACTION_REPLACEMENT, replacement
        return _ACTION_MARK, _tag(cluster.kept.label, source, numbering, tag_names)
    if action == _ACTION_KEEP:
        return _ACTION_KEEP, source
    if action == _ACTION_SHIFT and offset is not None:
        shifted = shift_full_date(source, offset)
        if shifted is not None:
            return _ACTION_SHIFT, shifted
    if action == _ACTION_REPLACE and surrogates is not None:
        surrogate = surrogates.surrogate(source, cluster.kept.label)
        if surrogate is not None:
            return _ACTION_REPLACE, surrogate
    if action == _ACTION_GENERALISE:
        generalised = _generalise(cluster, source, offset, treatment)
        if generalised is not None:
            return _ACTION_GENERALISE, generalised
    return _ACTION_MARK, _tag(cluster.kept.label, source, numbering, tag_names)


# Tag text for name parts, so a split name still reads as one person's parts.
_TAG_NAMES: dict[str, str] = {
    "FIRST_NAME": "PERSON_FN",
    "MIDDLE_NAME": "PERSON_MN",
    "LAST_NAME": "PERSON_LN",
}


def _tag(
    label: str,
    source: str,
    numbering: dict[str, dict[str, int]],
    tag_names: Mapping[str, str] | None = None,
) -> str:
    name = (_TAG_NAMES if tag_names is None else tag_names).get(label, label)
    key = " ".join(source.split()).casefold()
    numbers = numbering.setdefault(name, {})
    number = numbers.setdefault(key, len(numbers) + 1)
    return f"[{name}_{number}]"


# ---------------------------------------------------------------------------
# Date shifting (full dates only; format preserved)
# ---------------------------------------------------------------------------

_NUMERIC_DATE_RE: Final = re.compile(
    r"(?:(?P<y1>[0-9]{4})(?P<s1>[./-])(?P<m1>[0-9]{1,2})(?P=s1)(?P<d1>[0-9]{1,2})"
    r"|(?P<d2>[0-9]{1,2})(?P<s2>[./-])(?P<m2>[0-9]{1,2})(?P=s2)(?P<y2>[0-9]{4}|[0-9]{2}))"
)
_MONTHS: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_MONTH_LOOKUP: Final[Mapping[str, int]] = {
    **{name: i + 1 for i, name in enumerate(_MONTHS)},
    **{name[:3]: i + 1 for i, name in enumerate(_MONTHS)},
    "sept": 9,
}
_DAY_MONTH_YEAR_RE: Final = re.compile(
    r"(?P<day>[0-9]{1,2})(?P<ord>st|nd|rd|th)?(?P<s1>\s+(?:of\s+)?|\s*-\s*)"
    r"(?P<mon>[A-Za-z]+)(?P<dot>\.)?(?P<s2>,?\s+|\s*-\s*)(?P<year>[0-9]{4}|[0-9]{2})",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR_RE: Final = re.compile(
    r"(?P<mon>[A-Za-z]+)(?P<dot>\.)?(?P<s1>\s+)(?P<day>[0-9]{1,2})"
    r"(?P<ord>st|nd|rd|th)?(?P<s2>,\s*|\s+)(?P<year>[0-9]{4})",
    re.IGNORECASE,
)


def shift_full_date(value: str, offset: int) -> str | None:
    """Shift a full date by ``offset`` days, preserving its format.

    Numeric dates are read day-first (or year-first with a 4-digit lead);
    month-name dates in English are accepted with an optional ordinal. Any
    other shape, including year-only and month-year values, returns ``None``.

    Args:
        value: The date text, optionally surrounded by whitespace.
        offset: Day offset.

    Returns:
        The shifted text, or ``None`` when ``value`` is not a full date or the
        result cannot be rendered in the original format.
    """
    core = value.strip()
    if not core:
        return None
    lead = value[: len(value) - len(value.lstrip())]
    trail = value[len(value.rstrip()) :]
    try:
        shifted = _shift_numeric(core, offset)
        if shifted is None:
            shifted = _shift_month_name(core, offset)
    except (ValueError, OverflowError):
        return None
    return None if shifted is None else f"{lead}{shifted}{trail}"


def _expand_two_digit_year(year: int) -> int:
    # Matches ``locale_formats`` so a rendered year round-trips the parser.
    return 2000 + year if year < 50 else 1900 + year


def _render_year(year: int, original: str) -> str | None:
    if len(original) == 2:
        short = year % 100
        return f"{short:02d}" if _expand_two_digit_year(short) == year else None
    return f"{year:04d}"


def _render_number(value: int, padded: bool) -> str:
    return f"{value:02d}" if padded else str(value)


def _shift_numeric(core: str, offset: int) -> str | None:
    match = _NUMERIC_DATE_RE.fullmatch(core)
    if match is None:
        return None
    parsed = parse_date(core, lang=DATE_LOCALE)
    if parsed.normalized is None:
        return None
    new = date(*parsed.normalized) + timedelta(days=offset)
    if match.group("y1") is not None:
        year_s, sep, month_s, day_s = match.group("y1", "s1", "m1", "d1")
    else:
        day_s, sep, month_s, year_s = match.group("d2", "s2", "m2", "y2")
    year = _render_year(new.year, year_s)
    if year is None:
        return None
    # Day and month each keep their own original width (``04/7`` -> ``14/7``).
    day = _render_number(new.day, len(day_s) == 2)
    month = _render_number(new.month, len(month_s) == 2)
    if match.group("y1") is not None:
        return sep.join((year, month, day))
    return sep.join((day, month, year))


def _ordinal(day: int, original: str) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return suffix.upper() if original.isupper() else suffix


def _render_month(month: int, original: str) -> str:
    lowered = original.lower()
    name = _MONTHS[month - 1]
    if lowered == _MONTHS[_MONTH_LOOKUP[lowered] - 1]:
        rendered = name
    elif lowered == "sept" and month == 9:
        rendered = "sept"
    else:
        rendered = name[:3]
    if original.isupper():
        return rendered.upper()
    if original.islower():
        return rendered
    return rendered.capitalize()


def _shift_month_name(core: str, offset: int) -> str | None:
    match = _DAY_MONTH_YEAR_RE.fullmatch(core)
    day_first = match is not None
    if match is None:
        match = _MONTH_DAY_YEAR_RE.fullmatch(core)
    if match is None:
        return None
    mon_s = match.group("mon")
    month = _MONTH_LOOKUP.get(mon_s.lower())
    if month is None:
        return None
    day_s, year_s = match.group("day"), match.group("year")
    year = int(year_s)
    if len(year_s) == 2:
        year = _expand_two_digit_year(year)
    new = date(year, month, int(day_s)) + timedelta(days=offset)
    year_out = _render_year(new.year, year_s)
    if year_out is None:
        return None
    day_out = _render_number(new.day, day_s.startswith("0"))
    ordinal = match.group("ord")
    if ordinal:
        day_out += _ordinal(new.day, ordinal)
    month_out = _render_month(new.month, mon_s) + (match.group("dot") or "")
    s1, s2 = match.group("s1"), match.group("s2")
    if day_first:
        return f"{day_out}{s1}{month_out}{s2}{year_out}"
    return f"{month_out}{s1}{day_out}{s2}{year_out}"
