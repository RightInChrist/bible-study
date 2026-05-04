"""Pydantic models for the Claude generation backend (Slice 3a).

All schemas live here per global rules. The runner/HTTP layer
(``api.runs.schemas``) carries its own request/response shapes; this
module owns the Claude-specific contracts: source-set enum, prompt
metadata, source bundle wrapper, and the Claude-call request/response.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Architect §Claude candidate identity → ``source_set_id`` enum (v1, locked).
SourceSetId = Literal[
    "SBLGNT_ONLY",
    "BYZ_ONLY",
    "BOTH_GREEK",
    "GREEK_PLUS_BIB",
    "GREEK_PLUS_BLB",
    "GREEK_PLUS_BSB",
    "ENGLISH_ONLY_BSB",
]


# ``error_code`` values pinned in PLAN §generation_run_items schema CHECK.
GenerationErrorCode = Literal[
    "anthropic_api_error",
    "anthropic_refusal",
    "timeout",
    "rate_limit",
    "invalid_response",
    "disk_full",
    "internal",
]


class StylePrompt(BaseModel):
    """One versioned style-prompt fixture (Architect §Prompt artifact format).

    Body content (the markdown after the YAML front-matter) is the actual
    text sent to Claude as the system prompt; ``compatible_source_sets``
    is the validation gate the Claude client checks before substituting
    placeholders or making any call.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Family name, e.g. 'literal'.")
    version: str = Field(description="Versioned identifier, e.g. 'literal-v1'.")
    description: str
    requires_greek: bool
    compatible_source_sets: list[SourceSetId]
    body: str = Field(description="Prompt body — the markdown after the front-matter.")


class BibInterlinearWordBundle(BaseModel):
    """One BIB interlinear row in a source bundle."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    verse: int
    position: int
    greek_form: str
    strong_id: str
    transliteration: str
    english_gloss: str


class VerseTextBundle(BaseModel):
    """One verse-keyed text row in a source bundle."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    verse: int
    text: str


class SourceBundle(BaseModel):
    """Canonical bundle handed to Claude for one ``(sentence, source_set)``.

    Per-field optionality reflects the seven ``SourceSetId`` shapes:
      - ``SBLGNT_ONLY`` → ``sblgnt`` only.
      - ``BYZ_ONLY`` → ``byzantine_verses`` only.
      - ``BOTH_GREEK`` → both.
      - ``GREEK_PLUS_BIB`` → SBLGNT + Byzantine + BIB rows.
      - ``GREEK_PLUS_BLB`` / ``GREEK_PLUS_BSB`` → SBLGNT + Byzantine + the named English.
      - ``ENGLISH_ONLY_BSB`` → BSB only.

    Serialization to ``payload_json`` for ``source_snapshots`` reuses
    ``api.importer.manifest.serialize_manifest_for_hashing``-style canonical
    JSON (Architect canonicalization rule). Only integers and strings —
    no floats. The ``snapshot_hash`` FK on ``claude_candidates`` is the
    SHA-256 of that canonicalised payload.
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    source_set_id: SourceSetId
    fixture_version: str
    prompt_version: str
    sblgnt: str | None = None
    byzantine_verses: list[VerseTextBundle] = Field(default_factory=list)
    bib_interlinear: list[BibInterlinearWordBundle] = Field(default_factory=list)
    blb_verses: list[VerseTextBundle] = Field(default_factory=list)
    bsb_verses: list[VerseTextBundle] = Field(default_factory=list)
    verse_range: str = Field(description="Human-readable range, e.g. '5:3'.")


class CostEstimate(BaseModel):
    """Cost estimate for one Claude call (Architect §Claude client)."""

    model_config = ConfigDict(extra="forbid")

    estimated_input_units: int = Field(
        description="Character-count heuristic: round(total_chars / 4)."
    )
    estimated_cost_usd: float = Field(
        description="Mid-point cost estimate in USD."
    )
    estimated_cost_usd_band_pct: int = Field(
        default=20,
        description="Confidence band (e.g. 20 = ±20%).",
    )


class CandidateGenerated(BaseModel):
    """The successful return shape from the Claude client."""

    model_config = ConfigDict(extra="forbid")

    candidate_text: str
    model: str
    generated_at: str = Field(description="ISO-8601 UTC timestamp.")
    latency_ms: int
    input_tokens: int | None = Field(
        default=None,
        description="Reported by the SDK when available; null when the SDK doesn't surface it.",
    )
    output_tokens: int | None = None
