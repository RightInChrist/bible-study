"""Pydantic schemas for chapter summaries (Slice 8).

The structured shape mirrors the prompt fixture
``fixtures/prompts/chapter-summary-v1.md`` ``Output format`` section
exactly. Mirrors are validated against the parsed JSON at read time;
unparseable summaries are surfaced via the ``raw_summary_text`` field
so the UI can show an error rather than silently dropping the row.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChapterSummaryIntertext(BaseModel):
    """One row in the prompt's ``key_intertexts`` list.

    The prompt is the source of truth for shape; we accept extra-permissively
    on string fields and a free-form ``note`` because chapter intertexts
    vary in granularity.
    """

    model_config = ConfigDict(extra="allow")

    reference: str | None = None
    type: str | None = None
    note: str | None = None


class ChapterSummaryKeySentence(BaseModel):
    """One row in the prompt's ``key_sentences`` list."""

    model_config = ConfigDict(extra="allow")

    sentence_id: str | None = None
    verse_range: str | None = None
    why_pivotal: str | None = None


class ChapterSummaryStructured(BaseModel):
    """Mirrors the JSON shape returned by the chapter-summary subagent."""

    model_config = ConfigDict(extra="allow")

    summary: str | None = None
    narrative_arc: str | None = None
    audience_dynamics: str | None = None
    cultural_throughline: str | None = None
    rhetorical_strategy: str | None = None
    key_intertexts: list[ChapterSummaryIntertext] = Field(default_factory=list)
    pragmatic_arc: str | None = None
    key_sentences: list[ChapterSummaryKeySentence] = Field(default_factory=list)
    open_questions: str | None = None
    candidate_ids_consulted: list[int] = Field(default_factory=list)


class ChapterSummaryResponse(BaseModel):
    """Read-side projection of one ``chapter_summaries`` row."""

    model_config = ConfigDict(extra="forbid")

    summary_id: int
    chapter: int
    prompt_version: str
    source_set_id: str
    model: str
    source_snapshot_hash: str
    summary: ChapterSummaryStructured
    raw_summary_text: str = Field(
        description=(
            "The on-disk JSON string the agent emitted. Carried alongside "
            "the parsed structure so a UI can fall back to displaying it "
            "if the parse came up sparse."
        ),
    )
    generated_at: str
    run_id: str | None = None
    candidate_ids_consulted: list[int] = Field(default_factory=list)
    hidden_bool: bool


class ChapterSummaryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int
    summaries: list[ChapterSummaryResponse]


def parse_summary_payload(raw_text: str) -> ChapterSummaryStructured:
    """Best-effort parse of a chapter_summaries.summary_text JSON blob.

    Returns an empty :class:`ChapterSummaryStructured` if the text is
    not valid JSON or not a top-level object — never raises. The raw
    text is always surfaced separately on :class:`ChapterSummaryResponse`,
    so a UI can show "unparseable" rather than crash.
    """
    import json

    try:
        parsed: Any = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return ChapterSummaryStructured()
    if not isinstance(parsed, dict):
        return ChapterSummaryStructured()
    return ChapterSummaryStructured.model_validate(parsed)


__all__ = [
    "ChapterSummaryIntertext",
    "ChapterSummaryKeySentence",
    "ChapterSummaryStructured",
    "ChapterSummaryResponse",
    "ChapterSummaryListResponse",
    "parse_summary_payload",
]
