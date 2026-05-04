from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Returned by ``GET /api/v1/health``.

    Implements the user-CLAUDE.md "verify your assumptions" rule: the
    ``commit_hash`` must match ``git rev-parse --short HEAD`` so Gavin can
    confirm the running server is the code he expects.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    commit_hash: str = Field(description="Short git SHA captured at process startup.")
    env: Literal["development", "staging", "production"]


class FixtureStatusResponse(BaseModel):
    """Returned by ``GET /api/v1/admin/fixture-status`` — Designer status pill."""

    model_config = ConfigDict(extra="forbid")

    disk_manifest_hash: str = Field(
        description="SHA-256 of fixtures/manifest.json computed on demand."
    )
    db_fixture_version: str | None = Field(
        default=None,
        description="manifest_hash recorded by the last successful import; null when "
        "the fixture_version table has not been populated yet.",
    )
    stale: bool = Field(
        description="True iff disk_manifest_hash != db_fixture_version (or db_fixture_version is null)."
    )
    last_imported_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp recorded by the last successful import; null when not yet imported.",
    )


class ErrorResponse(BaseModel):
    """The single error shape across the API (Architect §HTTP API)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, object] | None = None
