"""Lean OpenMed service: a small, PHI-safe HTTP surface for de-identification."""

from __future__ import annotations

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(name)
