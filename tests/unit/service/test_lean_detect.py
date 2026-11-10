"""Tests for ``POST /v1/detect`` on the lean service.

Every identifier here is synthetic. Checksum-valid values are the published
self-test values or are computed in ``tests/unit/core/test_au_identifiers.py``
(``2123 45670 1`` Medicare, ``11 345 678 919`` ABN, ``8003601234567894`` IHI,
``123 456 782`` TFN, ``123 456 780`` ACN); phone, street and suburb values are
fictional. The model is always stubbed: no network and no model download.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from openmed.core.provenance import provenance
from openmed.processing.outputs import EntityPrediction, PredictionResult
from openmed.service.lean import detect as detect_module
from openmed.service.lean.app import create_app, get_model_detector
from openmed.service.lean.schemas import schema_names

MEDICARE = "2123 45670 1"
ABN = "11 345 678 919"
IHI = "8003601234567894"
TFN = "123 456 782"
ACN = "123 456 780"
PHONE = "0412 345 678"
OCR_PHONE = "0412.345.678"
ADDRESS = "Unit 3, 12 Smith Street, Randwick"
SYNTHETIC_NAME = "Zelphira Quandt"


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


def _name_detector(text, **kwargs):
    entities = []
    start = text.find(SYNTHETIC_NAME)
    if start >= 0:
        entities.append(
            EntityPrediction(
                text=SYNTHETIC_NAME,
                label="NAME",
                confidence=0.97,
                start=start,
                end=start + len(SYNTHETIC_NAME),
            )
        )
    return PredictionResult(
        text=text,
        entities=entities,
        model_name="stub",
        timestamp="2026-01-01T00:00:00",
    )


def _client(detector=_stub_detector) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_model_detector] = lambda: detector
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client() -> TestClient:
    return _client()


def _detect(client: TestClient, segments, policy: str = "ccia_l3", **extra):
    payload = {"segments": segments, "policy": policy, **extra}
    return client.post("/v1/detect", json=payload)


def _seg(segment_id: str, text: str) -> dict[str, str]:
    return {"segment_id": segment_id, "text": text}


def _by_subtype(findings, subtype):
    return [f for f in findings if f.get("subtype") == subtype]


def _single(findings, subtype):
    matches = _by_subtype(findings, subtype)
    assert len(matches) == 1, findings
    return matches[0]


# ---------------------------------------------------------------------------
# Response shape and AU values end-to-end
# ---------------------------------------------------------------------------


def test_medicare_returns_medicare_number_au_medicare_direct_identifier(
    client,
) -> None:
    text = f"Medicare number {MEDICARE} on card."
    response = _detect(client, [_seg("s1", text)])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"findings", "provenance"}
    finding = _single(body["findings"], "AU_MEDICARE")
    assert finding["segment_id"] == "s1"
    assert finding["label"] == "MEDICARE_NUMBER"
    assert finding["policy_class"] == "DIRECT_IDENTIFIER"
    assert text[finding["start"] : finding["end"]] == MEDICARE
    assert 0.0 < finding["score"] <= 1.0
    assert set(finding) == {
        "segment_id",
        "start",
        "end",
        "label",
        "subtype",
        "score",
        "policy_class",
    }


@pytest.mark.parametrize(
    ("template", "value", "subtype", "label", "policy_class"),
    [
        ("Practice ABN: {v} on invoice.", ABN, "AU_ABN", "ABN", "DIRECT_IDENTIFIER"),
        ("Patient IHI {v} confirmed.", IHI, "AU_IHI", "IHI", "DIRECT_IDENTIFIER"),
        ("Tax file number {v} lodged.", TFN, "AU_TFN", "TFN", "DIRECT_IDENTIFIER"),
        ("Company ACN {v} registered.", ACN, "AU_ACN", "ACN", "DIRECT_IDENTIFIER"),
        ("Mobile: {v} (self-test).", PHONE, "AU_PHONE", "PHONE", "DIRECT_IDENTIFIER"),
        (
            "Home: {v} NSW 2031.",
            ADDRESS,
            "AU_ADDRESS",
            "STREET_ADDRESS",
            "DIRECT_IDENTIFIER",
        ),
        (
            "Seen in Randwick {v} 2031.",
            "NSW",
            "AU_STATE",
            "LOCATION",
            "QUASI_IDENTIFIER",
        ),
        (
            "Seen in Randwick NSW {v}.",
            "2031",
            "AU_POSTCODE",
            "ZIPCODE",
            "QUASI_IDENTIFIER",
        ),
    ],
)
def test_au_values_end_to_end(
    client, template, value, subtype, label, policy_class
) -> None:
    text = template.format(v=value)
    body = _detect(client, [_seg("seg-a", text)]).json()

    finding = _single(body["findings"], subtype)
    start = text.index(value)
    assert (finding["start"], finding["end"]) == (start, start + len(value))
    assert finding["label"] == label
    assert finding["policy_class"] == policy_class


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("URN: 0012345 admitted.", "0012345"),
        (f"Accession {TFN} received.", TFN),
    ],
)
def test_detect_ur_number_returns_au_ur(client, text, value) -> None:
    findings = _detect(client, [_seg("s", text)]).json()["findings"]

    finding = _single(findings, "AU_UR")
    start = text.index(value)
    assert (finding["start"], finding["end"]) == (start, start + len(value))
    assert finding["label"] == "ID_NUM"
    assert not _by_subtype(findings, "AU_TFN")


def test_policy_class_comes_from_label_metadata(client) -> None:
    from openmed.core.labels import LABEL_METADATA

    text = f"Home: {ADDRESS} NSW 2031. Ph {PHONE}. Medicare {MEDICARE}."
    findings = _detect(client, [_seg("s", text)]).json()["findings"]
    assert findings
    for finding in findings:
        expected = LABEL_METADATA[finding["label"]]["policy_label"]
        assert finding["policy_class"] == expected


def test_model_finding_without_subtype_omits_it() -> None:
    client = _client(_name_detector)
    text = f"Patient {SYNTHETIC_NAME} reviewed."
    findings = _detect(client, [_seg("s", text)]).json()["findings"]

    names = [f for f in findings if f["label"] in {"NAME", "PERSON"}]
    assert len(names) == 1, findings
    name = names[0]
    assert "subtype" not in name or name["subtype"] is None
    assert text[name["start"] : name["end"]] == SYNTHETIC_NAME
    assert name["score"] == pytest.approx(0.97)
    assert name["policy_class"] == "DIRECT_IDENTIFIER"


def test_findings_are_tagged_with_their_segment(client) -> None:
    segments = [
        _seg("first", f"Medicare {MEDICARE} on card."),
        _seg("second", "No identifiers in this line."),
        _seg("third", f"Mobile: {PHONE}"),
    ]
    findings = _detect(client, segments).json()["findings"]

    by_segment = {f["segment_id"] for f in findings}
    assert by_segment == {"first", "third"}
    assert _single(findings, "AU_MEDICARE")["segment_id"] == "first"
    assert _single(findings, "AU_PHONE")["segment_id"] == "third"


def test_provenance_is_included(client) -> None:
    body = _detect(client, [_seg("s", "Nothing here.")]).json()
    assert body["findings"] == []
    expected = provenance()
    assert body["provenance"]["policies"] == expected["policies"]
    assert set(body["provenance"]) == {
        "openmed_version",
        "fork_tag",
        "models",
        "policies",
    }


# ---------------------------------------------------------------------------
# Safety sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "subtype"),
    [
        (f"Mobile {OCR_PHONE} after 5pm.", OCR_PHONE, "AU_PHONE"),
        ("Medicare number 2123.45670.1 on card.", "2123.45670.1", "AU_MEDICARE"),
    ],
)
def test_ocr_split_safety_sweep_findings_appear(client, text, value, subtype) -> None:
    findings = _detect(client, [_seg("ocr", text)]).json()["findings"]

    finding = _single(findings, subtype)
    start = text.index(value)
    assert (finding["start"], finding["end"]) == (start, start + len(value))


def test_detect_runs_pipeline_with_sweep_and_preserved_whitespace(
    client, monkeypatch
) -> None:
    seen: list[dict] = []
    real_pipeline = detect_module.Pipeline

    def spy(**kwargs):
        seen.append(kwargs)
        return real_pipeline(**kwargs)

    monkeypatch.setattr(detect_module, "Pipeline", spy)
    _detect(client, [_seg("s", f"Mobile {OCR_PHONE} after 5pm.")])

    assert seen
    assert seen[0]["use_safety_sweep"] is True
    assert seen[0]["preserve_whitespace"] is True
    assert seen[0]["policy"].name == "ccia_l3"


def test_safety_sweep_findings_reach_the_response(client, monkeypatch) -> None:
    """The OCR-split phone comes from the safety sweep and is returned as-is."""
    from openmed.core import safety_sweep as sweep_module

    swept: list[tuple[int, int, str | None]] = []
    real = sweep_module.safety_sweep

    def spy(*args, **kwargs):
        entities = real(*args, **kwargs)
        for entity in entities:
            metadata = entity.metadata or {}
            if metadata.get("source") == "safety_sweep":
                swept.append((entity.start, entity.end, metadata.get("subtype")))
        return entities

    monkeypatch.setattr(sweep_module, "safety_sweep", spy)
    text = f"Mobile {OCR_PHONE} after 5pm."
    findings = _detect(client, [_seg("s", text)]).json()["findings"]

    start = text.index(OCR_PHONE)
    expected = (start, start + len(OCR_PHONE), "AU_PHONE")
    assert expected in swept
    finding = _single(findings, "AU_PHONE")
    assert (finding["start"], finding["end"], finding["subtype"]) == expected


# ---------------------------------------------------------------------------
# Offsets index the exact text sent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"  \n Medicare number {MEDICARE} on card.\n  ",
        f"\tPh {PHONE}\r\n\r\n",
        f"\n\nLine one.\nHome: {ADDRESS} NSW 2031.\n",
        f"   Mobile {OCR_PHONE} after 5pm.   ",
    ],
)
def test_offsets_slice_sent_text_exactly(client, text) -> None:
    findings = _detect(client, [_seg("ws", text)]).json()["findings"]

    assert findings
    sliced = {text[f["start"] : f["end"]] for f in findings}
    for value in (MEDICARE, PHONE, ADDRESS, OCR_PHONE):
        if value in text:
            assert value in sliced, (sliced, findings)
    for finding in findings:
        span = text[finding["start"] : finding["end"]]
        assert span == span.strip()
        assert 0 <= finding["start"] < finding["end"] <= len(text)


def test_preserve_whitespace_false_still_indexes_sent_text(client) -> None:
    text = f"   \n Medicare number {MEDICARE} on card.\n "
    findings = _detect(client, [_seg("ws", text)], preserve_whitespace=False).json()[
        "findings"
    ]
    finding = _single(findings, "AU_MEDICARE")
    assert text[finding["start"] : finding["end"]] == MEDICARE


def test_production_detector_path_preserves_whitespace(monkeypatch) -> None:
    """No injected detector: the default ``extract_pii`` path, model stubbed."""
    import openmed

    def fake_analyze_text(inference_text, **kwargs):
        return PredictionResult(
            text=inference_text,
            entities=[],
            model_name=kwargs["model_name"],
            timestamp="2026-01-01T00:00:00",
        )

    monkeypatch.setattr(openmed, "analyze_text", fake_analyze_text)
    # No transformers needed: the shared loader is a stand-in object.
    monkeypatch.setattr(detect_module, "_LOADER", None)
    monkeypatch.setattr(detect_module, "_make_loader", object)
    client = TestClient(create_app(), raise_server_exceptions=False)
    text = f"\n  Medicare number {MEDICARE} on card.  \n"
    response = _detect(client, [_seg("p", text)])

    assert response.status_code == 200
    finding = _single(response.json()["findings"], "AU_MEDICARE")
    assert text[finding["start"] : finding["end"]] == MEDICARE


def test_missing_span_score_is_reported_as_one(client, monkeypatch) -> None:
    import dataclasses

    real_pipeline = detect_module.Pipeline

    class NoScorePipeline:
        def __init__(self, **kwargs):
            self._inner = real_pipeline(**kwargs)

        def run(self, text, **kwargs):
            result = self._inner.run(text, **kwargs)
            spans = tuple(dataclasses.replace(s, score=None) for s in result.spans)
            return dataclasses.replace(result, spans=spans)

    monkeypatch.setattr(detect_module, "Pipeline", NoScorePipeline)
    response = _detect(client, [_seg("s", f"Medicare number {MEDICARE} on card.")])

    assert response.status_code == 200
    finding = _single(response.json()["findings"], "AU_MEDICARE")
    assert finding["score"] == 1.0


def test_model_loader_is_shared_across_segments_and_requests(monkeypatch) -> None:
    """The default path reuses one process-wide loader (no per-call reload)."""
    import openmed

    created: list[object] = []

    def make_loader():
        loader = object()
        created.append(loader)
        return loader

    seen_loaders: list[object] = []

    def fake_analyze_text(inference_text, **kwargs):
        seen_loaders.append(kwargs.get("loader"))
        return PredictionResult(
            text=inference_text,
            entities=[],
            model_name=kwargs["model_name"],
            timestamp="2026-01-01T00:00:00",
        )

    monkeypatch.setattr(detect_module, "_LOADER", None)
    monkeypatch.setattr(detect_module, "_make_loader", make_loader)
    monkeypatch.setattr(openmed, "analyze_text", fake_analyze_text)
    client = TestClient(create_app(), raise_server_exceptions=False)
    segments = [
        _seg("a", f"Medicare number {MEDICARE} on card."),
        _seg("b", f"Mobile: {PHONE}"),
    ]
    assert _detect(client, segments).status_code == 200
    assert _detect(client, [_seg("c", "Another line.")]).status_code == 200

    assert len(created) == 1
    assert len(seen_loaders) >= 3
    assert all(loader is created[0] for loader in seen_loaders)


def test_labelled_invalid_medicare_is_found_without_subtype(client) -> None:
    """A labelled checksum failure (typo/OCR) is still a finding, untyped."""
    invalid = "2123 45671 1"  # MEDICARE with the last check digit flipped.
    text = f"Medicare number {invalid} on card."
    response = _detect(client, [_seg("s", text)], policy="ccia_l3")

    assert response.status_code == 200
    findings = response.json()["findings"]
    covering = [
        f
        for f in findings
        if f["start"] < text.index(invalid) + len(invalid)
        and f["end"] > text.index(invalid)
    ]
    assert len(covering) == 1, findings
    finding = covering[0]
    assert finding["label"] == "MEDICARE_NUMBER"
    assert "subtype" not in finding
    assert finding["policy_class"] == "DIRECT_IDENTIFIER"
    assert text[finding["start"] : finding["end"]] == invalid


def test_detect_inference_is_serialised_on_the_default_path(monkeypatch) -> None:
    """Concurrent requests never enter the shared model stage at once."""
    import threading

    import openmed

    guard = threading.Lock()
    state = {"active": 0, "max_active": 0, "calls": 0}
    other_entered = threading.Event()

    def fake_analyze_text(inference_text, **kwargs):
        with guard:
            state["calls"] += 1
            first = state["calls"] == 1
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            if not first:
                other_entered.set()
        try:
            if first:
                # Hold the model stage until another call enters it. With the
                # inference lock no other call can, and the wait times out.
                other_entered.wait(timeout=1.0)
        finally:
            with guard:
                state["active"] -= 1
        return PredictionResult(
            text=inference_text,
            entities=[],
            model_name=kwargs["model_name"],
            timestamp="2026-01-01T00:00:00",
        )

    monkeypatch.setattr(openmed, "analyze_text", fake_analyze_text)
    monkeypatch.setattr(detect_module, "_LOADER", None)
    monkeypatch.setattr(detect_module, "_make_loader", object)
    start = threading.Barrier(2)
    statuses: list[int] = []

    def worker(segment_id: str) -> None:
        client = TestClient(create_app(), raise_server_exceptions=False)
        start.wait()
        response = _detect(client, [_seg(segment_id, f"Mobile: {PHONE}")])
        statuses.append(response.status_code)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == [200, 200]
    assert state["calls"] >= 2
    assert state["max_active"] == 1


def test_loader_build_failure_maps_to_detect_failed(monkeypatch) -> None:
    def broken_loader():
        raise RuntimeError(f"cannot load model for {SYNTHETIC_NAME}")

    monkeypatch.setattr(detect_module, "_LOADER", None)
    monkeypatch.setattr(detect_module, "_make_loader", broken_loader)
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = _detect(client, [_seg("s", f"Patient {SYNTHETIC_NAME}")])

    assert response.status_code == 500
    assert response.json() == {"error_code": "DETECT_FAILED"}
    assert SYNTHETIC_NAME not in response.text


def test_injected_detector_does_not_create_a_loader(client, monkeypatch) -> None:
    def fail():
        raise AssertionError("loader must not be created")

    monkeypatch.setattr(detect_module, "_LOADER", None)
    monkeypatch.setattr(detect_module, "_make_loader", fail)
    response = _detect(client, [_seg("s", f"Mobile: {PHONE}")])
    assert response.status_code == 200


def test_empty_text_segment_has_no_findings(client) -> None:
    response = _detect(client, [_seg("blank", ""), _seg("ws", " \n\t ")])
    assert response.status_code == 200
    assert response.json()["findings"] == []


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_unknown_policy(client) -> None:
    response = _detect(client, [_seg("s", "x")], policy="not_a_policy")
    assert response.status_code == 404
    assert response.json() == {"error_code": "UNKNOWN_POLICY"}


def test_empty_segment_list(client) -> None:
    response = _detect(client, [])
    assert response.status_code == 400
    assert response.json() == {"error_code": "EMPTY_DOCUMENT"}


def test_duplicate_segment_id(client) -> None:
    response = _detect(
        client, [_seg("dup", f"Medicare {MEDICARE}"), _seg("dup", "other")]
    )
    assert response.status_code == 400
    assert response.json() == {"error_code": "DUPLICATE_SEGMENT_ID"}
    assert MEDICARE not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"segments": [{"segment_id": "s", "text": "x"}]},  # policy missing
        {"policy": "ccia_l3"},  # segments missing
        {"segments": "nope", "policy": "ccia_l3"},
        {"segments": [{"segment_id": "s"}], "policy": "ccia_l3"},
        {"segments": [{"segment_id": "", "text": "x"}], "policy": "ccia_l3"},
        {"segments": [{"text": "x"}], "policy": "ccia_l3"},
        {"segments": [{"segment_id": "s", "text": 3}], "policy": "ccia_l3"},
        {
            "segments": [{"segment_id": "s", "text": "x"}],
            "policy": "ccia_l3",
            "unexpected": True,
        },
    ],
)
def test_invalid_request(client, payload) -> None:
    response = client.post("/v1/detect", json=payload)
    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_REQUEST"}


def test_invalid_request_does_not_echo_text(client) -> None:
    payload = {
        "segments": [{"segment_id": "s", "text": f"{SYNTHETIC_NAME} {PHONE}"}],
        "policy": "ccia_l3",
        "preserve_whitespace": "not-a-bool",
    }
    response = client.post("/v1/detect", json=payload)
    assert response.json() == {"error_code": "INVALID_REQUEST"}
    assert SYNTHETIC_NAME not in response.text


def test_pipeline_exception_maps_to_detect_failed() -> None:
    def boom(text, **kwargs):
        raise RuntimeError(f"model failure on {text}")

    client = _client(boom)
    response = _detect(client, [_seg("s", f"Patient {SYNTHETIC_NAME}")])
    assert response.status_code == 500
    assert response.json() == {"error_code": "DETECT_FAILED"}
    assert SYNTHETIC_NAME not in response.text


# ---------------------------------------------------------------------------
# No segment text in responses or logs
# ---------------------------------------------------------------------------

_SECRET_VALUES = (MEDICARE, ABN, TFN, PHONE, OCR_PHONE, ADDRESS, SYNTHETIC_NAME)


def _phi_text() -> str:
    return (
        f"Patient {SYNTHETIC_NAME}, Medicare {MEDICARE}, ABN: {ABN}, "
        f"Tax file number {TFN}. Home: {ADDRESS} NSW 2031. "
        f"Mobile {OCR_PHONE}; Ph {PHONE}."
    )


def _log_blob(caplog) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(str(record.args))
        parts.append(record.exc_text or "")
        parts.append(str(record.exc_info))
    return "\n".join(parts)


def test_response_contains_no_span_text() -> None:
    client = _client(_name_detector)
    response = _detect(client, [_seg("s", _phi_text())])

    assert response.status_code == 200
    findings = response.json()["findings"]
    assert len(findings) >= 6
    for value in _SECRET_VALUES:
        assert value not in response.text
    for finding in findings:
        assert "text" not in finding
    serialized = json.dumps(findings)
    assert "Smith" not in serialized
    assert "Zelphira" not in serialized


def test_logs_contain_no_segment_text_success_and_errors(caplog) -> None:
    def boom(text, **kwargs):
        raise RuntimeError(f"model failure on {text}")

    ok = _client(_name_detector)
    failing = _client(boom)
    text = _phi_text()
    with caplog.at_level(logging.DEBUG):
        assert _detect(ok, [_seg("s", text)]).status_code == 200
        assert _detect(failing, [_seg("s", text)]).json() == {
            "error_code": "DETECT_FAILED"
        }
        assert _detect(ok, [_seg("d", text), _seg("d", text)]).json() == {
            "error_code": "DUPLICATE_SEGMENT_ID"
        }
        assert _detect(ok, [_seg("s", text)], policy="nope").json() == {
            "error_code": "UNKNOWN_POLICY"
        }
        bad = {"segments": [{"segment_id": "s", "text": text}], "policy": 7}
        assert ok.post("/v1/detect", json=bad).json() == {
            "error_code": "INVALID_REQUEST"
        }

    blob = _log_blob(caplog)
    for value in _SECRET_VALUES:
        assert value not in blob
    assert "Smith Street" not in blob
    assert "Zelphira" not in blob


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_detect_schemas_published(client) -> None:
    assert {"detect_request", "detect_response"} <= set(schema_names())
    request_schema = client.get("/v1/schemas/detect_request").json()
    assert {"segments", "policy", "preserve_whitespace"} <= set(
        request_schema["properties"]
    )
    response_schema = client.get("/v1/schemas/detect_response").json()
    assert set(response_schema["properties"]) == {"findings", "provenance"}
