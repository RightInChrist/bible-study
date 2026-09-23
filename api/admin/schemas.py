from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from api.claude.schemas import SourceSetId


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


# ---------------------------------------------------------------------------
# Orphan-resolution schemas (Architect §Contracts; PLAN §OrphanSummary)
# ---------------------------------------------------------------------------


OrphanReason = Literal["sentence_id_remapped", "sentence_text_changed", "sentence_removed"]


OrphanDisposition = Literal["delete", "keep", "remap"]


class OrphanCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: int
    sentence_id_old: str
    sentence_id_new: str | None = None
    reason: OrphanReason
    style_prompt_version: str
    source_set_id: SourceSetId
    model: str
    generated_at: str
    candidate_text_excerpt: str
    old_sentence_text_excerpt: str
    new_sentence_text_excerpt: str | None = None
    suggested_remap_sentence_id: str | None = None


class OrphanRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_id_old: str
    sentence_id_new: str | None = None
    reason: OrphanReason
    ranked_candidate_count: int
    has_notes: bool
    has_tie_break: bool
    version: int
    suggested_remap_sentence_id: str | None = None


class OrphanOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overlay_id: int
    range_start_sentence_id_old: str
    range_end_sentence_id_old: str
    reason: OrphanReason
    origin: Literal["berean", "manual"]
    rejected: bool
    suggested_remap_start_sentence_id: str | None = None
    suggested_remap_end_sentence_id: str | None = None


class OrphanSummary(BaseModel):
    """Pinned shape of ``ErrorResponse.details.orphan_summary`` (PLAN §Data)."""

    model_config = ConfigDict(extra="forbid")

    affected_candidates: list[OrphanCandidate] = Field(default_factory=list)
    affected_rankings: list[OrphanRanking] = Field(default_factory=list)
    affected_overlays: list[OrphanOverlay] = Field(default_factory=list)
    total: int


# ---------------------------------------------------------------------------
# Reimport endpoint
# ---------------------------------------------------------------------------


class ReimportRequest(BaseModel):
    """Request body for ``POST /api/v1/admin/reimport``.

    ``force=False`` runs the orphan pre-check; on detected orphans the
    response is ``409 orphans_detected`` with the structured summary.
    ``force=True`` skips the pre-check and applies any per-orphan
    dispositions the user authored on the orphan-resolution screen.
    Dispositions are keyed by an opaque per-orphan ID — for candidates
    the ID is ``f"candidate:{candidate_id}"``; for rankings ``f"ranking:{sentence_id_old}"``;
    for overlays ``f"overlay:{overlay_id}"``.
    """

    model_config = ConfigDict(extra="forbid")

    force: bool = False
    dispositions: dict[str, OrphanDisposition] | None = None


class ReimportResponse(BaseModel):
    """Returned by ``POST /api/v1/admin/reimport`` on success."""

    model_config = ConfigDict(extra="forbid")

    fixture_version: str
    files_imported: int
    sentences_built: int
    words_built: int
    byzantine_verses: int
    english_verses: int
    bib_interlinear_words: int
    red_letter_source_ranges: int
    style_prompts: int
    elapsed_ms: int


# ---------------------------------------------------------------------------
# Build-static endpoint (Architect §Contracts)
# ---------------------------------------------------------------------------


class BuildStaticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_unranked_placeholders: bool = True


class BuildStaticCoverage(BaseModel):
    """Architect §BuildStaticCoverage semantics (pinned)."""

    model_config = ConfigDict(extra="forbid")

    ranked: int
    total: int
    ch5_ranked: int
    ch5_total: int


class BuildStaticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dist_path: str
    files_written: int
    coverage: BuildStaticCoverage
    took_ms: int
