"""``POST /v1/verify/pdf``: verify a black-box redacted PDF in memory.

The uploads are PHI, so nothing here touches the disk: the request body is
read under a running cap, parsed in memory with the stdlib ``email`` parser
(Starlette's ``UploadFile`` would spool parts over 1 MiB to a temp file), and
handed to the ``openmed.multimodal.verify_pdf`` checks as ``BytesIO`` objects.

Responses and errors carry counts, boxes and hashes only. Nothing from the
PDFs is logged; on an unexpected failure only the exception type is.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import math
from collections.abc import Sequence
from email import policy as email_policy
from email.message import Message
from email.parser import BytesParser
from io import BytesIO
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request

from openmed.multimodal.verify_pdf import (
    verify_redacted_pdf,
    verify_redacted_text_removed,
)

from .errors import LeanError
from .schemas import MAX_VERIFY_REGIONS, VerifyPdfRegions, VerifyRegion

logger = logging.getLogger("openmed.service.lean")

#: Largest accepted request body (both PDFs plus regions), in bytes.
MAX_VERIFY_BODY_BYTES = 64 * 1024 * 1024

#: A region may overhang the page edge by this much (PDF points).
BOUNDS_TOLERANCE_PT = 0.5

#: Most pixels one page render may have (original and redacted alike).
PIXEL_BUDGET = 25_000_000
#: Render resolution when the page fits the budget (dots per inch).
MAX_RENDER_DPI = 150.0
#: Below this resolution the pixel check is skipped and the page's regions
#: become ``unverifiable`` (fail closed).
MIN_RENDER_DPI = 36.0

_PARTS = ("original", "redacted", "regions")
# pdfminer logs every content-stream operator with its operands at DEBUG,
# which includes the text being shown. The lean service silences these
# libraries (and any child loggers that already exist) when the app is built.
_PDF_LIBRARY_LOGGERS = ("pdfminer", "pdfplumber", "pypdfium2", "PIL")
_SILENT = logging.CRITICAL + 1


def _invalid() -> LeanError:
    return LeanError("INVALID_REQUEST")


async def read_capped_body(request: Request) -> bytes:
    """Read the request body into memory, refusing more than the cap.

    Args:
        request: The incoming request.

    Returns:
        The raw body bytes.

    Raises:
        LeanError: ``INVALID_REQUEST`` when the declared or actual body size
            exceeds ``MAX_VERIFY_BODY_BYTES``.
    """
    cap = MAX_VERIFY_BODY_BYTES
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                raise _invalid()
        except ValueError:
            raise _invalid() from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > cap:
            raise _invalid()
    return bytes(body)


def parse_multipart_form(body: bytes, content_type: str) -> dict[str, bytes]:
    """Parse a ``multipart/form-data`` body in memory.

    Uses ``email.parser.BytesParser`` with the HTTP policy; each part's bytes
    come back unchanged and nothing is spooled to disk.

    Args:
        body: The raw request body.
        content_type: The request ``Content-Type`` header, with its boundary.

    Returns:
        A mapping of form field name to the part's raw bytes.

    Raises:
        LeanError: ``INVALID_REQUEST`` when the body is not well-formed
            multipart form data, a part has no name, or a name repeats.
    """
    if (
        not content_type.lower().startswith("multipart/form-data")
        or "\r" in content_type
        or "\n" in content_type
    ):
        raise _invalid()
    try:
        header = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n"
        message = BytesParser(policy=email_policy.HTTP).parsebytes(header + body)
    except Exception:
        raise _invalid() from None
    if not message.is_multipart() or message.defects:
        raise _invalid()
    parts: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = _part_name(part)
        payload = None if part.is_multipart() else part.get_payload(decode=True)
        if name is None or name in parts or not isinstance(payload, bytes):
            raise _invalid()
        parts[name] = payload
    return parts


def _part_name(part: Message) -> str | None:
    if part.get_content_disposition() != "form-data":
        return None
    name = part.get_param("name", header="content-disposition")
    return name if isinstance(name, str) and name else None


def parse_verify_request(parts: dict[str, bytes]) -> list[VerifyRegion]:
    """Check the form parts and validate ``regions``.

    Args:
        parts: Output of :func:`parse_multipart_form`.

    Returns:
        The validated regions, in request order.

    Raises:
        LeanError: ``INVALID_REQUEST`` when a part is missing or unknown,
            ``regions`` is not a JSON list of ``{page, bbox}`` objects, is
            empty, has a page below 1, or has an inverted or empty bbox.
    """
    if set(parts) != set(_PARTS):
        raise _invalid()
    try:
        raw = json.loads(parts["regions"], parse_constant=_reject_constant)
        return list(VerifyPdfRegions.model_validate(raw).root)
    except (ValueError, ValidationError):
        raise _invalid() from None


def _reject_constant(_name: str) -> Any:
    raise ValueError("NaN and Infinity are not JSON")


def _import_pdfplumber() -> Any:
    return importlib.import_module("pdfplumber")


def silence_pdf_library_logs() -> None:
    """Silence the PDF libraries' loggers, including existing child loggers.

    pdfminer logs page text at DEBUG, so these loggers must never emit in the
    lean service. Child loggers created later inherit the silent level; ones
    that already exist (possibly with their own level) are set explicitly.
    """
    for root in _PDF_LIBRARY_LOGGERS:
        logging.getLogger(root).setLevel(_SILENT)
    for name, candidate in list(logging.Logger.manager.loggerDict.items()):
        if not isinstance(candidate, logging.Logger):
            continue
        if any(name.startswith(root + ".") for root in _PDF_LIBRARY_LOGGERS):
            candidate.setLevel(_SILENT)


def render_dpi(width_pt: float, height_pt: float) -> float | None:
    """Return the render resolution for a page, or ``None`` to skip rendering.

    The resolution is the lower of ``MAX_RENDER_DPI`` and the one that keeps
    the page within ``PIXEL_BUDGET`` pixels.

    Args:
        width_pt: Page width in PDF points.
        height_pt: Page height in PDF points.

    Returns:
        The dpi, or ``None`` when it would fall below ``MIN_RENDER_DPI``.
    """
    area_in2 = max(width_pt, 1.0) * max(height_pt, 1.0) / (72.0 * 72.0)
    dpi = min(MAX_RENDER_DPI, math.sqrt(PIXEL_BUDGET / area_in2))
    return None if dpi < MIN_RENDER_DPI else dpi


def render_size(width_pt: float, height_pt: float) -> tuple[int, int] | None:
    """Return the ``(width, height)`` in pixels a page would render at.

    Args:
        width_pt: Page width in PDF points.
        height_pt: Page height in PDF points.

    Returns:
        The pixel size at :func:`render_dpi`, or ``None`` when not rendered.
    """
    dpi = render_dpi(width_pt, height_pt)
    if dpi is None:
        return None
    scale = dpi / 72.0
    return math.floor(width_pt * scale), math.floor(height_pt * scale)


def _raster_backend_available() -> bool:
    return (
        importlib.util.find_spec("pypdfium2") is not None
        or importlib.util.find_spec("wand") is not None
    )


def _render_page(source: Any, page_index: int, dpi: float) -> Any:
    pdfplumber = _import_pdfplumber()
    with pdfplumber.open(source) as pdf:
        image = pdf.pages[page_index].to_image(resolution=dpi)
        return image.original.convert("L")


class _RenderSkipped(Exception):
    """The page is over the pixel budget; the library falls back to vector."""


class _PageRenders:
    """Per-request page renders: each page of each PDF is rendered once.

    Only the current page's renders are kept; regions are verified grouped by
    page, so a page change releases the previous page's images.
    """

    def __init__(self, dpis: Sequence[float | None]) -> None:
        self._dpis = dpis
        self._page: int | None = None
        self._images: dict[int, Any] = {}

    def rasterize(
        self, source: Any, page: int, region: tuple[float, float, float, float]
    ) -> bytes:
        dpi = self._dpis[page]
        if dpi is None:
            raise _RenderSkipped()
        if page != self._page:
            self.clear()
            self._page = page
        image = self._images.get(id(source))
        if image is None:
            image = _render_page(source, page, dpi)
            self._images[id(source)] = image
        scale = dpi / 72.0
        box = tuple(int(value * scale) for value in region)
        return image.crop(box).tobytes()

    def clear(self) -> None:
        self._images.clear()
        self._page = None


def _page_sizes(pdfplumber: Any, data: bytes) -> list[tuple[float, float]]:
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            sizes = [(float(page.width), float(page.height)) for page in pdf.pages]
    except Exception:
        raise LeanError("PDF_UNREADABLE") from None
    if not sizes:
        raise LeanError("PDF_UNREADABLE")
    return sizes


def _check_bounds(
    regions: Sequence[VerifyRegion],
    *page_sets: list[tuple[float, float]],
) -> None:
    tolerance = BOUNDS_TOLERANCE_PT
    for region in regions:
        x0, top, x1, bottom = region.bbox
        for pages in page_sets:
            if region.page > len(pages):
                raise LeanError("REGION_OUT_OF_BOUNDS", page=region.page)
            width, height = pages[region.page - 1]
            if (
                x0 < -tolerance
                or top < -tolerance
                or x1 > width + tolerance
                or bottom > height + tolerance
            ):
                raise LeanError("REGION_OUT_OF_BOUNDS", page=region.page)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _region_status(fidelity: Any, removal: Any, *, pixels_skipped: bool) -> str:
    if removal.source_word_count == 0:
        return "unverifiable"
    if fidelity.residual_text_found or removal.residual_word_count > 0:
        return "text_leak"
    if pixels_skipped:
        return "unverifiable"
    if not fidelity.visual_ok:
        return "no_box"
    return "passed"


_RegionKey = tuple[int, tuple[float, float, float, float]]


def _region_key(region: VerifyRegion) -> _RegionKey:
    x0, top, x1, bottom = (float(value) for value in region.bbox)
    return region.page, (x0, top, x1, bottom)


def _build_body(
    regions: Sequence[VerifyRegion],
    unique: Sequence[_RegionKey],
    fidelity: Any,
    removal: Any,
    skipped_pages: set[int],
) -> dict[str, Any]:
    if len(fidelity.regions) != len(unique) or len(removal.regions) != len(unique):
        raise RuntimeError("region count mismatch")
    checks = {
        key: (checked, removed)
        for key, checked, removed in zip(unique, fidelity.regions, removal.regions)
    }
    results: list[dict[str, Any]] = []
    for region in regions:
        key = _region_key(region)
        checked, removed = checks[key]
        results.append(
            {
                "page": region.page,
                "bbox": list(key[1]),
                "status": _region_status(
                    checked, removed, pixels_skipped=region.page in skipped_pages
                ),
                "residual_word_count": max(
                    checked.residual_word_count, removed.residual_word_count
                ),
            }
        )
    body: dict[str, Any] = {
        "passed": all(result["status"] == "passed" for result in results),
        "regions": results,
        "text_removal": {
            "selected": sum(r.source_word_count for r in removal.regions),
            "residual": sum(r.residual_word_count for r in removal.regions),
        },
    }
    body["report_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def verify_pdf_bytes(
    original: bytes,
    redacted: bytes,
    regions: Sequence[VerifyRegion],
) -> dict[str, Any]:
    """Verify that ``redacted`` removed the text under ``regions`` of ``original``.

    Runs ``verify_redacted_text_removed`` (selected source words are gone from
    the whole extracted text, including moved, split or duplicated copies) and
    ``verify_redacted_pdf`` (no selectable word under a region, an opaque box
    drawn, and a pixel check when a raster backend is installed).

    Args:
        original: The source PDF bytes.
        redacted: The redacted PDF bytes.
        regions: Validated regions with 1-based pages and top-left-origin
            ``[x0, top, x1, bottom]`` boxes in PDF points.

    Returns:
        ``{passed, regions: [{page, bbox, status, residual_word_count}],
        text_removal: {selected, residual}, report_sha256}``. ``status`` is
        ``unverifiable`` (no extractable source words), ``text_leak``,
        ``no_box`` or ``passed``; ``passed`` is true only when every region
        passed. Identical regions are checked and counted once. With a raster
        backend, each page of each PDF is rendered at most once, within
        ``PIXEL_BUDGET``; regions on a page too large to render at
        ``MIN_RENDER_DPI`` are ``unverifiable`` unless they leak text.

    Raises:
        LeanError: ``PDF_UNREADABLE``, ``PAGE_COUNT_MISMATCH``,
            ``REGION_OUT_OF_BOUNDS`` (with ``page``) or ``VERIFY_FAILED``.
    """
    try:
        pdfplumber = _import_pdfplumber()
    except Exception as exc:
        _log_failure(exc)
        raise LeanError("VERIFY_FAILED") from None
    original_pages = _page_sizes(pdfplumber, original)
    redacted_pages = _page_sizes(pdfplumber, redacted)
    if len(original_pages) != len(redacted_pages):
        raise LeanError("PAGE_COUNT_MISMATCH")
    _check_bounds(regions, original_pages, redacted_pages)
    # Identical regions are verified once; grouping by page lets the render
    # cache keep only the current page.
    unique = sorted(dict.fromkeys(_region_key(r) for r in regions), key=_page_of)
    spans = [{"page": page - 1, "bbox": list(bbox)} for page, bbox in unique]
    dpis = [
        render_dpi(max(ow, rw), max(oh, rh))
        for (ow, oh), (rw, rh) in zip(original_pages, redacted_pages)
    ]
    raster = _raster_backend_available()
    skipped_pages = (
        {index + 1 for index, dpi in enumerate(dpis) if dpi is None}
        if raster
        else set()
    )
    renders = _PageRenders(dpis)
    try:
        fidelity = verify_redacted_pdf(
            BytesIO(original),
            BytesIO(redacted),
            spans,
            rasterizer=renders.rasterize if raster else None,
        )
        renders.clear()
        removal = verify_redacted_text_removed(
            BytesIO(original), BytesIO(redacted), spans
        )
        return _build_body(regions, unique, fidelity, removal, skipped_pages)
    except Exception as exc:
        _log_failure(exc)
        raise LeanError("VERIFY_FAILED") from None
    finally:
        renders.clear()


def _page_of(key: _RegionKey) -> int:
    return key[0]


def parse_and_verify(body: bytes, content_type: str) -> dict[str, Any]:
    """Parse a verify request body and run the verification.

    Runs in a worker thread: parsing a large multipart body is CPU and memory
    heavy and must not block the event loop.

    Args:
        body: The raw request body (already capped).
        content_type: The request ``Content-Type`` header.

    Returns:
        The :func:`verify_pdf_bytes` response body.

    Raises:
        LeanError: Any code from :func:`parse_multipart_form`,
            :func:`parse_verify_request` or :func:`verify_pdf_bytes`.
    """
    parts = parse_multipart_form(body, content_type)
    regions = parse_verify_request(parts)
    return verify_pdf_bytes(parts["original"], parts["redacted"], regions)


def _log_failure(exc: BaseException) -> None:
    logger.warning("verify pdf failed type=%s", type(exc).__name__)


__all__ = [
    "BOUNDS_TOLERANCE_PT",
    "MAX_RENDER_DPI",
    "MAX_VERIFY_BODY_BYTES",
    "MAX_VERIFY_REGIONS",
    "MIN_RENDER_DPI",
    "PIXEL_BUDGET",
    "parse_and_verify",
    "parse_multipart_form",
    "parse_verify_request",
    "read_capped_body",
    "render_dpi",
    "render_size",
    "silence_pdf_library_logs",
    "verify_pdf_bytes",
]
