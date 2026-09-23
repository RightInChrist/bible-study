"""Pydantic schemas for the red-letter overlay editor (Slice 7).

The editor's coordinate system is the SBLGNT word index (Architect §Word
identity) — ``(start_sentence_id, start_word_offset, end_sentence_id,
end_word_offset)``. v1 frontend only wires verse-level mark/unmark, but
the schemas already carry word offsets so the lower-level
split/merge/extend/retract operations don't need a schema bump later.

OCC contract per PLAN §red_letter_overlays: writes target the chain head
(the leaf with no child); the ``If-Match`` header carries the head's
``version``. A successful write inserts a new leaf with
``parent_overlay_id = head.overlay_id`` and ``version = head.version + 1``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


OverlayOperation = Literal["create", "split", "merge", "extend", "retract", "reject"]
OverlayOrigin = Literal["berean", "manual"]
ProvenanceOrigin = Literal["berean", "manual", "rejected"]


class Overlay(BaseModel):
    """One row from ``red_letter_overlays`` in canonical wire form.

    ``rejected=1`` rows have NULL bounds per the table CHECK; we surface
    them as ``None`` rather than empty strings so the UI's "rejected"
    badge keys cleanly off the rejected flag, not a sentinel sentence_id.
    """

    model_config = ConfigDict(extra="forbid")

    overlay_id: int
    parent_overlay_id: int | None = None
    source_range_id: int | None = None
    operation: OverlayOperation
    start_sentence_id: str | None = None
    start_word_offset: int | None = None
    end_sentence_id: str | None = None
    end_word_offset: int | None = None
    rejected: bool
    origin: OverlayOrigin
    created_at: str
    version: int = Field(ge=1)


class OverlayChain(BaseModel):
    """A full chain — the head (leaf with no child) plus the history of
    operations that produced it.

    ``source_range_id`` is non-null when this chain edits a Berean source
    range; null for manual-origin chains created via "Mark as red letter."
    The chain's *origin* (whether the original create came from Berean or
    Gavin) is on the head's ``origin`` field — for source-anchored chains
    the origin matches the source range's origin.
    """

    model_config = ConfigDict(extra="forbid")

    source_range_id: int | None = None
    head: Overlay
    history: list[Overlay] = Field(default_factory=list)


class OverlayChainResponse(BaseModel):
    """Returned by ``GET /api/v1/red-letter/source-range/{id}/chain`` and
    ``GET /api/v1/red-letter/overlay/{id}/chain``."""

    model_config = ConfigDict(extra="forbid")

    chain: OverlayChain


class ChapterOverlaysResponse(BaseModel):
    """Returned by ``GET /api/v1/red-letter/chapter/{N}/overlays``.

    Lists every overlay chain that *might* affect the chapter, including
    chains that are currently rejected (so the editor can offer a Restore
    button). ``effective`` is the ``is_red_letter`` projection: the set
    of sentence_ids in the chapter that the canonical effective-set CTE
    currently flags red.
    """

    model_config = ConfigDict(extra="forbid")

    chapter: int
    chains: list[OverlayChain] = Field(default_factory=list)
    effective_sentence_ids: list[str] = Field(default_factory=list)


class MarkRedLetterRequest(BaseModel):
    """Request body for ``POST /api/v1/red-letter/mark`` — the high-level
    "this verse range is Jesus speaking" action.

    The service derives the SBLGNT sentence IDs that intersect the verse
    range; the resulting overlay is ``operation='create'``,
    ``origin='manual'``, ``parent_overlay_id=NULL``, ``rejected=0``.
    """

    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1, le=28)
    start_verse: int = Field(ge=1)
    end_verse: int = Field(ge=1)


class UnmarkRedLetterRequest(BaseModel):
    """Request body for ``POST /api/v1/red-letter/unmark``.

    Two scopes:
      - ``source_range``: target a Berean (or pre-existing manual)
        source-range ID. If a chain already exists off that source range,
        a new leaf is appended with ``rejected=1``; otherwise a new
        chain head is created with ``rejected=1``.
      - ``manual_overlay``: target an existing overlay chain head
        (``parent_overlay_id`` chain leaf) and append a new leaf with
        ``rejected=1``.

    Round-trip semantics: a chain whose current head is ``rejected=1``
    can be restored by appending another leaf with ``rejected=0`` and
    the prior bounds — see :class:`RestoreRedLetterRequest`.
    """

    model_config = ConfigDict(extra="forbid")

    scope: Literal["source_range", "manual_overlay"]
    target_id: int


class RestoreRedLetterRequest(BaseModel):
    """Request body for ``POST /api/v1/red-letter/restore``.

    Restores a rejected chain by appending a leaf whose ``rejected=0``
    and whose bounds match the latest non-rejected ancestor in the
    chain (or the source range, when the chain is anchored on one).
    """

    model_config = ConfigDict(extra="forbid")

    scope: Literal["source_range", "manual_overlay"]
    target_id: int


class MarkRedLetterResponse(BaseModel):
    """Response from ``POST /api/v1/red-letter/mark``."""

    model_config = ConfigDict(extra="forbid")

    overlay: Overlay
    affected_sentence_ids: list[str] = Field(default_factory=list)


class UnmarkRedLetterResponse(BaseModel):
    """Response from ``POST /api/v1/red-letter/unmark`` and
    ``POST /api/v1/red-letter/restore``."""

    model_config = ConfigDict(extra="forbid")

    overlay: Overlay
    affected_sentence_ids: list[str] = Field(default_factory=list)


class RedLetterProvenance(BaseModel):
    """Per-sentence provenance for the red-letter flag.

    Surfaced on the parallel response (Slice 7 addition) so the UI knows
    what to act on when Gavin clicks "Unmark":
      - ``origin='berean'`` + ``source_range_id``: a Berean range tagged it.
      - ``origin='manual'`` + ``head_overlay_id``: a manual overlay tagged it.
        ``source_range_id`` may be set if the manual overlay edits a Berean
        chain.
      - When a sentence is *not* red-letter, this object is omitted (None
        on the parent response).
    """

    model_config = ConfigDict(extra="forbid")

    origin: ProvenanceOrigin
    source_range_id: int | None = None
    head_overlay_id: int | None = None
