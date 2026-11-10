"""Tests for table column classification in ``POST /v1/detect`` (ticket 16).

One segment is one table row: the consumer joins the cells with `` | `` and
sends each cell's offsets alongside the table header. Every value here is
synthetic (fictional names, dates, record numbers and phone numbers). The model
is always stubbed with an empty detector, so "model disabled" is literal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openmed.processing.outputs import PredictionResult
from openmed.service.lean.app import (
    create_app,
    get_model_detector,
    get_provenance_payload,
)
from openmed.service.lean.selftest import SELFTEST_TEXT

TABLE_COLUMN = "table-column"
_FIXED_PROVENANCE = {
    "openmed_version": "0.0.0",
    "fork_tag": "test",
    "models": ["stub"],
    "policies": {"ccia_l3": "f"},
}

# Recorded at HEAD (before table columns existed) for the selftest text, the
# empty detector, ``ccia_l3`` and the fixed provenance above.
_HEAD_SELFTEST_BYTES = (
    b'{"findings":[{"segment_id":"probe","start":31,"end":43,'
    b'"label":"MEDICARE_NUMBER","subtype":"AU_MEDICARE","score":0.9,'
    b'"policy_class":"DIRECT_IDENTIFIER"},{"segment_id":"probe","start":49,'
    b'"end":63,"label":"ABN","subtype":"AU_ABN","score":0.95,'
    b'"policy_class":"DIRECT_IDENTIFIER"},{"segment_id":"probe","start":68,'
    b'"end":80,"label":"PHONE","subtype":"AU_PHONE","score":0.6,'
    b'"policy_class":"DIRECT_IDENTIFIER"},{"segment_id":"probe","start":87,'
    b'"end":97,"label":"DATE","score":0.85,"policy_class":"QUASI_IDENTIFIER"}],'
    b'"provenance":{"openmed_version":"0.0.0","fork_tag":"test",'
    b'"models":["stub"],"policies":{"ccia_l3":"f"}}}'
)


def _empty_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: _empty_detector
    app.dependency_overrides[get_provenance_payload] = lambda: _FIXED_PROVENANCE
    return TestClient(app, raise_server_exceptions=False)


def _row(segment_id: str, values, header) -> dict:
    """Join ``values`` with `` | `` and return a table-row segment."""
    parts: list[str] = []
    cells = []
    cursor = 0
    for index, value in enumerate(values):
        if index:
            parts.append(" | ")
            cursor += 3
        cells.append({"start": cursor, "end": cursor + len(value)})
        parts.append(value)
        cursor += len(value)
    return {
        "segment_id": segment_id,
        "text": "".join(parts),
        "table_header": list(header),
        "cells": cells,
    }


def _detect(client: TestClient, segments, policy: str = "ccia_l3"):
    return client.post("/v1/detect", json={"segments": segments, "policy": policy})


def _table(findings):
    return [f for f in findings if f.get("source") == TABLE_COLUMN]


def test_name_dob_ur_exam_table_with_model_disabled(client):
    header = ["Name", "D.O.B", "UR", "Exam"]
    rows = [
        ["Jane CITIZEN", "04/07/1961", "0012345", "CT chest"],
        ["John SAMPLE", "12/11/1975", "0098765", "MRI brain"],
    ]
    segments = [_row(f"row-{i}", values, header) for i, values in enumerate(rows)]
    response = _detect(client, segments)
    assert response.status_code == 200, response.content
    findings = response.json()["findings"]

    for segment, values in zip(segments, rows):
        mine = _table([f for f in findings if f["segment_id"] == segment["segment_id"]])
        assert [f["label"] for f in mine] == ["PERSON", "DATE_OF_BIRTH", "ID_NUM"]
        for finding, cell in zip(mine, segment["cells"][:3]):
            assert (finding["start"], finding["end"]) == (cell["start"], cell["end"])
            assert finding["score"] == 1.0
            assert finding["policy_class"] == "DIRECT_IDENTIFIER"
        assert [f.get("subtype") for f in mine] == [None, None, "AU_UR"]
        exam = segment["cells"][3]
        assert all(f["end"] <= exam["start"] or f["start"] >= exam["end"] for f in mine)


def test_unknown_headers_give_no_table_findings_but_detection_runs(client):
    segment = _row("row-0", ["Review", "ph 0412 345 678"], ["Exam", "Result"])
    response = _detect(client, [segment])
    assert response.status_code == 200, response.content
    findings = response.json()["findings"]
    assert _table(findings) == []
    assert [f for f in findings if f.get("subtype") == "AU_PHONE"]


def test_no_table_segments_byte_identical(client):
    response = _detect(client, [{"segment_id": "probe", "text": SELFTEST_TEXT}])
    assert response.status_code == 200
    assert response.content == _HEAD_SELFTEST_BYTES
    assert all("source" not in f for f in response.json()["findings"])


def test_dob_column_uses_cell_values_not_samples(client):
    header = ["Exam"]
    segments = [
        _row(f"row-{i}", [value], header)
        for i, value in enumerate(["04/07/1961", "12/11/1975", "01/02/1980"])
    ]
    response = _detect(client, segments)
    assert response.status_code == 200, response.content
    assert _table(response.json()["findings"]) == []


def test_header_row_segment_is_never_a_finding(client):
    header = ["Name", "D.O.B", "UR"]
    segment = _row("header", ["Name", "D.O.B", "UR"], header)
    response = _detect(client, [segment])
    assert response.status_code == 200, response.content
    assert _table(response.json()["findings"]) == []


def test_blank_and_padded_cells(client):
    header = ["Name", "Name"]
    segment = _row("row-0", ["  ", " Jane CITIZEN "], header)
    response = _detect(client, [segment])
    assert response.status_code == 200, response.content
    mine = _table(response.json()["findings"])
    assert len(mine) == 1
    start = segment["text"].index("Jane CITIZEN")
    assert (mine[0]["start"], mine[0]["end"]) == (start, start + len("Jane CITIZEN"))


def test_table_column_findings_added_alongside_pattern_findings(client):
    segment = _row("row-0", ["0412 345 678"], ["Mobile"])
    response = _detect(client, [segment])
    assert response.status_code == 200, response.content
    findings = response.json()["findings"]
    cell = segment["cells"][0]
    span = (cell["start"], cell["end"])
    pattern = [f for f in findings if f.get("subtype") == "AU_PHONE"]
    table = _table(findings)
    assert [(f["start"], f["end"]) for f in pattern] == [span]
    assert [(f["start"], f["end"], f["label"]) for f in table] == [(*span, "PHONE")]


def _bad_segments():
    text = "Jane CITIZEN | 0012345"
    base = {"segment_id": "row-0", "text": text}
    return {
        "header_without_cells": {**base, "table_header": ["Name", "UR"]},
        "cells_without_header": {**base, "cells": [{"start": 0, "end": 12}]},
        "length_mismatch": {
            **base,
            "table_header": ["Name", "UR"],
            "cells": [{"start": 0, "end": 12}],
        },
        "start_after_end": {
            **base,
            "table_header": ["Name"],
            "cells": [{"start": 12, "end": 0}],
        },
        "end_past_text": {
            **base,
            "table_header": ["Name"],
            "cells": [{"start": 0, "end": len(text) + 1}],
        },
        "negative_start": {
            **base,
            "table_header": ["Name"],
            "cells": [{"start": -1, "end": 3}],
        },
        "extra_cell_field": {
            **base,
            "table_header": ["Name"],
            "cells": [{"start": 0, "end": 12, "label": "PERSON"}],
        },
    }


@pytest.mark.parametrize("case", sorted(_bad_segments()))
def test_invalid_table_shapes_are_invalid_request(client, case):
    response = _detect(client, [_bad_segments()[case]])
    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_REQUEST"}


def test_detect_schema_publishes_table_fields(client):
    request = client.get("/v1/schemas/detect_request").json()
    segment = request["$defs"]["DetectSegment"]["properties"]
    assert {"table_header", "cells"} <= set(segment)
    response = client.get("/v1/schemas/detect_response").json()
    assert "source" in response["$defs"]["DetectFinding"]["properties"]
