"""Tests for profile-level policy fingerprints and provenance."""

from __future__ import annotations

import dataclasses

from openmed.core.policy import list_policies, load_policy
from openmed.core.provenance import policy_fingerprint, provenance


def test_fingerprint_is_stable_prefixed_sha256() -> None:
    first = policy_fingerprint("hipaa_safe_harbor")
    assert first == policy_fingerprint("hipaa_safe_harbor")
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_fingerprint_differs_across_policies() -> None:
    values = {policy_fingerprint(name) for name in list_policies()}
    assert len(values) == len(list_policies())


def test_fingerprint_ignores_source_but_tracks_profile_changes() -> None:
    profile = load_policy(list_policies()[0])
    base = policy_fingerprint(profile)
    moved = dataclasses.replace(profile, metadata={**profile.metadata, "source": "x"})
    assert policy_fingerprint(moved) == base
    changed = dataclasses.replace(profile, keep_mapping=not profile.keep_mapping)
    assert policy_fingerprint(changed) != base


def test_provenance_shape(monkeypatch) -> None:
    monkeypatch.setenv("OPENMED_FORK_TAG", "v3.2.0-mt.9")
    monkeypatch.setenv("OPENMED_LEAN_MODEL", "org/some-model")
    payload = provenance()
    assert payload["fork_tag"] == "v3.2.0-mt.9"
    assert payload["models"] == ["org/some-model"]
    assert set(payload["policies"]) == set(list_policies())
    assert payload["openmed_version"]


def test_provenance_defaults(monkeypatch) -> None:
    monkeypatch.delenv("OPENMED_FORK_TAG", raising=False)
    monkeypatch.delenv("OPENMED_LEAN_MODEL", raising=False)
    payload = provenance()
    assert payload["fork_tag"] == "v3.2.0-mt.dev"
    assert len(payload["models"]) == 1
