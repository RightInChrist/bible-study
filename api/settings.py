from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Process-wide configuration loaded from .env / environment variables.

    Security §Build-time guards: refuse to start if BIND_HOST is anything
    other than 127.0.0.1 / localhost / ::1 unless BIND_HOST_ALLOW_NON_LOCAL=1.
    """

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT_DEFAULT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["development", "staging", "production"] = Field(default="development")
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=8000)
    bind_host_allow_non_local: bool = Field(default=False)

    database_path: str = Field(default="data/bible_study.db")
    project_root_override: str = Field(default="")

    # ---- Generation: subscription-aware caps (CLAUDE.md §Generation mechanism)
    # No dollar caps — usage is subscription-bounded, not per-token billed.
    # ``MAX_WORKTREES_PER_RUN`` replaces the old ``MAX_SENTENCES_PER_RUN``;
    # we keep ``max_sentences_per_run`` as an alias property so legacy
    # callers / env vars still work during the transition.
    max_worktrees_per_run: int = Field(default=200)
    max_runs_per_day: int = Field(default=100)
    max_wall_clock_seconds_per_sentence: int = Field(default=300)
    max_concurrent_worktrees: int = Field(default=2)
    worktree_base_dir: str = Field(default="data/worktrees")
    claude_cli_path: str = Field(default="claude")
    # Default Claude model id passed via ``claude -p --model <id>``. The CLI
    # accepts an alias (e.g. ``opus``) or a full name (e.g. ``claude-opus-4-7``).
    claude_model: str = Field(default="claude-opus-4-7")
    # Default effort level passed via ``claude -p --effort <level>``. CLI
    # accepts: low, medium, high, xhigh, max (verified via ``claude --help``).
    claude_effort: str = Field(default="xhigh")

    max_error_message_bytes: int = Field(default=2048)

    # Context window (slice 3c). Adjacent SBLGNT sentences before / after
    # the focal are added to the source bundle when the active prompt
    # opts in via ``wants_context_window: true`` in its front-matter.
    # Two separate values let an asymmetric window (e.g. 5 before, 2
    # after) be configured without lobbying for a per-request override.
    context_window_before: int = Field(default=3, ge=0, le=10)
    context_window_after: int = Field(default=3, ge=0, le=10)

    backup_interval_seconds: int = Field(default=3600)
    backup_retention_count: int = Field(default=24)
    backup_hard_cap: int = Field(default=32)

    @field_validator("bind_host")
    @classmethod
    def _validate_bind_host(cls, value: str) -> str:
        allowed = {"127.0.0.1", "localhost", "::1"}
        if value in allowed:
            return value
        if os.environ.get("BIND_HOST_ALLOW_NON_LOCAL") == "1":
            return value
        raise ValueError(
            f"BIND_HOST={value!r} is not loopback. Set BIND_HOST=127.0.0.1 / "
            f"localhost / ::1, or set BIND_HOST_ALLOW_NON_LOCAL=1 to override."
        )

    @property
    def project_root(self) -> Path:
        if self.project_root_override:
            return Path(self.project_root_override)
        return _PROJECT_ROOT_DEFAULT

    @property
    def database_path_absolute(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    @property
    def max_sentences_per_run(self) -> int:
        """Back-compat alias for ``max_worktrees_per_run`` — same unit."""
        return self.max_worktrees_per_run


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test hook — clear the settings cache so a fresh env can be picked up."""
    get_settings.cache_clear()


@lru_cache(maxsize=1)
def get_commit_hash() -> str:
    """Resolve the current git commit at process startup.

    Returns 'unknown' when git is unavailable (e.g. running from a tarball).
    Cached because the value is fixed for the process lifetime.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_PROJECT_ROOT_DEFAULT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"
