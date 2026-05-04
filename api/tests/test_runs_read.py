"""Read-API smoke tests for runs + candidates.

The runner-level tests already exercise these paths transitively;
this file adds explicit coverage of the read shapes' Pydantic
``response_model`` so future schema changes don't drift unnoticed.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_run_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/api/v1/runs/not-a-real-run-id")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "run_not_found"


def test_list_runs_returns_empty_list_when_db_is_fresh(client: TestClient) -> None:
    response = client.get("/api/v1/runs?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 5
    assert body["runs"] == []


def test_list_sentence_candidates_returns_empty_when_no_candidates(
    client: TestClient,
) -> None:
    sentence_id = "mat-5-4"
    response = client.get(f"/api/v1/sentences/{sentence_id}/candidates")
    assert response.status_code == 200
    body = response.json()
    assert body["sentence_id"] == sentence_id
    assert body["candidates"] == []
