"""``BIBLE_STUDY_FAKE_CLAUDE`` env gating.

Eval-harness toggle (``settings.bible_study_fake_claude``) is honored
ONLY in ``env=development`` — the same gate as ``EXTRA_ALLOWED_HOSTS``
and ``EXTRA_ALLOWED_ORIGINS``. Without this gate, a public-facing
deployment could be made to bypass the real Anthropic integration with
a single env var.
"""
from __future__ import annotations

import pytest

from api.runs.routes import (
    _PROCESS_CLAUDE_CLIENT,  # type: ignore[attr-defined]
    get_claude_client,
)
from api.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the cached process-wide Claude client between tests."""
    import api.runs.routes as routes_mod

    routes_mod._PROCESS_CLAUDE_CLIENT = None
    reset_settings_cache()
    yield
    routes_mod._PROCESS_CLAUDE_CLIENT = None
    reset_settings_cache()


def test_fake_claude_honored_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("BIBLE_STUDY_FAKE_CLAUDE", "1")
    reset_settings_cache()
    client = get_claude_client()
    assert client.api_key_set is True
    # Class name reveals it's the fake (built inside _build_eval_fake_claude_client).
    assert type(client).__name__ != "AnthropicClaudeClient"


def test_fake_claude_ignored_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("BIND_HOST_ALLOW_NON_LOCAL", "1")
    monkeypatch.setenv("BIBLE_STUDY_FAKE_CLAUDE", "1")
    reset_settings_cache()
    client = get_claude_client()
    # Real client when env != development, even though the flag is set.
    assert type(client).__name__ == "AnthropicClaudeClient"
