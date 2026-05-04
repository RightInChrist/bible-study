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
    BibInterlinearWordBundle,
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


def validate_compatibility(prompt: StylePrompt, source_set_id: SourceSetId) -> None:
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
        SELECT sentence_id, chapter, text_sblgnt,
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


def resolve_source_bundle(
    conn: sqlite3.Connection,
    *,
    sentence_id: str,
    source_set_id: SourceSetId,
    prompt_version: str,
) -> SourceBundle:
    """Build the canonical :class:`SourceBundle` for one (sentence, source set).

    Caller guarantees the prompt has been compatibility-validated (call
    :func:`validate_compatibility` before this function — the runner does
    that at run-creation time so the rejection is end-to-end fast).
    """
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
