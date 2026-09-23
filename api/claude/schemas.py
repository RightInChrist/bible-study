"""Pydantic models for the Claude generation backend (Slice 3a-redo).

All schemas live here per global rules. The runner/HTTP layer
(``api.runs.schemas``) carries its own request/response shapes; this
module owns the prompt + source-bundle + worktree-result contracts.

Generation mechanism (CLAUDE.md §Generation mechanism): the runner
spawns a Claude Code subagent in a fresh git worktree; this module's
schemas describe the inputs and parsed return shape, not an SDK call.
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


# Slice 8: ``ChapterSourceSetId`` is the single-valued literal used by the
# chapter-summary scope. It's intentionally not part of ``SourceSetId``
# above — that enum is the per-sentence claude_candidates contract and
# locked. The chapter scope's ``source_set_id`` is always ``CHAPTER_BUNDLE``,
# which signals the bundle shape contains a chapter's narrative scaffold
# + per-sentence candidates rather than Greek/translation per-sentence
# rows. The chapter-summaries table has its own CHECK constraint
# enforcing the value.
ChapterSourceSetId = Literal["CHAPTER_BUNDLE"]


# Run-scope kind enum used by prompt front-matter ``compatible_run_scopes``.
# A prompt that declares ``wants_chapter_input: true`` and
# ``compatible_run_scopes: [chapter_summary]`` is the chapter-synthesis
# style; per-sentence prompts default to compatibility with all the
# per-sentence scope kinds.
RunScopeKind = Literal[
    "one_sentence",
    "verse_range",
    "whole_chapter",
    "all_unranked_red_letter",
    "chapter_summary",
]


# ``error_code`` values pinned in PLAN §generation_run_items schema CHECK.
# The 0001_init migration's CHECK enum still includes the SDK-era codes
# (anthropic_api_error, anthropic_refusal, rate_limit) since the column
# is forward-compatible — the worktree runner just doesn't emit them. The
# new codes the worktree mechanism uses are ``timeout``, ``invalid_response``,
# ``disk_full``, and ``internal``. We keep the union here so existing tests
# that assert on the old codes still type-check (the schema evolution is a
# follow-up cleanup once SPEC/PLAN are reworked — see TODO.md).
GenerationErrorCode = Literal[
    "anthropic_api_error",
    "anthropic_refusal",
    "timeout",
    "rate_limit",
    "invalid_response",
    "disk_full",
    "internal",
]


# Per-prompt output format. Drives whether the worktree runner JSON-parses
# the subagent's stdout or stores it as plain text.
PromptOutputFormat = Literal["text", "json"]


class StylePrompt(BaseModel):
    """One versioned style-prompt fixture (Architect §Prompt artifact format).

    Body content (the markdown after the YAML front-matter) is the actual
    text handed to the Claude Code subagent in its ``input.md``.
    ``compatible_source_sets`` is the validation gate the runner checks
    before resolving the source bundle.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Family name, e.g. 'literal'.")
    version: str = Field(description="Versioned identifier, e.g. 'literal-v1'.")
    description: str
    requires_greek: bool
    compatible_source_sets: list[SourceSetId] = Field(default_factory=list)
    body: str = Field(description="Prompt body — the markdown after the front-matter.")
    output_format: PromptOutputFormat = Field(
        default="text",
        description=(
            "How the runner should treat the subagent's stdout. 'text' "
            "stores it as-is; 'json' parses it and validates structured "
            "fields are present."
        ),
    )
    wants_context_window: bool = Field(
        default=False,
        description=(
            "Slice 3c opt-in: when ``true`` the source-bundle resolver "
            "attaches an ``adjacent_context`` block of N-before / N-after "
            "sentences (sized by ``CONTEXT_WINDOW_BEFORE`` / "
            "``CONTEXT_WINDOW_AFTER``). Default false keeps the existing "
            "simpler prompts' bundle hashes stable — they get the "
            "focal-only bundle they always got."
        ),
    )
    wants_chapter_input: bool = Field(
        default=False,
        description=(
            "Slice 8 opt-in: when ``true`` the runner builds a chapter "
            "bundle (narrative_text + red_letter_candidates) instead of "
            "a per-sentence bundle. Mutually exclusive with "
            "``wants_context_window`` — chapter input is its own bundle "
            "shape."
        ),
    )
    compatible_run_scopes: list[RunScopeKind] = Field(
        default_factory=lambda: [
            "one_sentence",
            "verse_range",
            "whole_chapter",
            "all_unranked_red_letter",
        ],
        description=(
            "Which ``RunScope.kind`` values are allowed against this "
            "prompt. Per-sentence prompts default to all per-sentence "
            "scope kinds; the chapter-synthesis prompt overrides this "
            "to ``[chapter_summary]`` only."
        ),
    )


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


class AdjacentSentence(BaseModel):
    """One adjacent sentence carried in the bundle's context window.

    Slice 3c — the cultural-grounding agent reads N sentences before / N
    after the focal sentence so a fragmented pericope (e.g. one
    Antithesis hinge clause) lands inside its surrounding structure
    rather than in isolation.

    Deliberately smaller than the focal sentence:
      - SBLGNT Greek text plus a readable English (BSB) line for
        pericope orientation. No Byzantine, no BIB — the agent has no
        ambiguity about which sentence is the translation target.
      - ``is_red_letter`` lets the agent see whether the surrounding
        material is also Jesus speaking, or is narrator framing.
      - ``chapter`` is included alongside ``ordinal_in_chapter`` because
        the adjacent context may span chapter boundaries (Matt 4:25 →
        Matt 5:1 is the canonical pericope-crossing-chapter example;
        the focal sentence's surrounding pericope does not respect the
        chapter division).
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    chapter: int
    ordinal_in_chapter: int
    start_verse: int
    end_verse: int
    text_sblgnt: str
    bsb_text: str | None = Field(
        default=None,
        description=(
            "Concatenated BSB English for the verse range this adjacent "
            "sentence covers. ``None`` when no BSB rows match (e.g. a "
            "future-translation gap); included to give the agent "
            "pericope orientation without retranslating."
        ),
    )
    is_red_letter: bool


class ContextWindow(BaseModel):
    """The opt-in adjacent-sentence bundle attached to a focal sentence.

    Populated only when the prompt sets ``wants_context_window: true``.
    Both lists are deterministically ordered by ``(chapter,
    ordinal_in_chapter)`` — for ``before`` that means earliest neighbour
    first, closest-to-focal last; for ``after``, closest-to-focal first.
    Lists may span chapter boundaries (a focal sentence near the end of
    chapter 4 has chapter-5 ``after`` neighbours).
    """

    model_config = ConfigDict(extra="forbid")

    before: list[AdjacentSentence] = Field(default_factory=list)
    after: list[AdjacentSentence] = Field(default_factory=list)


class SourceBundle(BaseModel):
    """Canonical bundle handed to the Claude Code subagent.

    Per-field optionality reflects the seven ``SourceSetId`` shapes:
      - ``SBLGNT_ONLY`` → ``sblgnt`` only.
      - ``BYZ_ONLY`` → ``byzantine_verses`` only.
      - ``BOTH_GREEK`` → both.
      - ``GREEK_PLUS_BIB`` → SBLGNT + Byzantine + BIB rows.
      - ``GREEK_PLUS_BLB`` / ``GREEK_PLUS_BSB`` → SBLGNT + Byzantine + the named English.
      - ``ENGLISH_ONLY_BSB`` → BSB only.

    Slice 3c adds an optional ``adjacent_context`` block — opt-in via
    the prompt's ``wants_context_window`` front-matter flag. When the
    flag is false (the default for the simpler prompts), the field is
    ``None`` and ``model_dump(exclude_none=True)`` keeps it out of the
    canonical JSON entirely so legacy snapshot hashes stay stable. When
    the flag is true, ``adjacent_context`` carries N-before / N-after
    neighbours (sized by ``CONTEXT_WINDOW_BEFORE`` / ``CONTEXT_WINDOW_AFTER``)
    and the snapshot hash includes the neighbours — different window
    sizes intentionally produce different snapshots.

    Serialization to ``payload_json`` for ``source_snapshots`` reuses
    the canonical-JSON rule (NFC strings, sorted keys, no floats).
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
    adjacent_context: ContextWindow | None = Field(
        default=None,
        description=(
            "Slice 3c — opt-in adjacent-sentence pericope frame. "
            "``None`` for prompts that did not set ``wants_context_window: "
            "true`` in their front-matter; populated otherwise. The "
            "presence/absence of this field is part of the canonical "
            "JSON, so flipping a prompt's opt-in invalidates its prior "
            "snapshots — that is by design."
        ),
    )


class SentenceMeta(BaseModel):
    """Light context the runner hands the subagent alongside the bundle."""

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    chapter: int
    start_verse: int
    end_verse: int
    verse_range: str


class ChapterNarrativeSentence(BaseModel):
    """One SBLGNT sentence in a chapter's narrative scaffold (Slice 8).

    Used by the chapter-summary bundle to walk the chapter sequentially:
    every sentence is included regardless of red-letter status so the
    agent can see narrative framing alongside speech.
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    verse_range: str
    text_sblgnt: str
    text_bsb: str | None = None
    is_red_letter: bool


class ChapterRedLetterCandidate(BaseModel):
    """One per-sentence cultural-grounding candidate inlined into the
    chapter bundle's ``red_letter_candidates`` section.

    For ``output_format=json`` candidates the structured fields are
    parsed from ``candidate_text`` and surfaced as named fields. For
    text-format candidates only ``english`` is populated; the structured
    fields are ``None`` so the agent can tell the substrate is thinner.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: int
    style_prompt_version: str
    model: str
    is_top_ranked: bool
    english: str
    underlying_hypothesis: str | None = None
    cultural_notes: str | None = None
    intertexts: list[dict[str, object]] | None = None
    audience: str | None = None
    pragmatic_act: str | None = None
    confidence: str | None = None


class ChapterRedLetterSentence(BaseModel):
    """A red-letter sentence's slot in the chapter bundle, with its
    parsed candidates inline (sorted top-ranked-first, then generated_at desc).
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    verse_range: str
    candidates: list[ChapterRedLetterCandidate] = Field(default_factory=list)


class ChapterBundle(BaseModel):
    """Canonical bundle handed to the chapter-summary subagent.

    Mirrors :class:`SourceBundle` for hashing/snapshotting purposes —
    same canonicalisation rule (NFC, sorted keys, no whitespace) — but
    carries a chapter's narrative scaffold + the per-sentence candidates
    instead of per-sentence Greek/translations.

    ``source_set_id`` is always ``"CHAPTER_BUNDLE"`` (a synthetic value
    distinct from the per-sentence enum). ``prompt_version`` is captured
    so a prompt body change invalidates the cached snapshot, and
    ``fixture_version`` keeps the bundle pinned to a specific import.
    """

    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1, le=28)
    source_set_id: ChapterSourceSetId = "CHAPTER_BUNDLE"
    fixture_version: str
    prompt_version: str
    narrative_text: list[ChapterNarrativeSentence] = Field(default_factory=list)
    red_letter_candidates: list[ChapterRedLetterSentence] = Field(default_factory=list)


class ChapterMeta(BaseModel):
    """Light context the runner hands the subagent alongside the chapter bundle."""

    model_config = ConfigDict(extra="forbid")

    chapter: int


class WorktreeResult(BaseModel):
    """The successful return shape from the worktree subagent runner.

    ``raw_output`` is exactly what the subagent printed to stdout (post
    fence-strip). ``parsed_candidate`` is the dict the runner persisted —
    for ``output_format=text`` prompts it's ``{"english": <stdout>}``;
    for ``output_format=json`` it's the parsed JSON.

    ``model`` is the canonical model-identity string the spawner pinned
    via ``claude -p --model <model> --effort <effort>``, formatted as
    ``"{model}+{effort}"`` (e.g. ``"claude-opus-4-7+xhigh"``). This is
    what the candidate row stores for provenance and resolves the
    CLAUDE.md §Open questions item about subagent model capture.

    ``cli_version`` is the diagnostic-only ``claude --version`` reading
    (e.g. ``"claude-code-2.1.126"``). It captures *which CLI build* spawned
    the request — useful for triaging output regressions. It is NOT the
    canonical model identity; ``model`` is.
    """

    model_config = ConfigDict(extra="forbid")

    raw_output: str
    parsed_candidate: dict[str, object]
    model: str
    cli_version: str = Field(
        default="claude-code-unknown",
        description=(
            "Diagnostic-only ``claude --version`` reading "
            "(``claude-code-{version}``). Not the canonical model identity."
        ),
    )
    started_at: str = Field(description="ISO-8601 UTC timestamp.")
    completed_at: str = Field(description="ISO-8601 UTC timestamp.")
    elapsed_seconds: float
