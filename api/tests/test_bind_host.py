"""Security #1 in the test list: bind refuses non-loopback.

Implemented as a settings-load test: the launcher reads Settings at import
time, and Settings._validate_bind_host raises on 0.0.0.0. We verify the
validator directly because spinning up uvicorn just to assert it dies is
flakier than asserting the load contract.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.settings import Settings


def test_server_refuses_to_start_with_bind_host_0_0_0_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIND_HOST_ALLOW_NON_LOCAL", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(bind_host="0.0.0.0")  # type: ignore[arg-type]
    error_text = str(excinfo.value)
    assert "0.0.0.0" in error_text
    assert "BIND_HOST_ALLOW_NON_LOCAL" in error_text


def test_server_accepts_loopback_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIND_HOST_ALLOW_NON_LOCAL", raising=False)
    for host in ("127.0.0.1", "localhost", "::1"):
        s = Settings(bind_host=host)
        assert s.bind_host == host


def test_server_accepts_non_loopback_when_override_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented escape hatch — explicit env var unblocks non-loopback bind."""
    monkeypatch.setenv("BIND_HOST_ALLOW_NON_LOCAL", "1")
    s = Settings(bind_host="0.0.0.0")  # type: ignore[arg-type]
    assert s.bind_host == "0.0.0.0"
