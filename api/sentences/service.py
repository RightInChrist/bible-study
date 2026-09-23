"""Service layer for sentence reads.

Architect §Components: routes are thin adapters over service functions.
The static-site builder calls these directly (Hard decision #6).

Slice 7 — switched ``_red_letter_sentence_ids`` to PLAN's canonical
effective-set CTE (PLAN.md §Effective red-letter set query, ~line 632).
The CTE materialises chain heads, gates source-anchored chains on
``rejected=1`` from the head, and UNION ALLs manual-origin chain heads.
The legacy slice-3c stub that walked ``red_letter_source_ranges`` only
is gone. The function is now a thin re-export of
:func:`api.red_letter.service.effective_red_letter_set` so there is one
canonical implementation.
"""
from __future__ import annotations

import sqlite3

from api.errors import SentenceNotFoundError
from api.red_letter.service import (
    effective_red_letter_provenance,
    effective_red_letter_set,
)
from api.sentences.schemas import (
    BibInterlinearWord,
    ByzantineVerse,
    SentenceListItem,
    SentenceListResponse,
    SentenceParallelResponse,
    TranslationColumn,
    TranslationVerse,
)


_TRANSLATIONS: tuple[str, ...] = ("BSB", "BLB", "WEB")


def list_sentences_in_chapter(conn: sqlite3.Connection, chapter: int) -> SentenceListResponse:
    rows = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter, start_verse, end_verse,
               starts_at_verse_boundary, ends_at_verse_boundary, text_sblgnt, word_count
        FROM sentences
        WHERE chapter = ?
        ORDER BY ordinal_in_chapter
        """,
        (chapter,),
    ).fetchall()
    red_letter_ids = _red_letter_sentence_ids(conn)
    items = [
        SentenceListItem(
            sentence_id=row["sentence_id"],
            chapter=row["chapter"],
            ordinal_in_chapter=row["ordinal_in_chapter"],
            start_verse=row["start_verse"],
            end_verse=row["end_verse"],
            starts_at_verse_boundary=bool(row["starts_at_verse_boundary"]),
            ends_at_verse_boundary=bool(row["ends_at_verse_boundary"]),
            text_preview=_preview(row["text_sblgnt"]),
            word_count=row["word_count"],
            is_red_letter=row["sentence_id"] in red_letter_ids,
        )
        for row in rows
    ]
    return SentenceListResponse(chapter=chapter, sentences=items)


def get_sentence_parallel(
    conn: sqlite3.Connection, sentence_id: str
) -> SentenceParallelResponse:
    sentence = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter, text_sblgnt,
               start_chapter, start_verse, end_chapter, end_verse,
               starts_at_verse_boundary, ends_at_verse_boundary, word_count
        FROM sentences WHERE sentence_id = ?
        """,
        (sentence_id,),
    ).fetchone()
    if sentence is None:
        raise SentenceNotFoundError(sentence_id, _current_fixture_version(conn))

    start_chapter = sentence["start_chapter"]
    start_verse = sentence["start_verse"]
    end_chapter = sentence["end_chapter"]
    end_verse = sentence["end_verse"]

    byzantine = _fetch_byzantine_for_range(
        conn, start_chapter, start_verse, end_chapter, end_verse
    )
    english_columns: list[TranslationColumn] = []
    for translation in _TRANSLATIONS:
        verses = _fetch_english_for_range(
            conn, translation, start_chapter, start_verse, end_chapter, end_verse
        )
        english_columns.append(TranslationColumn(translation=translation, verses=verses))

    bib = _fetch_bib_for_range(conn, start_chapter, start_verse, end_chapter, end_verse)

    provenance_map = effective_red_letter_provenance(conn)
    sid = sentence["sentence_id"]
    red_letter_provenance = provenance_map.get(sid)
    return SentenceParallelResponse(
        sentence_id=sentence["sentence_id"],
        chapter=sentence["chapter"],
        ordinal_in_chapter=sentence["ordinal_in_chapter"],
        start_chapter=start_chapter,
        start_verse=start_verse,
        end_chapter=end_chapter,
        end_verse=end_verse,
        starts_at_verse_boundary=bool(sentence["starts_at_verse_boundary"]),
        ends_at_verse_boundary=bool(sentence["ends_at_verse_boundary"]),
        word_count=sentence["word_count"],
        text_sblgnt=sentence["text_sblgnt"],
        byzantine=byzantine,
        english=english_columns,
        bib_interlinear=bib,
        is_red_letter=red_letter_provenance is not None,
        red_letter_provenance=red_letter_provenance,
    )


def _preview(text: str, max_chars: int = 80) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _current_fixture_version(conn: sqlite3.Connection) -> str | None:
    """Return the ``manifest_hash`` recorded by the last successful import.

    ``None`` when the importer has not run yet — Designer's "Sentence not
    found" empty state still wants to show *something* in the
    ``Current fixture: …`` field, but UIs render an explicit dash for null.
    """
    row = conn.execute(
        "SELECT manifest_hash FROM fixture_version WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return row["manifest_hash"]


def _red_letter_sentence_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of sentence_ids in the **effective** red-letter set.

    Slice 7: thin re-export of :func:`api.red_letter.service.effective_red_letter_set`
    so there is one canonical implementation of PLAN's effective-set CTE.
    Existing callers (the static-site builder, the GSV coverage query, the
    rank-queue "is red-letter" filter) keep working without rewiring.
    """
    return effective_red_letter_set(conn)


def _sentence_ids_between(
    conn: sqlite3.Connection, start_sentence_id: str, end_sentence_id: str
) -> list[str]:
    """Return every ``sentence_id`` from ``start`` to ``end`` inclusive in
    canonical (chapter, ordinal_in_chapter) order.

    Single source range may span multiple sentences (Designer Flow 3 allows
    multi-sentence ranges within a chapter). We resolve the sentences by
    looking up both endpoints' (chapter, ordinal_in_chapter) and selecting
    every sentence inside that interval.
    """
    bookends = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter
        FROM sentences
        WHERE sentence_id IN (?, ?)
        """,
        (start_sentence_id, end_sentence_id),
    ).fetchall()
    if len(bookends) < 2 and start_sentence_id != end_sentence_id:
        # One endpoint missing — should be impossible given FK constraints.
        return []
    by_id = {row["sentence_id"]: row for row in bookends}
    if start_sentence_id == end_sentence_id:
        return [start_sentence_id] if start_sentence_id in by_id else []
    start_row = by_id[start_sentence_id]
    end_row = by_id[end_sentence_id]
    rows = conn.execute(
        """
        SELECT sentence_id
        FROM sentences
        WHERE (chapter > ? OR (chapter = ? AND ordinal_in_chapter >= ?))
          AND (chapter < ? OR (chapter = ? AND ordinal_in_chapter <= ?))
        ORDER BY chapter, ordinal_in_chapter
        """,
        (
            start_row["chapter"],
            start_row["chapter"],
            start_row["ordinal_in_chapter"],
            end_row["chapter"],
            end_row["chapter"],
            end_row["ordinal_in_chapter"],
        ),
    ).fetchall()
    return [row["sentence_id"] for row in rows]


def _fetch_byzantine_for_range(
    conn: sqlite3.Connection,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> list[ByzantineVerse]:
    rows = conn.execute(
        """
        SELECT chapter, verse, text FROM byzantine_verses
        WHERE (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (start_chapter, start_chapter, start_verse, end_chapter, end_chapter, end_verse),
    ).fetchall()
    return [
        ByzantineVerse(chapter=row["chapter"], verse=row["verse"], text=row["text"])
        for row in rows
    ]


def _fetch_english_for_range(
    conn: sqlite3.Connection,
    translation: str,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> list[TranslationVerse]:
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
            start_chapter,
            start_chapter,
            start_verse,
            end_chapter,
            end_chapter,
            end_verse,
        ),
    ).fetchall()
    return [
        TranslationVerse(chapter=row["chapter"], verse=row["verse"], text=row["text"])
        for row in rows
    ]


def _fetch_bib_for_range(
    conn: sqlite3.Connection,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> list[BibInterlinearWord]:
    rows = conn.execute(
        """
        SELECT chapter, verse, position, greek_form, strong_id,
               transliteration, english_gloss, inflected_meaning
        FROM bib_interlinear_words
        WHERE (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse, position
        """,
        (start_chapter, start_chapter, start_verse, end_chapter, end_chapter, end_verse),
    ).fetchall()
    return [
        BibInterlinearWord(
            chapter=row["chapter"],
            verse=row["verse"],
            position=row["position"],
            greek_form=row["greek_form"],
            strong_id=row["strong_id"],
            transliteration=row["transliteration"],
            english_gloss=row["english_gloss"],
            inflected_meaning=row["inflected_meaning"],
        )
        for row in rows
    ]
