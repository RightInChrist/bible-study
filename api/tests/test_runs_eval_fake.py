"""Worktree-spawner dependency wiring.

Slice 3a-redo replaced the SDK-era ``BIBLE_STUDY_FAKE_CLAUDE`` env-var
fake with explicit dependency injection: tests pass a fake spawner via
``app.dependency_overrides[get_worktree_spawner]``; production always
gets the real :class:`ClaudeCodeWorktreeSpawner`. There is no env-var
toggle anymore — the previous toggle was a security wart (a public
deployment could disable real generation with a single env flip; the
twin-override pattern's env-gate was the band-aid).

These tests guard the new contract: the default-resolved spawner is
always the production spawner, and tests inject the fake explicitly.
"""
from __future__ import annotations

import pytest

from api.runs.routes import get_worktree_spawner
from api.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Clear the cached process-wide spawner between tests."""
    import api.runs.routes as routes_mod

    routes_mod._PROCESS_WORKTREE_SPAWNER = None
    reset_settings_cache()
    yield
    routes_mod._PROCESS_WORKTREE_SPAWNER = None
    reset_settings_cache()


def test_default_spawner_is_real_claude_code_worktree_spawner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    reset_settings_cache()
    spawner = get_worktree_spawner()
    assert type(spawner).__name__ == "ClaudeCodeWorktreeSpawner"


def test_spawner_is_singleton_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    reset_settings_cache()
    a = get_worktree_spawner()
    b = get_worktree_spawner()
    assert a is b
