"""Source-set resolver + content-addressed snapshot persistence.

Given ``(sentence_id, source_set_id)`` this module returns a
:class:`SourceBundle` (per :class:`SourceSetId`) and writes it
content-addressed into ``source_snapshots`` on first use. Architect §
Resolved source documents pins the canonicalization rule + the dedup
mechanism (``INSERT OR IGNORE`` keyed by SHA-256 of the canonical
``payload_json``).

We deliberately reuse :func:`api.importer.manifest.serialize_manifest_for_hashing`'s
canonicalization rule by writing through ``json.dumps`` with the same
kwargs. The Architect pin states the rule must be byte-identical across
Python versions; reusing the importer's helper would couple the bundle
serialisation to the *manifest* schema, so we duplicate the kwargs but
share the test that asserts byte-identity (``test_canonicalisation_byte_identical``).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from datetime import UTC, datetime

from api.claude.schemas import (
    AdjacentSentence,
    BibInterlinearWordBundle,
    ChapterBundle,
    ChapterNarrativeSentence,
    ChapterRedLetterCandidate,
    ChapterRedLetterSentence,
    ContextWindow,
    SourceBundle,
    SourceSetId,
    StylePrompt,
    VerseTextBundle,
)
from api.errors import DomainError


class IncompatibleSourceSetError(DomainError):
    """Raised when ``style_prompt.compatible_source_sets`` excludes the
    requested ``source_set_id``. Surfaces at run-creation time so the
    runner refuses the run before any Anthropic call.
    """

    status_code = 400
    code = "incompatible_source_set"


class SentenceMissingError(DomainError):
    status_code = 404
    code = "sentence_not_found"


def validate_compatibility(prompt: StylePrompt, source_set_id: str) -> None:
    """Compatibility gate (Slice 8 extends to chapter scope).

    For ``CHAPTER_BUNDLE`` the prompt MUST opt-in via
    ``wants_chapter_input: true``; per-sentence source sets must be in
    ``prompt.compatible_source_sets``. The two paths are exclusive —
    a chapter prompt has empty ``compatible_source_sets``; a per-sentence
    prompt has empty ``compatible_run_scopes`` for chapter scopes.
    """
    if source_set_id == "CHAPTER_BUNDLE":
        if not prompt.wants_chapter_input:
            raise IncompatibleSourceSetError(
                message=(
                    f"prompt {prompt.version!r} does not opt in to "
                    "wants_chapter_input — cannot drive a CHAPTER_BUNDLE run"
                ),
                details={
                    "prompt_version": prompt.version,
                    "source_set_id": source_set_id,
                    "wants_chapter_input": prompt.wants_chapter_input,
                },
            )
        return
    if source_set_id not in prompt.compatible_source_sets:
        raise IncompatibleSourceSetError(
            message=(
                f"prompt {prompt.version!r} does not list {source_set_id!r} in its "
                f"compatible_source_sets ({prompt.compatible_source_sets})"
            ),
            details={
                "prompt_version": prompt.version,
                "source_set_id": source_set_id,
                "compatible_source_sets": list(prompt.compatible_source_sets),
            },
        )


def _verse_range_label(start_verse: int, end_verse: int, chapter: int) -> str:
    if start_verse == end_verse:
        return f"{chapter}:{start_verse}"
    return f"{chapter}:{start_verse}–{end_verse}"


def _fetch_sentence(conn: sqlite3.Connection, sentence_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter, text_sblgnt,
               start_chapter, start_verse, end_chapter, end_verse
        FROM sentences WHERE sentence_id = ?
        """,
        (sentence_id,),
    ).fetchone()
    if row is None:
        raise SentenceMissingError(
            message=f"sentence {sentence_id!r} is not in the current fixture set",
            details={"sentence_id": sentence_id},
        )
    return row


def _red_letter_sentence_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of sentence_ids in the effective red-letter set.

    Mirrors :func:`api.sentences.service._red_letter_sentence_ids` —
    duplicated rather than imported to avoid pulling the read-side
    sentences module into the runner-side dependency tree (cycles), and
    because the v1-slice "no overlays yet" simplification holds here as
    well. The next slice that authors overlays MUST swap both call
    sites to PLAN's canonical effective-set CTE in the same change.
    """
    rows = conn.execute(
        """
        SELECT rls.start_sentence_id AS start_sid,
               rls.end_sentence_id AS end_sid
        FROM red_letter_source_ranges AS rls
        WHERE NOT EXISTS (
            SELECT 1 FROM red_letter_overlays AS rlo
            WHERE rlo.source_range_id = rls.source_range_id
              AND rlo.operation = 'reject'
        )
        """
    ).fetchall()
    if not rows:
        return set()
    sentence_ids: set[str] = set()
    for row in rows:
        bookends = conn.execute(
            "SELECT sentence_id, chapter, ordinal_in_chapter FROM sentences "
            "WHERE sentence_id IN (?, ?)",
            (row["start_sid"], row["end_sid"]),
        ).fetchall()
        if not bookends:
            continue
        if row["start_sid"] == row["end_sid"]:
            sentence_ids.add(row["start_sid"])
            continue
        by_id = {b["sentence_id"]: b for b in bookends}
        if row["start_sid"] not in by_id or row["end_sid"] not in by_id:
            continue
        s_row = by_id[row["start_sid"]]
        e_row = by_id[row["end_sid"]]
        span = conn.execute(
            """
            SELECT sentence_id FROM sentences
            WHERE (chapter > ? OR (chapter = ? AND ordinal_in_chapter >= ?))
              AND (chapter < ? OR (chapter = ? AND ordinal_in_chapter <= ?))
            """,
            (
                s_row["chapter"],
                s_row["chapter"],
                s_row["ordinal_in_chapter"],
                e_row["chapter"],
                e_row["chapter"],
                e_row["ordinal_in_chapter"],
            ),
        ).fetchall()
        sentence_ids.update(r["sentence_id"] for r in span)
    return sentence_ids


def _bsb_text_for_sentence(
    conn: sqlite3.Connection,
    *,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> str | None:
    """Return concatenated BSB English for a sentence's verse span.

    BSB is verse-keyed; a sentence's English approximation is the join
    of its covered verses. Returns ``None`` when no rows match (we
    don't ship empty strings — the agent should see the field absent
    rather than a misleading empty string).
    """
    rows = conn.execute(
        """
        SELECT text FROM english_verses
        WHERE translation = 'BSB'
          AND (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (
            start_chapter,
            start_chapter,
            start_verse,
            end_chapter,
            end_chapter,
            end_verse,
        ),
    ).fetchall()
    if not rows:
        return None
    return " ".join(unicodedata.normalize("NFC", r["text"]) for r in rows).strip() or None


def _fetch_adjacent_sentences(
    conn: sqlite3.Connection,
    *,
    focal_chapter: int,
    focal_ordinal: int,
    direction: str,
    limit: int,
    red_letter_ids: set[str],
) -> list[AdjacentSentence]:
    """Return up to ``limit`` adjacent sentences spanning chapters.

    ``direction`` is ``"before"`` (canonically earlier — smaller
    ``(chapter, ordinal_in_chapter)``) or ``"after"`` (canonically
    later). Result is always in canonical ascending order — for
    ``"before"`` that means the earliest-of-the-N first; for
    ``"after"``, the closest-to-focal first then onwards. Window may
    cross chapter boundaries: a focal at the end of chapter 4 can have
    chapter-5 ``after`` neighbours (the canonical Matt 4:25 → Matt 5:1
    pericope-spans-chapter case). Truncates at the corpus edge — fewer
    than ``limit`` entries near the start or end of Matthew.
    """
    if limit <= 0:
        return []
    if direction == "before":
        rows = conn.execute(
            """
            SELECT sentence_id, chapter, ordinal_in_chapter, text_sblgnt,
                   start_chapter, start_verse, end_chapter, end_verse
            FROM sentences
            WHERE chapter < ? OR (chapter = ? AND ordinal_in_chapter < ?)
            ORDER BY chapter DESC, ordinal_in_chapter DESC
            LIMIT ?
            """,
            (focal_chapter, focal_chapter, focal_ordinal, limit),
        ).fetchall()
        rows = list(reversed(rows))
    elif direction == "after":
        rows = conn.execute(
            """
            SELECT sentence_id, chapter, ordinal_in_chapter, text_sblgnt,
                   start_chapter, start_verse, end_chapter, end_verse
            FROM sentences
            WHERE chapter > ? OR (chapter = ? AND ordinal_in_chapter > ?)
            ORDER BY chapter ASC, ordinal_in_chapter ASC
            LIMIT ?
            """,
            (focal_chapter, focal_chapter, focal_ordinal, limit),
        ).fetchall()
    else:  # pragma: no cover — defensive
        raise ValueError(f"unknown direction: {direction!r}")
    out: list[AdjacentSentence] = []
    for row in rows:
        bsb_text = _bsb_text_for_sentence(
            conn,
            start_chapter=int(row["start_chapter"]),
            start_verse=int(row["start_verse"]),
            end_chapter=int(row["end_chapter"]),
            end_verse=int(row["end_verse"]),
        )
        out.append(
            AdjacentSentence(
                sentence_id=row["sentence_id"],
                chapter=int(row["chapter"]),
                ordinal_in_chapter=int(row["ordinal_in_chapter"]),
                start_verse=int(row["start_verse"]),
                end_verse=int(row["end_verse"]),
                text_sblgnt=unicodedata.normalize("NFC", row["text_sblgnt"]),
                bsb_text=bsb_text,
                is_red_letter=row["sentence_id"] in red_letter_ids,
            )
        )
    return out


def _fetch_byzantine(conn: sqlite3.Connection, row: sqlite3.Row) -> list[VerseTextBundle]:
    rows = conn.execute(
        """
        SELECT chapter, verse, text FROM byzantine_verses
        WHERE (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (
            row["start_chapter"],
            row["start_chapter"],
            row["start_verse"],
            row["end_chapter"],
            row["end_chapter"],
            row["end_verse"],
        ),
    ).fetchall()
    return [
        VerseTextBundle(
            chapter=int(r["chapter"]),
            verse=int(r["verse"]),
            text=unicodedata.normalize("NFC", r["text"]),
        )
        for r in rows
    ]


def _fetch_english(
    conn: sqlite3.Connection, row: sqlite3.Row, translation: str
) -> list[VerseTextBundle]:
    rows = conn.execute(
        """
        SELECT chapter, verse, text FROM english_verses
        WHERE translation = ?
          AND (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (
            translation,
            row["start_chapter"],
            row["start_chapter"],
            row["start_verse"],
            row["end_chapter"],
            row["end_chapter"],
            row["end_verse"],
        ),
    ).fetchall()
    return [
        VerseTextBundle(
            chapter=int(r["chapter"]),
            verse=int(r["verse"]),
            text=unicodedata.normalize("NFC", r["text"]),
        )
        for r in rows
    ]


def _fetch_bib(conn: sqlite3.Connection, row: sqlite3.Row) -> list[BibInterlinearWordBundle]:
    rows = conn.execute(
        """
        SELECT chapter, verse, position, greek_form, strong_id,
               transliteration, english_gloss
        FROM bib_interlinear_words
        WHERE (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse, position
        """,
        (
            row["start_chapter"],
            row["start_chapter"],
            row["start_verse"],
            row["end_chapter"],
            row["end_chapter"],
            row["end_verse"],
        ),
    ).fetchall()
    return [
        BibInterlinearWordBundle(
            chapter=int(r["chapter"]),
            verse=int(r["verse"]),
            position=int(r["position"]),
            greek_form=unicodedata.normalize("NFC", r["greek_form"]),
            strong_id=r["strong_id"],
            transliteration=r["transliteration"],
            english_gloss=r["english_gloss"],
        )
        for r in rows
    ]


def _fetch_fixture_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT manifest_hash FROM fixture_version WHERE id = 1"
    ).fetchone()
    if row is None:
        raise DomainError(
            message="fixture_version row missing — run `make import` before generating",
            details={"hint": "POST /api/v1/admin/reimport, or `make import`"},
        )
    return row["manifest_hash"]


_MAX_CONTEXT_WINDOW = 10


def resolve_source_bundle(
    conn: sqlite3.Connection,
    *,
    sentence_id: str,
    source_set_id: SourceSetId,
    prompt_version: str,
    wants_context_window: bool = False,
    context_window_before: int = 0,
    context_window_after: int = 0,
) -> SourceBundle:
    """Build the canonical :class:`SourceBundle` for one (sentence, source set).

    Caller guarantees the prompt has been compatibility-validated (call
    :func:`validate_compatibility` before this function — the runner does
    that at run-creation time so the rejection is end-to-end fast).

    Slice 3c: when ``wants_context_window=True`` the bundle's
    ``adjacent_context`` is populated with up to ``context_window_before``
    preceding and ``context_window_after`` following sentences. Window
    may span chapter boundaries (Matt 4 narrative → Matt 5 beatitudes).
    When ``wants_context_window=False`` the field stays ``None`` and is
    elided from the canonical JSON, preserving snapshot hashes for the
    simpler prompts that didn't opt in.
    """
    for label, value in (
        ("context_window_before", context_window_before),
        ("context_window_after", context_window_after),
    ):
        if value < 0 or value > _MAX_CONTEXT_WINDOW:
            raise ValueError(
                f"{label} must be in [0, {_MAX_CONTEXT_WINDOW}]; got {value}"
            )
    sentence = _fetch_sentence(conn, sentence_id)
    fixture_version = _fetch_fixture_version(conn)
    sblgnt_text = unicodedata.normalize("NFC", sentence["text_sblgnt"])
    verse_range = _verse_range_label(
        int(sentence["start_verse"]), int(sentence["end_verse"]), int(sentence["chapter"])
    )

    bundle_kwargs: dict[str, object] = {
        "sentence_id": sentence_id,
        "source_set_id": source_set_id,
        "fixture_version": fixture_version,
        "prompt_version": prompt_version,
        "verse_range": verse_range,
    }

    if source_set_id == "SBLGNT_ONLY":
        bundle_kwargs["sblgnt"] = sblgnt_text
    elif source_set_id == "BYZ_ONLY":
        bundle_kwargs["byzantine_verses"] = _fetch_byzantine(conn, sentence)
    elif source_set_id == "BOTH_GREEK":
        bundle_kwargs["sblgnt"] = sblgnt_text
        bundle_kwargs["byzantine_verses"] = _fetch_byzantine(conn, sentence)
    elif source_set_id == "GREEK_PLUS_BIB":
        bundle_kwargs["sblgnt"] = sblgnt_text
        bundle_kwargs["byzantine_verses"] = _fetch_byzantine(conn, sentence)
        bundle_kwargs["bib_interlinear"] = _fetch_bib(conn, sentence)
    elif source_set_id == "GREEK_PLUS_BLB":
        bundle_kwargs["sblgnt"] = sblgnt_text
        bundle_kwargs["byzantine_verses"] = _fetch_byzantine(conn, sentence)
        bundle_kwargs["blb_verses"] = _fetch_english(conn, sentence, "BLB")
    elif source_set_id == "GREEK_PLUS_BSB":
        bundle_kwargs["sblgnt"] = sblgnt_text
        bundle_kwargs["byzantine_verses"] = _fetch_byzantine(conn, sentence)
        bundle_kwargs["bsb_verses"] = _fetch_english(conn, sentence, "BSB")
    elif source_set_id == "ENGLISH_ONLY_BSB":
        bundle_kwargs["bsb_verses"] = _fetch_english(conn, sentence, "BSB")
    else:  # pragma: no cover — typeshed prevents this branch
        raise ValueError(f"unknown source_set_id: {source_set_id}")

    if wants_context_window:
        red_letter_ids = _red_letter_sentence_ids(conn)
        focal_chapter = int(sentence["chapter"])
        focal_ordinal = int(sentence["ordinal_in_chapter"])
        before = _fetch_adjacent_sentences(
            conn,
            focal_chapter=focal_chapter,
            focal_ordinal=focal_ordinal,
            direction="before",
            limit=context_window_before,
            red_letter_ids=red_letter_ids,
        )
        after = _fetch_adjacent_sentences(
            conn,
            focal_chapter=focal_chapter,
            focal_ordinal=focal_ordinal,
            direction="after",
            limit=context_window_after,
            red_letter_ids=red_letter_ids,
        )
        bundle_kwargs["adjacent_context"] = ContextWindow(before=before, after=after)

    return SourceBundle(**bundle_kwargs)


def canonicalize_bundle(bundle: SourceBundle) -> bytes:
    """Render the bundle as canonicalised UTF-8 JSON bytes (Architect §
    canonicalization rule).

    Steps:
      1. ``model_dump`` to a Python dict.
      2. Drop None-valued top-level fields (so ``sblgnt: null`` doesn't
         hash differently from "field absent" — keeps two equivalent
         bundles dedup-friendly).
      3. ``json.dumps`` with ``ensure_ascii=False`` + ``sort_keys=True``
         + ``separators=(",", ":")`` + ``allow_nan=False``.

    All numeric fields in :class:`SourceBundle` are integers; strings are
    NFC-normalised by the resolver. The integers-only rule is enforced
    by ``allow_nan=False`` (which raises on float ``nan``/``inf``); the
    schema doesn't allow other floats.
    """
    payload = bundle.model_dump(exclude_none=True)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def hash_bundle(bundle: SourceBundle) -> tuple[str, bytes]:
    """Return ``(snapshot_hash, payload_bytes)``.

    ``payload_bytes`` is the canonicalised UTF-8 the SHA-256 was computed
    over; we hand it back so the caller can pass it verbatim to the
    snapshot ``INSERT`` (storing the bytes that hashed to the key, not
    a re-canonicalisation that might disagree).
    """
    payload = canonicalize_bundle(bundle)
    h = hashlib.sha256()
    h.update(payload)
    return h.hexdigest(), payload


def upsert_snapshot(
    conn: sqlite3.Connection,
    *,
    bundle: SourceBundle,
    canon_version: str,
) -> str:
    """Compute hash, ``INSERT OR IGNORE`` the snapshot, return the hash.

    Called inside the runner's per-sentence ``BEGIN IMMEDIATE`` transaction
    (PLAN §Generation runner durability). Idempotent under concurrent
    runners producing identical bundles — both attempts dedup on
    ``snapshot_hash`` PK.
    """
    snapshot_hash, payload = hash_bundle(bundle)
    payload_text = payload.decode("utf-8")
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    conn.execute(
        """
        INSERT OR IGNORE INTO source_snapshots (
            snapshot_hash, sentence_id, source_set_id, fixture_version,
            prompt_version, payload_json, byte_size,
            source_snapshot_canon_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_hash,
            bundle.sentence_id,
            bundle.source_set_id,
            bundle.fixture_version,
            bundle.prompt_version,
            payload_text,
            len(payload),
            canon_version,
            created_at,
        ),
    )
    return snapshot_hash


# ---------------------------------------------------------------------------
# Chapter bundle (Slice 8) — chapter-level synthesis input
# ---------------------------------------------------------------------------


class ChapterMissingError(DomainError):
    status_code = 404
    code = "chapter_not_found"


def _bsb_text_for_verse(
    conn: sqlite3.Connection, *, chapter: int, start_verse: int, end_verse: int
) -> str | None:
    """BSB English for a single sentence's verse span (chapter-internal)."""
    rows = conn.execute(
        """
        SELECT text FROM english_verses
        WHERE translation = 'BSB' AND chapter = ?
          AND verse BETWEEN ? AND ?
        ORDER BY verse
        """,
        (chapter, start_verse, end_verse),
    ).fetchall()
    if not rows:
        return None
    joined = " ".join(unicodedata.normalize("NFC", r["text"]) for r in rows).strip()
    return joined or None


def _top_ranked_candidate_ids(conn: sqlite3.Connection, sentence_id: str) -> set[int]:
    """Return the set of claude_candidate_ids tied at rank-1 for a sentence."""
    rows = conn.execute(
        """
        SELECT claude_candidate_id
        FROM ranking_entries
        WHERE sentence_id = ? AND rank = 1 AND candidate_kind = 'claude'
              AND claude_candidate_id IS NOT NULL
        """,
        (sentence_id,),
    ).fetchall()
    return {int(r["claude_candidate_id"]) for r in rows}


def _parse_structured_candidate_text(text: str) -> dict[str, object] | None:
    """Try to parse a candidate's stored ``candidate_text`` as JSON.

    Returns the parsed dict on success, ``None`` if the text is not a
    JSON object (e.g. plain text from a literal/dynamic/plainspoken
    prompt). Used to project structured fields into the chapter bundle.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _build_chapter_red_letter_candidate(
    *,
    candidate_row: sqlite3.Row,
    is_top_ranked: bool,
) -> ChapterRedLetterCandidate:
    parsed = _parse_structured_candidate_text(candidate_row["candidate_text"])
    if parsed is None:
        return ChapterRedLetterCandidate(
            candidate_id=int(candidate_row["candidate_id"]),
            style_prompt_version=str(candidate_row["style_prompt_version"]),
            model=str(candidate_row["model"]),
            is_top_ranked=is_top_ranked,
            english=unicodedata.normalize(
                "NFC", str(candidate_row["candidate_text"]).strip()
            ),
            underlying_hypothesis=None,
            cultural_notes=None,
            intertexts=None,
            audience=None,
            pragmatic_act=None,
            confidence=None,
        )
    english = parsed.get("english")
    intertexts_raw = parsed.get("intertexts")
    intertexts: list[dict[str, object]] | None
    if isinstance(intertexts_raw, list):
        intertexts = [it for it in intertexts_raw if isinstance(it, dict)]
    else:
        intertexts = None

    def _str_or_none(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return unicodedata.normalize("NFC", value)
        return unicodedata.normalize("NFC", str(value))

    return ChapterRedLetterCandidate(
        candidate_id=int(candidate_row["candidate_id"]),
        style_prompt_version=str(candidate_row["style_prompt_version"]),
        model=str(candidate_row["model"]),
        is_top_ranked=is_top_ranked,
        english=_str_or_none(english) or "",
        underlying_hypothesis=_str_or_none(parsed.get("underlying_hypothesis")),
        cultural_notes=_str_or_none(parsed.get("cultural_notes")),
        intertexts=intertexts,
        audience=_str_or_none(parsed.get("audience")),
        pragmatic_act=_str_or_none(parsed.get("pragmatic_act")),
        confidence=_str_or_none(parsed.get("confidence")),
    )


def build_chapter_bundle(
    conn: sqlite3.Connection,
    *,
    chapter: int,
    prompt_version: str,
) -> ChapterBundle:
    """Build the chapter-summary input bundle (Slice 8).

    Returns a :class:`ChapterBundle` containing:
      - ``narrative_text``: every SBLGNT sentence in the chapter, in
        ordinal order, with the BSB English approximation and a
        red-letter flag.
      - ``red_letter_candidates``: per red-letter sentence, the list of
        non-hidden Claude candidates, sorted top-ranked first then by
        ``generated_at`` descending.

    Sentences without any non-hidden candidates are omitted from
    ``red_letter_candidates`` per the prompt fixture's contract.
    Chapters with no red-letter Claude candidates at all return
    ``red_letter_candidates: []``.
    """
    fixture_version = _fetch_fixture_version(conn)
    sentence_rows = conn.execute(
        """
        SELECT sentence_id, ordinal_in_chapter, text_sblgnt,
               start_verse, end_verse
        FROM sentences
        WHERE chapter = ?
        ORDER BY ordinal_in_chapter
        """,
        (chapter,),
    ).fetchall()
    if not sentence_rows:
        raise ChapterMissingError(
            message=f"chapter {chapter!r} has no sentences in current fixtures",
            details={"chapter": chapter},
        )

    red_letter_ids = _red_letter_sentence_ids(conn)

    narrative: list[ChapterNarrativeSentence] = []
    for row in sentence_rows:
        bsb = _bsb_text_for_verse(
            conn,
            chapter=chapter,
            start_verse=int(row["start_verse"]),
            end_verse=int(row["end_verse"]),
        )
        verse_label = (
            f"{chapter}:{int(row['start_verse'])}"
            if int(row["start_verse"]) == int(row["end_verse"])
            else f"{chapter}:{int(row['start_verse'])}–{int(row['end_verse'])}"
        )
        narrative.append(
            ChapterNarrativeSentence(
                sentence_id=row["sentence_id"],
                verse_range=verse_label,
                text_sblgnt=unicodedata.normalize("NFC", row["text_sblgnt"]),
                text_bsb=bsb,
                is_red_letter=row["sentence_id"] in red_letter_ids,
            )
        )

    red_letter_sentences: list[ChapterRedLetterSentence] = []
    for row in sentence_rows:
        sentence_id = row["sentence_id"]
        if sentence_id not in red_letter_ids:
            continue
        candidate_rows = conn.execute(
            """
            SELECT candidate_id, sentence_id, style_prompt_version,
                   source_set_id, model, generated_at, candidate_text
            FROM claude_candidates
            WHERE sentence_id = ? AND hidden_bool = 0
            ORDER BY generated_at DESC, candidate_id DESC
            """,
            (sentence_id,),
        ).fetchall()
        if not candidate_rows:
            continue
        top_ids = _top_ranked_candidate_ids(conn, sentence_id)
        candidates: list[ChapterRedLetterCandidate] = [
            _build_chapter_red_letter_candidate(
                candidate_row=cr,
                is_top_ranked=int(cr["candidate_id"]) in top_ids,
            )
            for cr in candidate_rows
        ]
        # Sort: top-ranked first, then preserve generated_at desc order.
        candidates.sort(key=lambda c: (0 if c.is_top_ranked else 1,))
        verse_label = (
            f"{chapter}:{int(row['start_verse'])}"
            if int(row["start_verse"]) == int(row["end_verse"])
            else f"{chapter}:{int(row['start_verse'])}–{int(row['end_verse'])}"
        )
        red_letter_sentences.append(
            ChapterRedLetterSentence(
                sentence_id=sentence_id,
                verse_range=verse_label,
                candidates=candidates,
            )
        )

    return ChapterBundle(
        chapter=chapter,
        source_set_id="CHAPTER_BUNDLE",
        fixture_version=fixture_version,
        prompt_version=prompt_version,
        narrative_text=narrative,
        red_letter_candidates=red_letter_sentences,
    )


def canonicalize_chapter_bundle(bundle: ChapterBundle) -> bytes:
    """Render the chapter bundle as canonicalised UTF-8 JSON bytes.

    Same canonicalisation rule as :func:`canonicalize_bundle`: NFC
    strings (resolver normalised), sorted keys, no whitespace, no nan/inf.
    Top-level None fields are dropped via ``exclude_none=True``.
    """
    payload = bundle.model_dump(exclude_none=True)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def hash_chapter_bundle(bundle: ChapterBundle) -> tuple[str, bytes]:
    payload = canonicalize_chapter_bundle(bundle)
    h = hashlib.sha256()
    h.update(payload)
    return h.hexdigest(), payload


def upsert_chapter_snapshot(
    conn: sqlite3.Connection,
    *,
    bundle: ChapterBundle,
    canon_version: str,
) -> str:
    """Insert the chapter bundle into ``source_snapshots`` and return its hash.

    ``source_snapshots.sentence_id`` is FK-bound to ``sentences``; for a
    chapter bundle we use the chapter's first sentence_id as the
    representative anchor (the bundle hash is what dedups, not the
    sentence_id field). This keeps the FK satisfied without a schema
    relaxation. The ``source_set_id`` is ``'CHAPTER_BUNDLE'`` so reads
    can distinguish chapter snapshots from per-sentence ones.
    """
    snapshot_hash, payload = hash_chapter_bundle(bundle)
    payload_text = payload.decode("utf-8")
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    anchor = conn.execute(
        """
        SELECT sentence_id FROM sentences
        WHERE chapter = ? ORDER BY ordinal_in_chapter LIMIT 1
        """,
        (bundle.chapter,),
    ).fetchone()
    if anchor is None:
        raise ChapterMissingError(
            message=f"chapter {bundle.chapter!r} has no sentences in current fixtures",
            details={"chapter": bundle.chapter},
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO source_snapshots (
            snapshot_hash, sentence_id, source_set_id, fixture_version,
            prompt_version, payload_json, byte_size,
            source_snapshot_canon_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_hash,
            anchor["sentence_id"],
            bundle.source_set_id,
            bundle.fixture_version,
            bundle.prompt_version,
            payload_text,
            len(payload),
            canon_version,
            created_at,
        ),
    )
    return snapshot_hash
