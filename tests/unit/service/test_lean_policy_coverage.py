"""Tests for ``GET /v1/policies/{name}/coverage`` on the lean service."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openmed.core.policy import compile_policy, list_policies, verify_policy_plan
from openmed.core.provenance import policy_fingerprint
from openmed.service.lean.app import create_app
from openmed.service.lean.schemas import schema_names


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("name", ["ccia_l1", "ccia_l2", "ccia_l3"])
def test_coverage_for_ccia_profiles(client: TestClient, name: str) -> None:
    response = client.get(f"/v1/policies/{name}/coverage")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"covered_labels", "coverage_percent", "fingerprint"}
    assert body["coverage_percent"] == 100.0
    assert body["fingerprint"] == policy_fingerprint(name)
    compiled = compile_policy(name)
    proof = verify_policy_plan(name, compiled.plan)
    assert body["covered_labels"] == sorted(proof.required_labels)
    assert "DATE" in body["covered_labels"]
    assert "AGE" not in body["covered_labels"]


def test_coverage_works_for_every_profile(client: TestClient) -> None:
    for name in list_policies():
        response = client.get(f"/v1/policies/{name}/coverage")
        assert response.status_code == 200, name
        assert response.json()["fingerprint"] == policy_fingerprint(name)


def test_coverage_levels_are_cumulative(client: TestClient) -> None:
    covered = [
        set(client.get(f"/v1/policies/{name}/coverage").json()["covered_labels"])
        for name in ("ccia_l1", "ccia_l2", "ccia_l3")
    ]
    assert covered[0] < covered[1] < covered[2]


def test_unknown_policy_returns_error_code(client: TestClient) -> None:
    response = client.get("/v1/policies/not_a_policy/coverage")

    assert response.status_code == 404
    assert response.json() == {"error_code": "UNKNOWN_POLICY"}


def test_coverage_response_schema_published(client: TestClient) -> None:
    assert "policy_coverage_response" in schema_names()
    schema = client.get("/v1/schemas/policy_coverage_response").json()
    assert set(schema["properties"]) == {
        "covered_labels",
        "coverage_percent",
        "fingerprint",
    }
