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

    anthropic_api_key: str = Field(default="")

    max_sentences_per_run: int = Field(default=200)
    max_run_cost_usd: float = Field(default=5.0)
    max_daily_cost_usd: float = Field(default=20.0)
    max_concurrent_anthropic_calls: int = Field(default=4)
    max_error_message_bytes: int = Field(default=2048)

    anthropic_request_timeout_seconds: int = Field(default=120)
    default_claude_model: str = Field(default="claude-sonnet-4-6")

    # Eval-harness toggle: when set, the runner uses a deterministic
    # fake-Claude responder instead of the real Anthropic SDK. Honored
    # only in ``env=development`` (security gate matches the twin-override
    # pattern from user-CLAUDE.md). The fake's body is hashed from the
    # source bundle so candidates are reproducible across eval runs.
    bible_study_fake_claude: bool = Field(default=False)

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
