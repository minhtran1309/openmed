"""Fixed error vocabulary and the single error-response helper.

Every non-200 response of the lean service has the body
``{"error_code": CODE}`` and nothing else. Request text is never echoed and
exception messages are never logged, since they may carry PHI.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("openmed.service.lean")

ERROR_CODES: tuple[str, ...] = (
    "INVALID_REQUEST",
    "INTERNAL_ERROR",
    "NOT_FOUND",
    "UNKNOWN_POLICY",
    "EMPTY_DOCUMENT",
    "DUPLICATE_SEGMENT_ID",
    "UNKNOWN_SEGMENT_ID",
    "SPAN_OUT_OF_RANGE",
    "SPAN_INVERTED",
    "UNKNOWN_LABEL",
    "UNSUPPORTED_TREATMENT",
    "DATE_SHIFT_KEY_REQUIRED",
    "DETECT_FAILED",
    "APPLY_FAILED",
    "AUDIT_KEY_MISSING",
)

_STATUS: dict[str, int] = {
    "NOT_FOUND": 404,
    "UNKNOWN_POLICY": 404,
    "INTERNAL_ERROR": 500,
    "DETECT_FAILED": 500,
    "APPLY_FAILED": 500,
    "AUDIT_KEY_MISSING": 500,
}


def status_for(error_code: str) -> int:
    """Return the HTTP status for an error code (400 unless listed)."""
    return _STATUS.get(error_code, 400)


class LeanError(Exception):
    """Raised by endpoints to return ``{"error_code": CODE}``.

    Args:
        error_code: A member of ``ERROR_CODES``.

    Raises:
        ValueError: If the code is not in the fixed vocabulary.
    """

    def __init__(self, error_code: str) -> None:
        if error_code not in ERROR_CODES:
            raise ValueError("error_code is not in the documented vocabulary")
        super().__init__(error_code)
        self.error_code = error_code


def error_response(error_code: str, status_code: int | None = None) -> JSONResponse:
    """Build the one allowed error body for ``error_code``."""
    code = error_code if error_code in ERROR_CODES else "INTERNAL_ERROR"
    return JSONResponse(
        {"error_code": code},
        status_code=status_code if status_code is not None else status_for(code),
    )


def _handle_error_code(request: Request, exc: Exception) -> JSONResponse:
    return error_response(exc.error_code)  # type: ignore[attr-defined]


def _handle_http(request: Request, exc: Exception) -> JSONResponse:
    status = getattr(exc, "status_code", 500)
    if status == 404:
        return error_response("NOT_FOUND")
    if status < 500:
        return error_response("INVALID_REQUEST", status)
    return error_response("INTERNAL_ERROR")


def _handle_validation(request: Request, exc: Exception) -> JSONResponse:
    return error_response("INVALID_REQUEST")


def _code_for_exception(exc: BaseException) -> str:
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code in ERROR_CODES:
        return code
    return "INTERNAL_ERROR"


class SafeErrorsMiddleware:
    """Pure ASGI middleware that turns any escaping exception into a safe body.

    Unlike ``add_exception_handler(Exception, ...)``, which Starlette runs in
    ``ServerErrorMiddleware`` and then re-raises (so the server logs the full
    traceback and message), this never re-raises. Only the exception type name
    is logged: messages and tracebacks may contain PHI.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            code = _code_for_exception(exc)
            if code == "INTERNAL_ERROR":
                logger.error("unhandled error type=%s", type(exc).__name__)
            if started:
                return
            await error_response(code)(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    """Register the single error mapping on ``app``.

    Any exception carrying a vocabulary ``error_code`` attribute (including
    ``LeanError`` and library errors such as ``ApplySpansError``) is mapped
    straight through; everything unknown becomes ``INTERNAL_ERROR``.
    """
    app.add_exception_handler(LeanError, _handle_error_code)
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    app.add_exception_handler(RequestValidationError, _handle_validation)
    app.add_middleware(SafeErrorsMiddleware)
