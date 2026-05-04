"""Security middleware coverage from the slice scope.

Slice scope tests required:
  - test_origin_required_on_writes (smoke)
  - test_x_requested_by_required_on_writes (smoke)
  - test_host_header_allowlist_rejects_attacker_com

There are no production write endpoints in this slice, so we add a temporary
write route to the test app to exercise the CSRF middleware. This is the
shape Reliability's `test_csrf_coverage_parametrised_over_openapi` will
extend with full OpenAPI introspection in a follow-up slice.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.main import create_app
from api.settings import get_commit_hash


class _StubBody(BaseModel):
    ping: str


@pytest.fixture
def writable_client(imported_db) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    get_commit_hash.cache_clear()
    app = create_app()

    @app.post("/api/v1/_test/write", response_model=_StubBody)
    def _stub(body: _StubBody) -> _StubBody:
        return body

    test_client = TestClient(app, base_url="http://127.0.0.1:8000")
    yield test_client
    test_client.close()


def test_host_header_allowlist_rejects_attacker_com(writable_client: TestClient) -> None:
    """Host header allowlist is the DNS-rebinding defence (Security §Authn / Authz)."""
    response = writable_client.get(
        "/api/v1/health",
        headers={"Host": "attacker.com"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_host"


def test_host_header_allows_loopback(writable_client: TestClient) -> None:
    response = writable_client.get(
        "/api/v1/health",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200


def test_origin_required_on_writes(writable_client: TestClient) -> None:
    """No Origin header on a state-changing route → 403 origin_required."""
    response = writable_client.post(
        "/api/v1/_test/write",
        json={"ping": "hi"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


def test_origin_evil_rejected(writable_client: TestClient) -> None:
    response = writable_client.post(
        "/api/v1/_test/write",
        headers={
            "Origin": "http://evil.example",
            "X-Requested-By": "bible-study-ui",
        },
        json={"ping": "hi"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


def test_x_requested_by_required_on_writes(writable_client: TestClient) -> None:
    """Allow-listed Origin without X-Requested-By → 403 x_requested_by_required."""
    response = writable_client.post(
        "/api/v1/_test/write",
        headers={"Origin": "http://localhost:5173"},
        json={"ping": "hi"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "x_requested_by_required"


def test_write_succeeds_with_proper_csrf_headers(writable_client: TestClient) -> None:
    response = writable_client.post(
        "/api/v1/_test/write",
        headers={
            "Origin": "http://localhost:5173",
            "X-Requested-By": "bible-study-ui",
        },
        json={"ping": "hi"},
    )
    assert response.status_code == 200
    assert response.json()["ping"] == "hi"


def test_extra_allowed_hosts_honoured_in_development(
    imported_db,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev convenience: ``EXTRA_ALLOWED_HOSTS`` extends the loopback
    allowlist so the eval harness can reach the API via host.docker.internal.
    """
    from api.settings import reset_settings_cache

    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("EXTRA_ALLOWED_HOSTS", "host.docker.internal")
    reset_settings_cache()
    get_commit_hash.cache_clear()

    from api.main import create_app

    app = create_app()
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    try:
        response = client.get(
            "/api/v1/health",
            headers={"Host": "host.docker.internal:8000"},
        )
        assert response.status_code == 200
    finally:
        client.close()


def test_extra_allowed_hosts_ignored_outside_development(
    imported_db,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security: ``EXTRA_ALLOWED_HOSTS`` MUST be ignored in production /
    staging because both surfaces are publicly reachable; honoring the env
    var would let any caller widen the DNS-rebinding defence by setting
    a process-environment value.
    """
    from api.settings import reset_settings_cache

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("EXTRA_ALLOWED_HOSTS", "evil.com")
    monkeypatch.setenv("BIND_HOST_ALLOW_NON_LOCAL", "1")
    reset_settings_cache()
    get_commit_hash.cache_clear()

    from api.main import create_app

    app = create_app()
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    try:
        response = client.get(
            "/api/v1/health",
            headers={"Host": "evil.com"},
        )
        assert response.status_code == 400, (
            "EXTRA_ALLOWED_HOSTS must be silently dropped outside dev"
        )
        assert response.json()["code"] == "invalid_host"
    finally:
        client.close()


def test_test_only_path_csrf_bypass_ignored_outside_development(
    imported_db,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security: the CSRF bypass for ``/api/v1/admin/test-only/*`` MUST be
    double-gated on ``env == 'development'``. In production / staging the
    middleware must enforce Origin / X-Requested-By even on test-only
    paths, so a future engineer who mistakenly mounts a test-only route
    without env-gating the *mount* cannot leak a CSRF bypass.

    We mount a stub POST under ``/api/v1/admin/test-only/foo`` and assert
    that an Origin-less request in production is rejected with 403
    ``origin_required`` — same shape as the regular write-path test.
    """
    from api.settings import reset_settings_cache

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BIND_HOST_ALLOW_NON_LOCAL", "1")
    reset_settings_cache()
    get_commit_hash.cache_clear()

    from api.main import create_app

    app = create_app()

    @app.post("/api/v1/admin/test-only/foo", response_model=_StubBody)
    def _stub_test_only(body: _StubBody) -> _StubBody:
        return body

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    try:
        response = client.post(
            "/api/v1/admin/test-only/foo",
            json={"ping": "hi"},
        )
        assert response.status_code == 403, (
            "test-only CSRF bypass must be silently dropped outside dev"
        )
        assert response.json()["code"] == "origin_required"
    finally:
        client.close()
