"""Provenance helpers: policy fingerprints and build/model identification.

The fingerprints here are profile-level: any change to a loaded policy profile
changes its fingerprint. Nothing in this module touches request text.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from openmed.__about__ import __version__

from .policy import PolicyProfile, list_policies, load_policy

FORK_TAG_ENV = "OPENMED_FORK_TAG"
LEAN_MODEL_ENV = "OPENMED_LEAN_MODEL"
DEFAULT_FORK_TAG = "v3.2.0-mt.dev"

__all__ = ["policy_fingerprint", "provenance"]


def policy_fingerprint(policy: str | PolicyProfile) -> str:
    """Return a stable fingerprint of a policy profile.

    The value is ``"sha256:<hex>"`` over canonical JSON of the loaded profile
    with ``metadata["source"]`` removed.

    Args:
        policy: A policy name from ``list_policies()`` or a ``PolicyProfile``.

    Returns:
        The prefixed hex digest.
    """
    payload: dict[str, Any] = load_policy(policy).to_dict()
    metadata = dict(payload.get("metadata") or {})
    metadata.pop("source", None)
    payload["metadata"] = metadata
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _configured_models() -> list[str]:
    configured = os.environ.get(LEAN_MODEL_ENV, "").strip()
    if configured:
        return [configured]
    from .pii import _DEFAULT_EN_MODEL

    return [_DEFAULT_EN_MODEL]


def provenance() -> dict[str, Any]:
    """Return version, fork tag, configured models and policy fingerprints.

    No model is loaded; ``models`` lists the configured model ids only.

    Returns:
        A JSON-compatible mapping with ``openmed_version``, ``fork_tag``,
        ``models`` and ``policies``.
    """
    return {
        "openmed_version": __version__,
        "fork_tag": os.environ.get(FORK_TAG_ENV, "").strip() or DEFAULT_FORK_TAG,
        "models": _configured_models(),
        "policies": {name: policy_fingerprint(name) for name in list_policies()},
    }
