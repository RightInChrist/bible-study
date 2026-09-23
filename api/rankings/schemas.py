"""Pydantic schemas for the ranking API (Slice 4 — rank-core).

Architect §Contracts pins the shape of the ranking model:

  - ``RankingResponse`` — the full per-sentence ranking surface, returned
    by ``GET /api/v1/sentences/{id}/ranking``.
  - ``RankingWriteRequest`` — request body for ``PUT .../ranking``.
  - ``HideComboRequest`` / ``HiddenComboResponse`` — POST/DELETE the
    ``hidden_combos`` rows that drive the available-candidate filter.
  - ``TieBreakRequest`` / ``TieBreakResponse`` — append-only tie-break
    decision rows.

All writes are OCC-protected via ``If-Match: <version>``; the route
layer raises :class:`api.rankings.errors.StaleRankingVersionError` on
mismatch and the global handler renders the canonical ``ErrorResponse``
shape with ``code='stale_version'`` and the current state in
``details``.

Notes (Hard decision #17): the parent ``rankings`` row's ``version``
covers both the rank-list and the notes string — they save together as
one logical unit. ``RankingWriteRequest.notes`` is therefore part of the
single PUT body.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from api.runs.schemas import CandidateResponse


# Mirrors the ``ranking_entries.candidate_kind`` CHECK enum and Architect
# §Ranking model's discriminated union. ``translation`` covers the open-
# source English translations (BSB / BLB / WEB) and the two Greek editions
# (SBLGNT / Byzantine) — the rank-page treats them all as named-source
# cards. ``claude`` is a Claude-generated candidate referenced by
# ``candidate_id``.
CandidateKind = Literal["translation", "claude"]

TranslationName = Literal["SBLGNT", "BYZ", "BSB", "BLB", "WEB"]


class TranslationCandidateRef(BaseModel):
    """Discriminated-union variant for a translation/Greek-edition card."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["translation"] = "translation"
    name: TranslationName
    verse_range: str = Field(
        description="Human-readable verse range, e.g. '5:3' or '5:3-5:4'."
    )


class ClaudeCandidateRef(BaseModel):
    """Discriminated-union variant for a Claude-generated candidate."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["claude"] = "claude"
    candidate_id: int


CandidateRef = Annotated[
    Union[TranslationCandidateRef, ClaudeCandidateRef],
    Field(discriminator="kind"),
]


class RankingEntry(BaseModel):
    """One ranked card. ``position`` is the order Gavin saved (1..N);
    ``rank`` is the competition rank (positions tied with the one above
    share a rank).

    ``tied_with_above=True`` means "this card is tied with the card at
    position-1 — the two share the rank slot above this." Linear ties
    only in v1 (multi-card tie groups via consecutive ``tied_with_above``
    flags); a richer tie-group shape is a polish slice.
    """

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    rank: int = Field(ge=1)
    tied_with_above: bool = False
    candidate_ref: CandidateRef


class HiddenComboResponse(BaseModel):
    """One row from ``hidden_combos`` — surfaced so the rank UI can
    render an "N hidden" affordance and (in a future polish slice) an
    unhide management screen.
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    style_prompt_version: str
    source_set_id: str
    model: str
    hidden_at: str
    version: int = Field(ge=1)


class TranslationCandidateCard(BaseModel):
    """A rankable open-source translation card (BSB/BLB/WEB) or a Greek
    edition card (SBLGNT/Byzantine)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["translation"] = "translation"
    name: TranslationName
    verse_range: str
    text: str = Field(description="Concatenated text for the sentence's verse range.")


class ClaudeCandidateCard(BaseModel):
    """A Claude-generated candidate as an available rankable card.

    Reuses :class:`api.runs.schemas.CandidateResponse` for the full
    candidate payload so the read shape is consistent with the
    ``GET /sentences/{id}/candidates`` route Designer Flow 4 already
    surfaces.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["claude"] = "claude"
    candidate: CandidateResponse


AvailableCandidate = Annotated[
    Union[TranslationCandidateCard, ClaudeCandidateCard],
    Field(discriminator="kind"),
]


class RankingResponse(BaseModel):
    """Returned by ``GET /api/v1/sentences/{id}/ranking``.

    Design choice: when no ranking row exists, this returns
    ``version=0, entries=[], notes=None`` rather than 404. The 0 sentinel
    drives the first-save flow — the client sends ``If-Match: 0`` and
    the server inserts a fresh ``rankings`` row at ``version=1`` if no
    row existed (or 409s if a row appeared in the meantime).
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    version: int = Field(
        ge=0,
        description="0 when no ranking row exists yet; ≥1 once saved.",
    )
    notes: str | None = None
    entries: list[RankingEntry] = Field(default_factory=list)
    available_candidates: list[AvailableCandidate] = Field(
        default_factory=list,
        description=(
            "Everything rankable for this sentence: SBLGNT + Byzantine + "
            "BSB + BLB + WEB + every Claude candidate not in hidden_combos."
        ),
    )
    hidden_combos: list[HiddenComboResponse] = Field(default_factory=list)


class RankingWriteRequest(BaseModel):
    """Request body for ``PUT /api/v1/sentences/{id}/ranking``.

    The ``If-Match`` header carries the version; the body carries the
    full new state (entries + notes). This is one logical save per
    Hard decision #17.
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[RankingEntry] = Field(default_factory=list)
    notes: str | None = None


class HideComboRequest(BaseModel):
    """Request body for ``POST /api/v1/sentences/{id}/hidden-combos``.

    The ``If-Match`` header carries the ranking row's version (the same
    OCC token that protects rank list + notes — hide/unhide also
    increments it so a hide in one tab cannot silently land on a stale
    rank list in another).
    """

    model_config = ConfigDict(extra="forbid")

    style_prompt_version: str
    source_set_id: str
    model: str


class TieBreakRequest(BaseModel):
    """Request body for ``POST /api/v1/sentences/{id}/tie-break``."""

    model_config = ConfigDict(extra="forbid")

    winner: CandidateRef
    tied_against: list[CandidateRef] = Field(default_factory=list)


class TieBreakResponse(BaseModel):
    """One row from ``tie_break_decisions`` — append-only history."""

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    resolved_at: str
    winner: CandidateRef
    tied_against: list[CandidateRef] = Field(default_factory=list)
    reason: str
    version: int = Field(ge=1)
