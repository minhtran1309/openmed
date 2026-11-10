"""Tests for ``POST /v1/verify/pdf`` on the lean service (ticket 15)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("pdfplumber")

from openmed.multimodal import extract_pdf, project_text_spans  # noqa: E402
from openmed.multimodal.verify_pdf import verify_redacted_pdf  # noqa: E402
from openmed.service.lean import verify as lean_verify  # noqa: E402
from openmed.service.lean.app import create_app  # noqa: E402

_FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "multimodal"
    / "fixtures"
    / "redaction_pdfs.py"
)
# Words from the synthetic fixtures that must never leave the service.
_SENTINELS = ("John", "Doe", "MRN", "12345")
_ROUTE = "/v1/verify/pdf"
# Inside the filled block that stands in for a scanned image on page 2.
_SCANNED_REGION = {"page": 2, "bbox": [100.0, 100.0, 200.0, 150.0]}


def _load_fixture_builder() -> Any:
    spec = importlib.util.spec_from_file_location("redaction_pdfs", _FIXTURES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = _load_fixture_builder()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _name_region(original: bytes) -> dict[str, Any]:
    document = extract_pdf(BytesIO(original))
    start = document.text.index("John")
    end = document.text.index("Doe") + len("Doe")
    (rect,) = project_text_spans(document, [(start, end)])
    return {"page": rect.page + 1, "bbox": [float(v) for v in rect.bbox]}


def _post(
    client: TestClient,
    original: bytes | None,
    redacted: bytes | None,
    regions: Any,
) -> httpx.Response:
    files: dict[str, Any] = {}
    if original is not None:
        files["original"] = ("original.pdf", original, "application/pdf")
    if redacted is not None:
        files["redacted"] = ("redacted.pdf", redacted, "application/pdf")
    if regions is not None:
        payload = regions if isinstance(regions, str) else json.dumps(regions)
        files["regions"] = (None, payload, "application/json")
    return client.post(_ROUTE, files=files)


def _verify(client: TestClient, redacted: bytes, regions=None) -> httpx.Response:
    original = fx.original_pdf_bytes()
    regions = regions if regions is not None else [_name_region(original)]
    return _post(client, original, redacted, regions)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def test_blackbox_redaction_passes(client: TestClient) -> None:
    response = _verify(client, fx.clean_redaction_pdf_bytes())
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    (region,) = body["regions"]
    assert region["status"] == "passed"
    assert region["residual_word_count"] == 0
    assert region["page"] == 1
    assert len(region["bbox"]) == 4
    assert body["text_removal"] == {"selected": 2, "residual": 0}
    assert set(body) == {"passed", "regions", "text_removal", "report_sha256"}
    assert set(region) == {"page", "bbox", "status", "residual_word_count"}


def test_drawn_rectangle_only_is_text_leak(client: TestClient) -> None:
    body = _verify(client, fx.leaky_redaction_pdf_bytes()).json()
    (region,) = body["regions"]
    assert region["status"] == "text_leak"
    assert region["residual_word_count"] >= 1
    assert body["passed"] is False


def test_moved_text_is_text_leak(client: TestClient) -> None:
    body = _verify(client, fx.moved_leak_pdf_bytes()).json()
    (region,) = body["regions"]
    assert region["status"] == "text_leak"
    assert region["residual_word_count"] >= 1
    assert body["text_removal"]["residual"] >= 1
    assert body["passed"] is False


def test_region_without_box_is_no_box(client: TestClient) -> None:
    body = _verify(client, fx.no_box_redaction_pdf_bytes()).json()
    (region,) = body["regions"]
    assert region["status"] == "no_box"
    assert region["residual_word_count"] == 0
    assert body["passed"] is False


def test_scanned_page_region_is_unverifiable(client: TestClient) -> None:
    original = fx.scanned_page_pdf_bytes()
    redacted = fx.scanned_page_pdf_bytes(redacted=True)
    regions = [_name_region(original), _SCANNED_REGION]
    response = _post(client, original, redacted, regions)
    assert response.status_code == 200
    body = response.json()
    first, second = body["regions"]
    assert first["status"] == "passed"
    assert second["page"] == 2
    assert second["status"] == "unverifiable"
    assert body["passed"] is False
    assert body["text_removal"] == {"selected": 2, "residual": 0}


def test_no_source_text_in_response_or_logs(client: TestClient, caplog) -> None:
    original = fx.original_pdf_bytes()
    region = _name_region(original)
    with caplog.at_level(logging.DEBUG):
        responses = [
            _post(client, original, fx.leaky_redaction_pdf_bytes(), [region]),
            _post(client, original, fx.clean_redaction_pdf_bytes(), [region]),
            _post(client, original, fx.moved_leak_pdf_bytes(), [region]),
            _post(client, original, b"not a pdf", [region]),
            _post(client, original, fx.leaky_redaction_pdf_bytes(), "John Doe"),
            _post(
                client,
                original,
                fx.leaky_redaction_pdf_bytes(),
                [{"page": 1, "bbox": [0, 0, 9000, 10]}],
            ),
        ]
    assert [r.status_code for r in responses] == [200, 200, 200, 400, 400, 400]
    for sentinel in _SENTINELS:
        for response in responses:
            assert sentinel not in response.text
        assert sentinel not in caplog.text


def test_report_sha256_matches_body(client: TestClient) -> None:
    for redacted in (fx.clean_redaction_pdf_bytes(), fx.leaky_redaction_pdf_bytes()):
        body = _verify(client, redacted).json()
        digest = body.pop("report_sha256")
        assert digest == hashlib.sha256(_canonical(body)).hexdigest()


_ONE_PAGE_REGION = [{"page": 1, "bbox": [72.0, 60.0, 200.0, 80.0]}]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("garbage", {"error_code": "PDF_UNREADABLE"}),
        ("garbage_original", {"error_code": "PDF_UNREADABLE"}),
        ("page_count", {"error_code": "PAGE_COUNT_MISMATCH"}),
        ("page_beyond", {"error_code": "REGION_OUT_OF_BOUNDS", "page": 3}),
        ("x1_beyond", {"error_code": "REGION_OUT_OF_BOUNDS", "page": 1}),
        ("negative_top", {"error_code": "REGION_OUT_OF_BOUNDS", "page": 1}),
        ("not_json", {"error_code": "INVALID_REQUEST"}),
        ("empty", {"error_code": "INVALID_REQUEST"}),
        ("not_list", {"error_code": "INVALID_REQUEST"}),
        ("page_zero", {"error_code": "INVALID_REQUEST"}),
        ("inverted_x", {"error_code": "INVALID_REQUEST"}),
        ("inverted_y", {"error_code": "INVALID_REQUEST"}),
        ("short_bbox", {"error_code": "INVALID_REQUEST"}),
        ("extra_key", {"error_code": "INVALID_REQUEST"}),
        ("bool_page", {"error_code": "INVALID_REQUEST"}),
        ("nan", {"error_code": "INVALID_REQUEST"}),
        ("missing_redacted", {"error_code": "INVALID_REQUEST"}),
        ("missing_regions", {"error_code": "INVALID_REQUEST"}),
    ],
)
def test_error_codes(client: TestClient, case: str, expected: dict) -> None:
    original = fx.original_pdf_bytes()
    redacted = fx.clean_redaction_pdf_bytes()
    regions: Any = _ONE_PAGE_REGION
    if case == "garbage":
        redacted = b"%PDF-garbage\x00\x01 not really a pdf"
    elif case == "garbage_original":
        original = b"garbage"
    elif case == "page_count":
        redacted = fx.multipage_pdf_bytes()
    elif case == "page_beyond":
        regions = [{"page": 3, "bbox": [72.0, 60.0, 200.0, 80.0]}]
    elif case == "x1_beyond":
        regions = [{"page": 1, "bbox": [72.0, 60.0, 613.0, 80.0]}]
    elif case == "negative_top":
        regions = [{"page": 1, "bbox": [72.0, -1.0, 200.0, 80.0]}]
    elif case == "not_json":
        regions = "{not json"
    elif case == "empty":
        regions = []
    elif case == "not_list":
        regions = {"page": 1, "bbox": [72.0, 60.0, 200.0, 80.0]}
    elif case == "page_zero":
        regions = [{"page": 0, "bbox": [72.0, 60.0, 200.0, 80.0]}]
    elif case == "inverted_x":
        regions = [{"page": 1, "bbox": [200.0, 60.0, 72.0, 80.0]}]
    elif case == "inverted_y":
        regions = [{"page": 1, "bbox": [72.0, 80.0, 200.0, 80.0]}]
    elif case == "short_bbox":
        regions = [{"page": 1, "bbox": [72.0, 60.0, 200.0]}]
    elif case == "extra_key":
        regions = [{"page": 1, "bbox": [72.0, 60.0, 200.0, 80.0], "text": "x"}]
    elif case == "bool_page":
        regions = [{"page": True, "bbox": [72.0, 60.0, 200.0, 80.0]}]
    elif case == "nan":
        regions = '[{"page": 1, "bbox": [NaN, 60.0, 200.0, 80.0]}]'
    elif case == "missing_redacted":
        redacted = None
    elif case == "missing_regions":
        regions = None
    response = _post(client, original, redacted, regions)
    assert response.status_code == 400
    assert response.json() == expected


def test_region_within_half_point_tolerance_is_accepted(client: TestClient) -> None:
    regions = [{"page": 1, "bbox": [-0.4, 0.0, 612.4, 792.4]}]
    response = _verify(client, fx.clean_redaction_pdf_bytes(), regions)
    assert response.status_code == 200


def test_non_multipart_body_is_invalid_request(client: TestClient) -> None:
    response = client.post(_ROUTE, json={"original": "x"})
    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_REQUEST"}


def test_duplicate_part_is_invalid_request(client: TestClient) -> None:
    original = fx.original_pdf_bytes()
    files = [
        ("original", ("a.pdf", original, "application/pdf")),
        ("original", ("b.pdf", original, "application/pdf")),
        ("redacted", ("c.pdf", original, "application/pdf")),
        ("regions", (None, json.dumps(_ONE_PAGE_REGION), "application/json")),
    ]
    response = client.post(_ROUTE, files=files)
    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_REQUEST"}


def test_unexpected_failure_is_verify_failed(
    client: TestClient, monkeypatch, caplog
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Zelphira")

    monkeypatch.setattr(lean_verify, "verify_redacted_pdf", _boom)
    with caplog.at_level(logging.DEBUG):
        response = _verify(client, fx.clean_redaction_pdf_bytes())
    assert response.status_code == 500
    assert response.json() == {"error_code": "VERIFY_FAILED"}
    assert "Zelphira" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_missing_pdfplumber_is_verify_failed(client: TestClient, monkeypatch) -> None:
    from openmed.multimodal.exceptions import MissingDependencyError

    def _missing() -> Any:
        raise MissingDependencyError(dependency="pdfplumber", instruction="x")

    monkeypatch.setattr(lean_verify, "_import_pdfplumber", _missing)
    response = _verify(client, fx.clean_redaction_pdf_bytes())
    assert response.status_code == 500
    assert response.json() == {"error_code": "VERIFY_FAILED"}


def _forbid_temp_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("upload bytes must not be written to disk")

    for name in (
        "SpooledTemporaryFile",
        "TemporaryFile",
        "NamedTemporaryFile",
        "mkstemp",
    ):
        monkeypatch.setattr(tempfile, name, _raise)
    # Starlette binds its spool class at import time; cover that name too.
    import starlette.formparsers

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", _raise)


def test_uploads_are_never_spooled_to_disk(client: TestClient, monkeypatch) -> None:
    large = fx.large_padded_pdf_bytes(2 * 1024 * 1024)
    assert len(large) > 2 * 1024 * 1024
    region = _name_region(fx.original_pdf_bytes())
    _forbid_temp_files(monkeypatch)
    response = _post(client, large, large, [region])
    assert response.status_code == 200
    assert response.json()["regions"][0]["status"] == "text_leak"


def test_binary_pdf_bytes_survive_multipart_parsing() -> None:
    payload = (
        bytes(range(256))
        + b"\r\n--not-the-boundary\r\n\r\r\n\n\r"
        + bytes(range(255, -1, -1))
        + b"\r\n\r\n\r"
    )
    second = b"\n" + payload[::-1] + b"\r"
    request = httpx.Request(
        "POST",
        "http://testserver/v1/verify/pdf",
        files={
            "original": ("o.pdf", payload, "application/pdf"),
            "redacted": ("r.pdf", second, "application/pdf"),
            "regions": (None, b"[]", "application/json"),
        },
    )
    body = request.read()
    parts = lean_verify.parse_multipart_form(body, request.headers["content-type"])
    assert parts["original"] == payload
    assert parts["redacted"] == second
    assert parts["regions"] == b"[]"


def test_body_over_cap_is_invalid_request(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(lean_verify, "MAX_VERIFY_BODY_BYTES", 1024)
    response = _verify(client, fx.clean_redaction_pdf_bytes())
    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_REQUEST"}


def test_raster_backend_available() -> None:
    assert importlib.util.find_spec("pypdfium2") is not None


def test_pixel_check_runs_from_memory() -> None:
    original = fx.original_pdf_bytes()
    region = _name_region(original)
    report = verify_redacted_pdf(
        BytesIO(original),
        BytesIO(fx.clean_redaction_pdf_bytes()),
        [{"page": region["page"] - 1, "bbox": region["bbox"]}],
    )
    (result,) = report.regions
    assert result.visual_method == "pixel"
    assert result.pixels_changed is True


def test_verify_pdf_schemas_published(client: TestClient) -> None:
    regions = client.get("/v1/schemas/verify_pdf_regions")
    assert regions.status_code == 200
    assert regions.json()["type"] == "array"
    response = client.get("/v1/schemas/verify_pdf_response")
    assert response.status_code == 200
    schema = response.json()
    assert set(schema["properties"]) == {
        "passed",
        "regions",
        "text_removal",
        "report_sha256",
    }
    error = client.get("/v1/schemas/error_response").json()
    assert "page" in error["properties"]


@pytest.mark.integration
def test_pymupdf_apply_redactions_passes(client: TestClient) -> None:
    # Opt-in only: PyMuPDF is AGPL and is never an openmed dependency.
    fitz = pytest.importorskip("fitz")
    original = fx.original_pdf_bytes()
    region = _name_region(original)
    document = fitz.open(stream=original, filetype="pdf")
    page = document[0]
    for rect in page.search_for("John Doe"):
        page.add_redact_annot(rect, fill=(0, 0, 0))
    page.apply_redactions()
    redacted = document.tobytes()
    body = _post(client, original, redacted, [region]).json()
    assert body["passed"] is True, body


# --- Fix round 1: resource bounds, threadpool parse, log hardening ---------


def _region_list(count: int) -> list[dict[str, Any]]:
    return [{"page": 1, "bbox": [72.0, 60.0, 200.0, 80.0]}] * count


def test_region_cap_rejects_257_regions(client: TestClient) -> None:
    original = fx.original_pdf_bytes()
    over = _post(client, original, original, _region_list(257))
    assert over.status_code == 400
    assert over.json() == {"error_code": "INVALID_REQUEST"}
    at_cap = _post(client, original, original, _region_list(256))
    assert at_cap.status_code == 200
    assert lean_verify.MAX_VERIFY_REGIONS == 256


def test_render_dpi_respects_pixel_budget() -> None:
    letter = lean_verify.render_size(612.0, 792.0)
    assert letter == (1275, 1650)  # 150 dpi: under the budget
    a0 = lean_verify.render_size(2384.0, 3370.0)
    assert a0 is not None
    assert a0[0] * a0[1] <= lean_verify.PIXEL_BUDGET
    assert lean_verify.render_dpi(2384.0, 3370.0) < 150.0
    # The PDF maximum page needs < 36 dpi to fit: no render at all.
    assert lean_verify.render_dpi(14400.0, 14400.0) is None
    assert lean_verify.render_size(14400.0, 14400.0) is None


def _huge_page_pdf(redacted: bool) -> bytes:
    lines = [(72.0, 14300.0, "Patient" if redacted else "Patient John Doe")]
    stream = fx._text_block(lines)
    if redacted:
        stream = fx._rect_fill((110.0, 14288.0, 95.0, 22.0)) + stream
    return fx.build_pdf_pages([stream], page_sizes=[(14400.0, 14400.0)])


def test_huge_page_is_unverifiable_without_rendering(
    client: TestClient, monkeypatch
) -> None:
    calls: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("over-budget page must not be rendered")

    monkeypatch.setattr(lean_verify, "_render_page", _spy)
    original = _huge_page_pdf(redacted=False)
    region = _name_region(original)
    response = _post(client, original, _huge_page_pdf(redacted=True), [region])
    assert response.status_code == 200
    body = response.json()
    assert body["regions"][0]["status"] == "unverifiable"
    assert body["passed"] is False
    assert calls == []


def test_leak_on_huge_page_is_still_text_leak(client: TestClient) -> None:
    original = _huge_page_pdf(redacted=False)
    region = _name_region(original)
    body = _post(client, original, original, [region]).json()
    assert body["regions"][0]["status"] == "text_leak"


def test_each_page_rendered_once_per_document(client: TestClient, monkeypatch) -> None:
    calls: list[tuple[int, int]] = []
    real = lean_verify._render_page

    def _spy(source: Any, page_index: int, dpi: float) -> Any:
        calls.append((id(source), page_index))
        return real(source, page_index, dpi)

    monkeypatch.setattr(lean_verify, "_render_page", _spy)
    original = fx.original_pdf_bytes()
    name = _name_region(original)
    regions = [name] + [
        {"page": 1, "bbox": [72.0 + i, 300.0, 300.0 + i, 320.0]} for i in range(6)
    ]
    response = _post(client, original, fx.clean_redaction_pdf_bytes(), regions)
    assert response.status_code == 200
    assert response.json()["regions"][0]["status"] == "passed"
    assert len(calls) == 2
    assert len(set(calls)) == 2


def test_duplicate_regions_are_counted_once(client: TestClient) -> None:
    original = fx.original_pdf_bytes()
    region = _name_region(original)
    body = _post(client, original, fx.leaky_redaction_pdf_bytes(), [region] * 3).json()
    assert len(body["regions"]) == 3
    assert {r["status"] for r in body["regions"]} == {"text_leak"}
    assert body["text_removal"] == {"selected": 2, "residual": 2}


def test_multipart_parse_runs_off_the_event_loop(
    client: TestClient, monkeypatch
) -> None:
    import asyncio

    seen: list[bool] = []
    real = lean_verify.parse_multipart_form

    def _spy(body: bytes, content_type: str) -> dict[str, bytes]:
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return real(body, content_type)

    monkeypatch.setattr(lean_verify, "parse_multipart_form", _spy)
    response = _verify(client, fx.clean_redaction_pdf_bytes())
    assert response.status_code == 200
    assert seen == [False]


def test_child_logger_at_debug_is_silenced_at_app_creation(caplog) -> None:
    child = logging.getLogger("pdfminer.pdfinterp")
    previous = child.level
    child.setLevel(logging.DEBUG)
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        original = fx.original_pdf_bytes()
        with caplog.at_level(logging.DEBUG):
            response = _post(
                client,
                original,
                fx.leaky_redaction_pdf_bytes(),
                [_name_region(original)],
            )
        assert response.status_code == 200
        for sentinel in _SENTINELS:
            assert sentinel not in caplog.text
    finally:
        child.setLevel(previous)
