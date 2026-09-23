from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from api.red_letter.schemas import RedLetterProvenance


TranslationName = Literal["BSB", "BLB", "WEB"]


class SentenceListItem(BaseModel):
    """Compact row for ``GET /api/v1/sentences?chapter=N`` navigation."""

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    chapter: int
    ordinal_in_chapter: int
    start_verse: int
    end_verse: int
    starts_at_verse_boundary: bool
    ends_at_verse_boundary: bool
    text_preview: str = Field(
        description="First ~80 chars of the SBLGNT sentence text — for navigation chrome only."
    )
    word_count: int
    is_red_letter: bool = Field(
        description="True iff the sentence intersects the effective red-letter set "
        "(red_letter_source_ranges minus rejecting overlays). Drives Designer Flow 1 "
        "step 4's left-edge red rule."
    )


class SentenceListResponse(BaseModel):  # noqa: D101 — sibling response shape
    model_config = ConfigDict(extra="forbid")

    chapter: int
    sentences: list[SentenceListItem]


class TranslationVerse(BaseModel):
    """One verse from a translation, used inside the parallel response."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    verse: int
    text: str


class ByzantineVerse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int
    verse: int
    text: str


class TranslationColumn(BaseModel):
    """All verses of one English translation that cover the focal sentence's verse range."""

    model_config = ConfigDict(extra="forbid")

    translation: TranslationName
    verses: list[TranslationVerse]


class BibInterlinearWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int
    verse: int
    position: int
    greek_form: str
    strong_id: str
    transliteration: str
    english_gloss: str
    inflected_meaning: str | None = None


class SentenceParallelResponse(BaseModel):
    """Returned by ``GET /api/v1/sentences/{sentence_id}/parallel``.

    Carries the SBLGNT sentence + Byzantine verse text + every English
    translation's verses that cover the sentence's verse range, plus
    ``starts_at_verse_boundary`` / ``ends_at_verse_boundary`` so the UI
    can apply Designer's reference rendering rules R1/R4.
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    chapter: int
    ordinal_in_chapter: int
    start_chapter: int
    start_verse: int
    end_chapter: int
    end_verse: int
    starts_at_verse_boundary: bool
    ends_at_verse_boundary: bool
    word_count: int
    text_sblgnt: str
    byzantine: list[ByzantineVerse]
    english: list[TranslationColumn]
    bib_interlinear: list[BibInterlinearWord]
    is_red_letter: bool = Field(
        description="True iff the sentence intersects the effective red-letter set "
        "(red_letter_source_ranges minus rejecting overlays). Drives Designer Flow 1 "
        "step 4's left-edge red rule."
    )
    red_letter_provenance: RedLetterProvenance | None = Field(
        default=None,
        description="When ``is_red_letter`` is True, identifies the chain head / "
        "source range that flagged the sentence so the unmark UI knows what to "
        "target. Omitted when the sentence is not in the effective set.",
    )
