from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_endpoint_returns_commit_hash(client: TestClient) -> None:
    """user-CLAUDE.md "verify your assumptions" rule: /health surfaces the
    commit hash so the running server can be matched to the source tree."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["commit_hash"], str) and body["commit_hash"]
    assert body["env"] == "development"
