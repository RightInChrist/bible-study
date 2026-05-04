"""Idempotent fixture importer.

Architect §Importer + Data §Idempotent import: reads ``fixtures/manifest.json``,
verifies every file's SHA-256, and atomic-or-nothing TRUNCATE+INSERT inside
``BEGIN IMMEDIATE``. Overlay tables (red_letter_overlays, claude_candidates,
rankings, ranking_entries, tie_break_decisions, hidden_combos, source_snapshots,
generation_runs, generation_run_items) are preserved across imports per
Hard decision #7.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from api.claude.prompts import body_sha256, load_prompt
from api.db.connection import open_connection
from api.importer.manifest import (
    Manifest,
    find_file,
    load_manifest,
    manifest_disk_hash,
    verify_manifest_files,
)
from api.importer.segmenter import (
    SegmentedSentence,
    parse_verse_lines,
    segment_sblgnt,
    tokenize_greek,
)
from api.logging import get_logger


_logger = get_logger("bible_study.importer")


@dataclass(frozen=True)
class ImportError_:
    """Structured importer error.

    Named ``ImportError_`` (trailing underscore) to avoid clashing with the
    Python builtin ``ImportError``; callers raise via :class:`FixtureImportError`
    below if a structured exception is needed.
    """

    code: str
    message: str
    details: dict[str, object] | None = None


class FixtureImportError(Exception):
    """Raised when the importer aborts (hash mismatch, missing file, …)."""

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class ImportResult:
    fixture_version: str
    files_imported: int
    sentences_built: int
    words_built: int
    red_letter_source_ranges: int
    took_ms: int


def _deterministic_imported_at(manifest: Manifest) -> str:
    """The ``imported_at`` timestamp recorded on every fixture-derived row.

    Data §Idempotent import requires byte-identical row content across
    re-imports of the same manifest. We therefore derive the timestamp
    from the manifest itself (latest ``retrieved_at`` across files) rather
    than clock-time at import.
    """
    candidates = [f.retrieved_at for f in manifest.files]
    return max(candidates) if candidates else "1970-01-01T00:00:00Z"


def _build_sentence_id(chapter: int, ordinal_in_chapter: int) -> str:
    """Architect §Sentence identity: ``mat-${chapter}-${ordinal}``."""
    return f"mat-{chapter}-{ordinal_in_chapter}"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _build_sentence_rows(
    sentences: list[SegmentedSentence],
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """Build the tuples for ``sentences`` and ``words`` INSERT statements."""
    sentence_rows: list[tuple[object, ...]] = []
    word_rows: list[tuple[object, ...]] = []
    for s in sentences:
        sentence_id = _build_sentence_id(s.chapter, s.ordinal_in_chapter)
        text_bytes = s.text.encode("utf-8")
        words = tokenize_greek(s.text)
        if not words:
            raise FixtureImportError(
                code="empty_sentence",
                message=f"sentence {sentence_id} contained zero Greek words",
                details={"text": s.text},
            )
        sentence_rows.append(
            (
                sentence_id,
                s.chapter,
                s.ordinal_in_chapter,
                s.text,
                s.start_chapter,
                s.start_verse,
                s.end_chapter,
                s.end_verse,
                1 if s.starts_at_verse_boundary else 0,
                1 if s.ends_at_verse_boundary else 0,
                len(words),
                len(text_bytes),
            )
        )
        for ordinal, word in enumerate(words, start=1):
            word_rows.append(
                (
                    sentence_id,
                    ordinal,
                    word.text,
                    None,  # strong_id populated by aligner — out of scope for this slice
                    word.byte_start,
                    word.byte_end,
                )
            )
    return sentence_rows, word_rows


def _build_byzantine_rows(content: str) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for chunk in parse_verse_lines(content):
        text_bytes = chunk.text.encode("utf-8")
        rows.append((chunk.chapter, chunk.verse, chunk.text, len(text_bytes)))
    return rows


def _build_english_rows(translation: str, content: str) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for chunk in parse_verse_lines(content):
        text_bytes = chunk.text.encode("utf-8")
        rows.append((translation, chunk.chapter, chunk.verse, chunk.text, len(text_bytes)))
    return rows


def _build_bib_rows(content: str) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    reader = csv.DictReader(content.splitlines(), delimiter="\t")
    for line in reader:
        verse_field = line["verse"].strip()
        # Verses arrive as "5:3"; we store chapter+verse as ints.
        chapter_str, verse_str = verse_field.split(":")
        chapter = int(chapter_str)
        verse = int(verse_str)
        position = int(line["position"])
        greek = unicodedata.normalize("NFC", line["greek"].strip())
        rows.append(
            (
                chapter,
                verse,
                position,
                greek,
                line["strong"].strip(),
                line["transliteration"].strip(),
                line["gloss"].strip(),
                line.get("inflected", "").strip() or None,
            )
        )
    return rows


def _build_red_letter_source_rows(
    content: str,
    sentence_index: dict[tuple[int, int], list[tuple[str, int]]],
    imported_at: str,
) -> list[tuple[object, ...]]:
    """Convert the red-letter-range fixture (verse-range form) into source-range rows.

    ``sentence_index`` maps ``(chapter, verse) -> [(sentence_id, word_count), ...]``,
    which lets us pick the first sentence intersecting the start verse and the
    last sentence intersecting the end verse. Word offsets default to 1 (start)
    and the sentence's word_count (end) — i.e. whole sentences. Mid-sentence
    word offsets are not yet authored in this stub fixture.
    """
    payload = json.loads(content)
    rows: list[tuple[object, ...]] = []
    for entry in payload.get("ranges", []):
        start_chapter = int(entry["start_chapter"])
        start_verse = int(entry["start_verse"])
        end_chapter = int(entry["end_chapter"])
        end_verse = int(entry["end_verse"])
        origin = entry.get("origin", "manual")
        note = entry.get("note")
        # Pick the first sentence whose start verse covers (start_chapter, start_verse).
        start_sids = sentence_index.get((start_chapter, start_verse))
        end_sids = sentence_index.get((end_chapter, end_verse))
        if not start_sids:
            raise FixtureImportError(
                code="red_letter_anchor_missing",
                message=f"red-letter range start {start_chapter}:{start_verse} has no sentence",
                details={"entry": entry},
            )
        if not end_sids:
            raise FixtureImportError(
                code="red_letter_anchor_missing",
                message=f"red-letter range end {end_chapter}:{end_verse} has no sentence",
                details={"entry": entry},
            )
        start_sid, _ = start_sids[0]
        end_sid, end_word_count = end_sids[-1]
        rows.append(
            (
                start_sid,
                1,
                end_sid,
                end_word_count,
                origin,
                note,
                imported_at,
            )
        )
    return rows


def _execute_many(conn: sqlite3.Connection, sql: str, rows: list[tuple[object, ...]]) -> None:
    if not rows:
        return
    conn.executemany(sql, rows)


def _build_sentences(
    sblgnt_text: str,
) -> tuple[
    list[SegmentedSentence],
    list[tuple[object, ...]],
    list[tuple[object, ...]],
    dict[tuple[int, int], list[tuple[str, int]]],
]:
    """Segment SBLGNT and pack the per-row tuples for INSERT.

    Pulled out as a module-level function so tests can monkeypatch it to
    raise mid-import (proves the transaction actually rolls back; see
    ``test_partial_import_rolls_back``).
    """
    sentences = segment_sblgnt(sblgnt_text)
    if not sentences:
        raise FixtureImportError(
            code="empty_sblgnt",
            message="SBLGNT fixture produced zero sentences",
        )
    sentence_rows, word_rows = _build_sentence_rows(sentences)
    sentence_index: dict[tuple[int, int], list[tuple[str, int]]] = {}
    for sentence_row, sentence in zip(sentence_rows, sentences, strict=True):
        sentence_id = sentence_row[0]
        word_count = sentence_row[10]
        for verse_num in range(sentence.start_verse, sentence.end_verse + 1):
            sentence_index.setdefault((sentence.start_chapter, verse_num), []).append(
                (str(sentence_id), int(word_count))  # type: ignore[arg-type]
            )
    return sentences, sentence_rows, word_rows, sentence_index


def import_fixtures(
    project_root: Path,
    *,
    db_path: Path | None = None,
) -> ImportResult:
    """Run the full import. Atomic-or-nothing — on any error, rolls back.

    The transaction is ``BEGIN IMMEDIATE`` so concurrent writers
    serialize cleanly under WAL (Reliability §OCC).
    """
    started = time.monotonic()
    manifest_path = project_root / "fixtures" / "manifest.json"
    manifest = load_manifest(manifest_path)

    manifest_hash_for_logs: str | None = None
    try:
        manifest_hash_for_logs = manifest_disk_hash(manifest_path)
    except Exception:  # noqa: BLE001 — logging fallback only
        manifest_hash_for_logs = None
    _logger.info(
        "import.started",
        extra={
            "target_fixture_version": manifest_hash_for_logs,
            "manifest_version": manifest.version,
        },
    )

    hash_errors = verify_manifest_files(manifest, project_root)
    if hash_errors:
        _logger.error(
            "import.aborted",
            extra={
                "error_code": "fixture_hash_mismatch",
                "errors": hash_errors,
                "stage": "preflight",
            },
        )
        raise FixtureImportError(
            code="fixture_hash_mismatch",
            message="fixture file hash mismatch",
            details={"errors": hash_errors},
        )

    manifest_hash = manifest_disk_hash(manifest_path)
    imported_at = _deterministic_imported_at(manifest)

    # Read every fixture into memory before opening the transaction so a
    # mid-import I/O error can't half-commit. This is the in-memory parallel
    # to "atomic-or-nothing": if reading fails we throw before BEGIN IMMEDIATE.
    sblgnt_path = project_root / find_file(manifest, "sblgnt-matthew").path
    byzantine_path = project_root / find_file(manifest, "byzantine-matthew").path
    bsb_path = project_root / find_file(manifest, "bsb-matthew").path
    blb_path = project_root / find_file(manifest, "blb-matthew").path
    web_path = project_root / find_file(manifest, "web-matthew").path
    bib_path = project_root / find_file(manifest, "bib-matthew").path
    red_letters_path = project_root / find_file(manifest, "red-letters-matthew").path

    sblgnt_text = _read_text(sblgnt_path)
    byzantine_text = _read_text(byzantine_path)
    bsb_text = _read_text(bsb_path)
    blb_text = _read_text(blb_path)
    web_text = _read_text(web_path)
    bib_text = _read_text(bib_path)
    red_letters_text = _read_text(red_letters_path)

    _sentences, sentence_rows, word_rows, sentence_index = _build_sentences(sblgnt_text)
    byzantine_rows = _build_byzantine_rows(byzantine_text)
    bsb_rows = _build_english_rows("BSB", bsb_text)
    blb_rows = _build_english_rows("BLB", blb_text)
    web_rows = _build_english_rows("WEB", web_text)
    bib_rows = _build_bib_rows(bib_text)

    red_letter_rows = _build_red_letter_source_rows(
        red_letters_text, sentence_index, imported_at
    )

    # Style prompts: read each fixture file, parse the front-matter, and
    # build the row tuples. The manifest already validated the SHA-256.
    prompt_rows: list[tuple[object, ...]] = []
    for prompt_entry in manifest.prompts:
        prompt_path = project_root / prompt_entry.path
        prompt = load_prompt(prompt_path)
        prompt_rows.append(
            (
                prompt.version,
                prompt.name,
                prompt.description,
                1 if prompt.requires_greek else 0,
                json.dumps(
                    list(prompt.compatible_source_sets),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                prompt_entry.path,
                body_sha256(prompt.body),
                imported_at,
            )
        )

    conn = open_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Truncate fixture-derived tables in child-first order; overlay
            # tables are NOT touched (Hard decision #7).
            #
            # ``style_prompts`` is fixture-derived (the body lives on disk
            # under ``fixtures/prompts/*.md``; the table is metadata). We
            # only DELETE rows whose ``prompt_version`` is not referenced
            # by any ``claude_candidates`` row — referenced prompts are
            # left in place so a candidate FK never dangles. The new
            # bodies and SHA-256s are then upserted on top.
            for table in (
                "words",
                "red_letter_source_ranges",
                "bib_interlinear_words",
                "english_verses",
                "byzantine_verses",
                "sentences",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                """
                DELETE FROM style_prompts
                WHERE prompt_version NOT IN (
                    SELECT DISTINCT style_prompt_version FROM claude_candidates
                )
                """
            )

            _execute_many(
                conn,
                """
                INSERT INTO sentences (
                    sentence_id, chapter, ordinal_in_chapter, text_sblgnt,
                    start_chapter, start_verse, end_chapter, end_verse,
                    starts_at_verse_boundary, ends_at_verse_boundary,
                    word_count, byte_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sentence_rows,
            )
            _execute_many(
                conn,
                """
                INSERT INTO words (
                    sentence_id, ordinal, text, strong_id, byte_start, byte_end
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                word_rows,
            )
            _execute_many(
                conn,
                """
                INSERT INTO byzantine_verses (chapter, verse, text, byte_size)
                VALUES (?, ?, ?, ?)
                """,
                byzantine_rows,
            )
            for english_rows in (bsb_rows, blb_rows, web_rows):
                _execute_many(
                    conn,
                    """
                    INSERT INTO english_verses (translation, chapter, verse, text, byte_size)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    english_rows,
                )
            _execute_many(
                conn,
                """
                INSERT INTO bib_interlinear_words (
                    chapter, verse, position, greek_form, strong_id,
                    transliteration, english_gloss, inflected_meaning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                bib_rows,
            )
            _execute_many(
                conn,
                """
                INSERT INTO red_letter_source_ranges (
                    start_sentence_id, start_word_offset, end_sentence_id,
                    end_word_offset, origin, note, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                red_letter_rows,
            )
            # Style prompts: ``INSERT OR REPLACE`` on the PK so a re-import
            # of the same manifest is a byte-identical no-op (preserves
            # the idempotency invariant), and a manifest update with a
            # new body SHA refreshes the row in place.
            for prompt_row in prompt_rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO style_prompts (
                        prompt_version, name, description, requires_greek,
                        compatible_source_sets, body_path, body_sha256, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    prompt_row,
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO fixture_version (
                    id, manifest_hash, manifest_version,
                    segmenter_rule, word_tokenizer_rule, byzantine_mode,
                    source_snapshot_canon_version,
                    last_imported_at, files_imported, sentences_built,
                    words_built, red_letter_source_ranges
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest_hash,
                    manifest.version,
                    manifest.segmenter.rule,
                    manifest.word_tokenizer,
                    manifest.byzantine_mode,
                    manifest.source_snapshot_canon_version,
                    imported_at,
                    len(manifest.files),
                    len(sentence_rows),
                    len(word_rows),
                    len(red_letter_rows),
                ),
            )
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            error_code = (
                exc.code if isinstance(exc, FixtureImportError) else "internal"
            )
            _logger.error(
                "import.aborted",
                extra={
                    "error_code": error_code,
                    "error_message": str(exc),
                    "stage": "transaction",
                },
            )
            raise
    finally:
        conn.close()

    took_ms = int((time.monotonic() - started) * 1000)
    _logger.info(
        "import.committed",
        extra={
            "fixture_version": manifest_hash,
            "files_imported": len(manifest.files),
            "sentences": len(sentence_rows),
            "words": len(word_rows),
            "byzantine_verses": len(byzantine_rows),
            "english_verses": len(bsb_rows) + len(blb_rows) + len(web_rows),
            "bib_interlinear_words": len(bib_rows),
            "red_letter_source_ranges": len(red_letter_rows),
            "elapsed_ms": took_ms,
        },
    )
    return ImportResult(
        fixture_version=manifest_hash,
        files_imported=len(manifest.files),
        sentences_built=len(sentence_rows),
        words_built=len(word_rows),
        red_letter_source_ranges=len(red_letter_rows),
        took_ms=took_ms,
    )


def fixture_status(
    project_root: Path,
    *,
    db_path: Path | None = None,
) -> tuple[str, str | None, str | None]:
    """Return (disk_manifest_hash, db_fixture_version, last_imported_at).

    The status pill compares the first two; ``last_imported_at`` is null when
    no import has run yet.
    """
    manifest_path = project_root / "fixtures" / "manifest.json"
    disk_hash = manifest_disk_hash(manifest_path)
    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT manifest_hash, last_imported_at FROM fixture_version WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return disk_hash, None, None
    return disk_hash, row["manifest_hash"], row["last_imported_at"]
