"""Table column findings for ``POST /v1/detect`` (ticket 16).

A segment that carries ``table_header`` and ``cells`` is one table row. Each
column is classified from its header alone with
:func:`openmed.multimodal.tabular_csv.classify_columns`, extended with the AU
header vocabulary. Every non-blank cell of a direct- or quasi-identifier
column becomes a finding over the cell's own offsets. No rows are passed to
the classifier, so cell values are never sampled, and cell text is never
logged or returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openmed.core.au_records import (
    AU_TABLE_HEADER_LABELS,
    AU_TABLE_HEADER_SUBTYPES,
    table_header_key,
)
from openmed.core.labels import LABEL_METADATA
from openmed.multimodal.tabular_csv import DIRECT_ID, QUASI_ID, classify_columns

from .schemas import DetectCell

__all__ = ["TABLE_COLUMN_SOURCE", "table_column_findings"]

TABLE_COLUMN_SOURCE = "table-column"
_IDENTIFIER_CLASSES = frozenset({DIRECT_ID, QUASI_ID})


def _is_header_row(
    text: str, table_header: Sequence[str], cells: Sequence[DetectCell]
) -> bool:
    return bool(cells) and all(
        text[cell.start : cell.end].strip().casefold() == header.strip().casefold()
        for header, cell in zip(table_header, cells)
    )


def table_column_findings(
    segment_id: str,
    text: str,
    table_header: Sequence[str],
    cells: Sequence[DetectCell],
) -> list[dict[str, Any]]:
    """Return one finding per non-blank cell of an identifier column.

    Args:
        segment_id: The segment the row belongs to.
        text: The segment text the cell offsets index.
        table_header: Column headers; ``cells[i]`` belongs to
            ``table_header[i]``.
        cells: Validated cell offsets into ``text``.

    Returns:
        Finding mappings with ``source`` set to ``"table-column"``, a score of
        1.0, the column's canonical label and policy class, and subtype
        ``AU_UR`` for record-number columns only. A cell is trimmed to its
        non-whitespace extent. The header row itself gives no findings.
    """
    if _is_header_row(text, table_header, cells):
        return []
    decisions = classify_columns(
        table_header, (), header_heuristics=AU_TABLE_HEADER_LABELS
    )
    findings: list[dict[str, Any]] = []
    for decision, cell in zip(decisions, cells):
        label = decision.canonical_label
        if decision.assigned_class not in _IDENTIFIER_CLASSES or label is None:
            continue
        raw = text[cell.start : cell.end]
        value = raw.strip()
        if not value:
            continue
        start = cell.start + len(raw) - len(raw.lstrip())
        finding: dict[str, Any] = {
            "segment_id": segment_id,
            "start": start,
            "end": start + len(value),
            "label": label,
            "score": 1.0,
            "policy_class": str(LABEL_METADATA[label]["policy_label"]),
            "source": TABLE_COLUMN_SOURCE,
        }
        subtype = AU_TABLE_HEADER_SUBTYPES.get(table_header_key(decision.name))
        if subtype is not None:
            finding["subtype"] = subtype
        findings.append(finding)
    return findings
