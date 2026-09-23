"""Pydantic schemas for the generation-run API (Architect §Contracts).

``RunScope`` is a discriminated union on ``kind`` (Architect §RunScope).
``CreateRunRequest`` / ``RunResponse`` / ``RunListResponse`` are the
HTTP-surface schemas; the SSE payload shapes live in ``api.runs.sse``.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from api.claude.schemas import (
    ChapterSourceSetId,
    GenerationErrorCode,
    SourceSetId,
)


# Slice 8: the request-level source_set_id literal accepts either the
# per-sentence enum or the synthetic chapter-bundle value. The runner
# routes by ``scope.kind`` — chapter scopes require ``CHAPTER_BUNDLE``;
# per-sentence scopes require one of the seven per-sentence values.
RequestSourceSetId = SourceSetId | ChapterSourceSetId


RunStatus = Literal[
    "pending", "running", "completed", "failed", "cancelled", "interrupted"
]

ItemStatus = Literal[
    "pending", "running", "completed", "failed", "cancelled", "interrupted"
]


class OneSentenceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["one_sentence"] = "one_sentence"
    sentence_id: str


class VerseRangeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["verse_range"] = "verse_range"
    chapter: int = Field(ge=1, le=28)
    start_verse: int = Field(ge=1)
    end_verse: int = Field(ge=1)


class WholeChapterScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["whole_chapter"] = "whole_chapter"
    chapter: int = Field(ge=1, le=28)


class AllUnrankedRedLetterScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["all_unranked_red_letter"] = "all_unranked_red_letter"
    # ``as_of`` is captured by the server at run-creation time per Architect
    # §RunScope. Optional on input — clients may omit and the server fills it.
    as_of: str | None = None


class ChapterSummaryScope(BaseModel):
    """Slice 8 — synthesise one chapter into a chapter-summary candidate.

    ``items_count`` is always 1 for this scope: one synthesis call,
    bundled with narrative_text + every red-letter sentence's saved
    candidates. Counts as 1 worktree against ``MAX_WORKTREES_PER_RUN`` /
    ``MAX_RUNS_PER_DAY`` caps.
    """

    model_config = ConfigDict(extra="forbid")
    kind: Literal["chapter_summary"] = "chapter_summary"
    chapter: int = Field(ge=1, le=28)


RunScope = Annotated[
    Union[
        OneSentenceScope,
        VerseRangeScope,
        WholeChapterScope,
        AllUnrankedRedLetterScope,
        ChapterSummaryScope,
    ],
    Field(discriminator="kind"),
]


class CreateRunRequest(BaseModel):
    """Body for ``POST /api/v1/runs``."""

    model_config = ConfigDict(extra="forbid")

    scope: RunScope
    style_prompt_version: str
    source_set_id: RequestSourceSetId
    model: str = Field(
        default="claude-opus-4-7",
        min_length=1,
        description=(
            "Claude model identifier passed as ``claude --model <id>``. "
            "Accepts an alias ('opus', 'sonnet') or a full name "
            "('claude-opus-4-7'). Validated against the CLI's accepted "
            "list lazily at spawn time — not at request time."
        ),
    )
    effort: str = Field(
        default="xhigh",
        min_length=1,
        description=(
            "Reasoning-effort level passed as ``claude --effort <level>``. "
            "CLI accepts: low, medium, high, xhigh, max. Higher effort "
            "increases reasoning tokens and latency."
        ),
    )


class RunResponse(BaseModel):
    """Returned by ``POST /api/v1/runs`` and ``GET /api/v1/runs/{run_id}``."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: RunStatus
    style_prompt_version: str
    source_set_id: RequestSourceSetId
    model: str
    items_count: int = Field(description="Number of sentences in the run's resolved scope.")
    items_completed: int
    items_failed: int
    items_pending: int
    items_running: int
    items_cancelled: int
    items_interrupted: int
    estimated_worktree_count: int = Field(
        description=(
            "Number of Claude Code worktrees this run will spawn — one per "
            "sentence in scope. Replaces the SDK-era estimated_cost_usd."
        ),
    )
    sentence_ids: list[str] = Field(
        default_factory=list,
        description="The expanded scope, in dispatch order (ordinal 1..N).",
    )
    chapter: int | None = Field(
        default=None,
        description=(
            "For chapter-summary scope runs, the chapter number being "
            "synthesised. None for per-sentence scope runs."
        ),
    )
    parent_run_id: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class RunListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: RunStatus
    style_prompt_version: str
    source_set_id: RequestSourceSetId
    model: str
    items_count: int
    items_completed: int
    items_failed: int
    estimated_worktree_count: int
    created_at: str
    completed_at: str | None = None


class RunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[RunListItem]
    limit: int


class CandidateResponse(BaseModel):
    """One Claude candidate for a sentence (read path)."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: int
    sentence_id: str
    style_prompt_version: str
    source_set_id: SourceSetId
    model: str
    generated_at: str
    candidate_text: str
    source_snapshot_hash: str
    hidden_bool: bool
    latency_ms: int | None = None


class SentenceCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    candidates: list[CandidateResponse]


class RunItemSnapshot(BaseModel):
    """Internal — used by the SSE replayer to feed events.

    For chapter-summary scope items, ``sentence_id`` is None and
    ``chapter`` carries the synthesised chapter number.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    ordinal: int
    sentence_id: str | None = None
    chapter: int | None = None
    status: ItemStatus
    candidate_id: int | None = None
    error_code: GenerationErrorCode | None = None
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
