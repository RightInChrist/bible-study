"""Pydantic schemas for the GSV (Gavin Standard Version) export (Slice 5).

Architect §Contracts pins the GSV provenance contract as a discriminated
union on ``kind``. The structured-JSON export is the canonical machine-
readable form; plain-text and Markdown are renderings derived from the
same per-sentence resolution.

The four ``kind`` variants are:

  - ``translation`` — winner is a named open-source translation card
    (BSB / BLB / WEB / SBLGNT / BYZ); text comes from the translation's
    verse range.
  - ``claude`` — winner is a Claude-generated candidate; text comes from
    the candidate's stored ``candidate_text`` (with the ``english``
    field extracted for ``output_format=json`` candidates).
  - ``tie`` — top rank has multiple entries and no tie-break decision
    has resolved them; structured/Markdown render this verbatim, plain-
    text refuses to render the chapter (Architect contract).
  - ``tie-broken`` — a ``tie_break_decisions`` row resolved a prior tie;
    the winner shape mirrors a single-winner ``translation``/``claude``
    plus the original ``tied_against`` list and ``resolved_at``.
  - ``unranked`` — red-letter sentence with no saved ranking; surfaces
    in the JSON form so consumers can show "rank now"; renders muted in
    plain-text and Markdown.

Manager DoD scopes ranking to red letters first; non-red-letter
sentences default to a BSB ``translation`` provenance so the GSV is a
continuous text. Provenance is honest — it's BSB, not a curated pick.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from api.claude.schemas import SourceSetId
from api.rankings.schemas import TranslationName


# A "single-winner" provenance — used for both ``translation``/``claude``
# top-level kinds and as the inner ``winner`` of a ``tie-broken``.
class TranslationWinner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["translation"] = "translation"
    name: TranslationName
    verse_range: str


class ClaudeWinner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["claude"] = "claude"
    candidate_id: int
    style_prompt_version: str
    source_set_id: SourceSetId
    model: str
    generated_at: str


SingleWinner = Annotated[
    Union[TranslationWinner, ClaudeWinner], Field(discriminator="kind")
]


class GsvTranslationProvenance(BaseModel):
    """Single-winner translation card."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["translation"] = "translation"
    name: TranslationName
    verse_range: str


class GsvClaudeProvenance(BaseModel):
    """Single-winner Claude candidate."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["claude"] = "claude"
    candidate_id: int
    style_prompt_version: str
    source_set_id: SourceSetId
    model: str
    generated_at: str


class GsvTieEntry(BaseModel):
    """One entry inside an unresolved tie."""

    model_config = ConfigDict(extra="forbid")

    winner: SingleWinner


class GsvTieProvenance(BaseModel):
    """Unresolved tie — multiple entries share rank 1 with no tie-break decision."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tie"] = "tie"
    entries: list[SingleWinner]


class GsvTieBrokenProvenance(BaseModel):
    """A ``tie_break_decisions`` row resolved a prior tie."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tie-broken"] = "tie-broken"
    winner: SingleWinner
    tied_against: list[SingleWinner] = Field(default_factory=list)
    resolved_at: str


class GsvUnrankedProvenance(BaseModel):
    """Red-letter sentence with no saved ranking."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unranked"] = "unranked"


GsvProvenance = Annotated[
    Union[
        GsvTranslationProvenance,
        GsvClaudeProvenance,
        GsvTieProvenance,
        GsvTieBrokenProvenance,
        GsvUnrankedProvenance,
    ],
    Field(discriminator="kind"),
]


class GsvSentence(BaseModel):
    """One row of the GSV — a sentence's resolved English text + provenance."""

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    chapter: int
    ordinal_in_chapter: int
    verse_range: str
    is_red_letter: bool
    text: str = Field(
        description=(
            "Resolved English text for this sentence. Empty string for "
            "kind=='tie' (the renderer treats the tie as unresolved); "
            "for kind=='unranked' carries the BSB fallback so consumers "
            "can show *something* readable while flagging the lack of a "
            "ranking."
        ),
    )
    provenance: GsvProvenance


class GsvCoverage(BaseModel):
    """Coverage stats for one chapter (subset of repository-wide coverage)."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    total_sentences: int
    total_red_letter_sentences: int
    ranked_red_letter_sentences: int
    unresolved_ties: int


class GsvChapterResponse(BaseModel):
    """Returned by ``GET /api/v1/gsv/{chapter}?format=json``."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    coverage: GsvCoverage
    sentences: list[GsvSentence]


class GsvPerChapterCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int
    total_sentences: int
    total_red_letter_sentences: int
    ranked_red_letter_sentences: int
    unresolved_ties: int


class GsvCoverageResponse(BaseModel):
    """Returned by ``GET /api/v1/gsv/coverage``.

    Architect §BuildStaticCoverage semantics: ``total`` is the count of
    red-letter sentences; ``ranked`` is the subset with a saved ranking
    that resolves to a non-tied winner OR a tie-broken winner.
    ``ch5_*`` fields are the chapter-5 restriction surfaced verbatim by
    Designer Flow 6 step 2.
    """

    model_config = ConfigDict(extra="forbid")

    ranked_sentences: int = Field(
        description=(
            "Sum across chapters of red-letter sentences that resolve "
            "to a non-tied winner or a tie-broken winner."
        ),
    )
    total_red_letter_sentences: int = Field(
        description="Sum across chapters of red-letter sentences."
    )
    ch5_ranked: int
    ch5_total: int
    per_chapter: list[GsvPerChapterCoverage]
