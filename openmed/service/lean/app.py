"""FastAPI app factory for the lean service.

Later endpoints are added as ``APIRouter`` instances passed to ``create_app``
(or included in ``_default_routers``); errors flow through ``LeanError`` or any
exception carrying a vocabulary ``error_code``. Request and response bodies are
never logged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from openmed.core.provenance import provenance

from .errors import LeanError, install_error_handlers
from .schemas import (
    HealthResponse,
    ProvenanceResponse,
    get_schema,
)

router = APIRouter()


def get_provenance_payload() -> dict[str, Any]:
    """Dependency supplying the provenance payload (overridable in tests)."""
    return provenance()


@router.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/v1/provenance", response_model=ProvenanceResponse)
def get_provenance(
    payload: dict[str, Any] = Depends(get_provenance_payload),
) -> dict[str, Any]:
    """Return version, fork tag, configured models and policy fingerprints."""
    return payload


@router.get("/v1/schemas/{name}")
def get_schema_by_name(name: str) -> dict[str, Any]:
    """Return a published JSON Schema or NOT_FOUND."""
    schema = get_schema(name)
    if schema is None:
        raise LeanError("NOT_FOUND")
    return schema


def create_app(extra_routers: tuple[APIRouter, ...] = ()) -> FastAPI:
    """Create the lean service app.

    Args:
        extra_routers: Additional routers to mount (later endpoints).

    Returns:
        A FastAPI app with the shared error handlers installed.
    """
    app = FastAPI(
        title="OpenMed lean service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_error_handlers(app)
    app.include_router(router)
    for extra in extra_routers:
        app.include_router(extra)
    return app


app = create_app()
