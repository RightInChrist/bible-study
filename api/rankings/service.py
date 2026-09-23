"""Service layer for the ranking surface (Slice 4 — rank-core).

PLAN §OCC under WAL: every write is one ``BEGIN IMMEDIATE`` transaction
that re-checks ``version`` inside the txn. The atomic ranking save is:

    BEGIN IMMEDIATE;
    SELECT version FROM rankings WHERE sentence_id=?;
    -- if absent and If-Match=0 → INSERT new ranking row at version=1
    -- elif present and version=If-Match → DELETE+INSERT entries; UPDATE notes; UPDATE version=version+1
    -- else → ROLLBACK; raise StaleRankingVersionError

The ``hidden_combos`` and ``tie_break_decisions`` writes share the same
``rankings.version`` token (Architect §Contracts pinned uniformity).
Hide/unhide therefore also updates ``rankings.version`` — a hide in one
tab cannot silently land on a stale rank list in another.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from api.errors import SentenceNotFoundError
from api.rankings.errors import (
    HiddenComboNotFoundError,
    StaleRankingVersionError,
)
from api.rankings.schemas import (
    AvailableCandidate,
    CandidateRef,
    ClaudeCandidateCard,
    ClaudeCandidateRef,
    HiddenComboResponse,
    RankingEntry,
    RankingResponse,
    RankingWriteRequest,
    TieBreakRequest,
    TieBreakResponse,
    TranslationCandidateCard,
    TranslationCandidateRef,
)
from api.runs.schemas import CandidateResponse


_TRANSLATION_NAMES: tuple[str, ...] = ("BSB", "BLB", "WEB")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verse_range_label(start_chapter: int, start_verse: int, end_chapter: int, end_verse: int) -> str:
    if start_chapter == end_chapter and start_verse == end_verse:
        return f"{start_chapter}:{start_verse}"
    if start_chapter == end_chapter:
        return f"{start_chapter}:{start_verse}-{end_verse}"
    return f"{start_chapter}:{start_verse}-{end_chapter}:{end_verse}"


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
        # Match the existing fixture-version detail used by /parallel.
        fv_row = conn.execute(
            "SELECT manifest_hash FROM fixture_version WHERE id = 1"
        ).fetchone()
        fv = fv_row["manifest_hash"] if fv_row is not None else None
        raise SentenceNotFoundError(sentence_id, fv)
    return row


def _entries_for_sentence(
    conn: sqlite3.Connection, sentence_id: str
) -> list[RankingEntry]:
    rows = conn.execute(
        """
        SELECT position, rank, tied_with_above,
               candidate_kind, translation_name, translation_verse_range,
               claude_candidate_id
        FROM ranking_entries
        WHERE sentence_id = ?
        ORDER BY position ASC
        """,
        (sentence_id,),
    ).fetchall()
    out: list[RankingEntry] = []
    for row in rows:
        if row["candidate_kind"] == "translation":
            ref: CandidateRef = TranslationCandidateRef(
                name=row["translation_name"],
                verse_range=row["translation_verse_range"] or "",
            )
        else:
            ref = ClaudeCandidateRef(candidate_id=int(row["claude_candidate_id"]))
        out.append(
            RankingEntry(
                position=int(row["position"]),
                rank=int(row["rank"]),
                tied_with_above=bool(row["tied_with_above"]),
                candidate_ref=ref,
            )
        )
    return out


def _fetch_ranking_row(
    conn: sqlite3.Connection, sentence_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT sentence_id, notes, status, version, updated_at "
        "FROM rankings WHERE sentence_id = ?",
        (sentence_id,),
    ).fetchone()


def _fetch_hidden_combos(
    conn: sqlite3.Connection, sentence_id: str
) -> list[HiddenComboResponse]:
    rows = conn.execute(
        """
        SELECT sentence_id, style_prompt_version, source_set_id, model,
               hidden_at, version
        FROM hidden_combos
        WHERE sentence_id = ?
        ORDER BY hidden_at DESC
        """,
        (sentence_id,),
    ).fetchall()
    return [
        HiddenComboResponse(
            sentence_id=row["sentence_id"],
            style_prompt_version=row["style_prompt_version"],
            source_set_id=row["source_set_id"],
            model=row["model"],
            hidden_at=row["hidden_at"],
            version=int(row["version"]),
        )
        for row in rows
    ]


def _hidden_combo_keys(
    conn: sqlite3.Connection, sentence_id: str
) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        """
        SELECT style_prompt_version, source_set_id, model
        FROM hidden_combos WHERE sentence_id = ?
        """,
        (sentence_id,),
    ).fetchall()
    return {
        (r["style_prompt_version"], r["source_set_id"], r["model"]) for r in rows
    }


def _build_available_candidates(
    conn: sqlite3.Connection, sentence_row: sqlite3.Row
) -> list[AvailableCandidate]:
    """Compose the rankable cards for one sentence.

    Order: SBLGNT, Byzantine, BSB, BLB, WEB, then Claude candidates by
    ``generated_at DESC, candidate_id DESC`` (matches the read-side
    candidates list elsewhere).

    Excludes Claude candidates whose ``(style_prompt_version, source_set_id,
    model)`` tuple appears in ``hidden_combos`` — that is the suppression
    predicate Architect §Contracts pinned for hide/unhide.
    """
    sentence_id = sentence_row["sentence_id"]
    start_chapter = int(sentence_row["start_chapter"])
    start_verse = int(sentence_row["start_verse"])
    end_chapter = int(sentence_row["end_chapter"])
    end_verse = int(sentence_row["end_verse"])
    verse_range = _verse_range_label(
        start_chapter, start_verse, end_chapter, end_verse
    )
    out: list[AvailableCandidate] = []

    # SBLGNT — text from the sentence itself.
    out.append(
        TranslationCandidateCard(
            name="SBLGNT",
            verse_range=verse_range,
            text=str(sentence_row["text_sblgnt"]),
        )
    )

    # Byzantine — concatenate verse text for the sentence's verse range.
    byz_rows = conn.execute(
        """
        SELECT chapter, verse, text FROM byzantine_verses
        WHERE (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (start_chapter, start_chapter, start_verse, end_chapter, end_chapter, end_verse),
    ).fetchall()
    byz_text = " ".join(r["text"] for r in byz_rows)
    out.append(
        TranslationCandidateCard(
            name="BYZ",
            verse_range=verse_range,
            text=byz_text,
        )
    )

    for translation in _TRANSLATION_NAMES:
        eng_rows = conn.execute(
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
        eng_text = " ".join(r["text"] for r in eng_rows)
        out.append(
            TranslationCandidateCard(
                name=translation,  # type: ignore[arg-type]
                verse_range=verse_range,
                text=eng_text,
            )
        )

    hidden_keys = _hidden_combo_keys(conn, sentence_id)
    cand_rows = conn.execute(
        """
        SELECT candidate_id, sentence_id, style_prompt_version, source_set_id,
               model, generated_at, candidate_text, source_snapshot_hash,
               hidden_bool, latency_ms
        FROM claude_candidates
        WHERE sentence_id=?
        ORDER BY generated_at DESC, candidate_id DESC
        """,
        (sentence_id,),
    ).fetchall()
    for row in cand_rows:
        key = (row["style_prompt_version"], row["source_set_id"], row["model"])
        if key in hidden_keys:
            continue
        if int(row["hidden_bool"]) == 1:
            continue
        candidate = CandidateResponse(
            candidate_id=int(row["candidate_id"]),
            sentence_id=row["sentence_id"],
            style_prompt_version=row["style_prompt_version"],
            source_set_id=row["source_set_id"],
            model=row["model"],
            generated_at=row["generated_at"],
            candidate_text=row["candidate_text"],
            source_snapshot_hash=row["source_snapshot_hash"],
            hidden_bool=bool(row["hidden_bool"]),
            latency_ms=int(row["latency_ms"]) if row["latency_ms"] is not None else None,
        )
        out.append(ClaudeCandidateCard(candidate=candidate))

    return out


def get_ranking(conn: sqlite3.Connection, sentence_id: str) -> RankingResponse:
    sentence_row = _fetch_sentence(conn, sentence_id)
    ranking_row = _fetch_ranking_row(conn, sentence_id)
    if ranking_row is None:
        version = 0
        notes: str | None = None
        entries: list[RankingEntry] = []
    else:
        version = int(ranking_row["version"])
        notes = ranking_row["notes"]
        entries = _entries_for_sentence(conn, sentence_id)
    available = _build_available_candidates(conn, sentence_row)
    hidden = _fetch_hidden_combos(conn, sentence_id)
    return RankingResponse(
        sentence_id=sentence_id,
        version=version,
        notes=notes,
        entries=entries,
        available_candidates=available,
        hidden_combos=hidden,
    )


def _serialize_current_state(
    conn: sqlite3.Connection, sentence_id: str, ranking_row: sqlite3.Row | None
) -> dict[str, Any]:
    """Build the conflict-modal payload for a stale_version error.

    Carries the server's current entries + notes + version so the client
    doesn't need a second round-trip to render the conflict modal.
    """
    if ranking_row is None:
        return {
            "current_entries": [],
            "current_notes": None,
        }
    entries = _entries_for_sentence(conn, sentence_id)
    return {
        "current_entries": [e.model_dump() for e in entries],
        "current_notes": ranking_row["notes"],
    }


def _compute_status(entries: list[RankingEntry]) -> str:
    """PLAN §rankings table 'status' derivation.

    v1 simplification: any saved row with at least one rank-1 entry is
    'ranked'; anything else is 'partial'. 'skipped' is reserved for an
    explicit Skip action which is a polish-slice affordance.
    """
    if any(e.rank == 1 for e in entries):
        return "ranked"
    return "partial"


def _normalize_ranks(entries: list[RankingEntry]) -> list[RankingEntry]:
    """Recompute ``rank`` from ``position`` + ``tied_with_above``.

    Position 1 is always rank 1; subsequent positions either inherit the
    rank above (when ``tied_with_above=True``) or take a fresh rank
    equal to ``position``. This matches Designer Flow 5's competition-
    style ranking ("1, 1, 3" rather than "1, 1, 2").

    The client may submit rank values; the server recomputes to stay the
    source of truth. ``position`` from the client wire is taken as
    authoritative — we re-index into 1..N to be defensive.
    """
    out: list[RankingEntry] = []
    last_rank = 0
    for idx, entry in enumerate(entries, start=1):
        if idx == 1:
            rank = 1
        elif entry.tied_with_above:
            rank = last_rank
        else:
            rank = idx
        out.append(
            RankingEntry(
                position=idx,
                rank=rank,
                tied_with_above=entry.tied_with_above and idx > 1,
                candidate_ref=entry.candidate_ref,
            )
        )
        last_rank = rank
    return out


def put_ranking(
    conn: sqlite3.Connection,
    sentence_id: str,
    if_match_version: int,
    request: RankingWriteRequest,
) -> RankingResponse:
    """Atomic ranking save (Hard decision #17).

    One ``BEGIN IMMEDIATE`` transaction:

      1. SELECT version FROM rankings WHERE sentence_id=?
         (treated as 0 when no row exists)
      2. If version != if_match_version → ROLLBACK + raise.
      3. DELETE+INSERT entries; UPSERT rankings row with version+1 and
         the new notes value.

    Note: ``conn.isolation_level`` is None (autocommit) per
    :mod:`api.db.connection`. We open the txn explicitly with
    ``BEGIN IMMEDIATE`` and either ``COMMIT`` or ``ROLLBACK`` ourselves.
    """
    _fetch_sentence(conn, sentence_id)  # 404 if unknown
    entries = _normalize_ranks(list(request.entries))
    notes = request.notes
    now = _utc_now()

    conn.execute("BEGIN IMMEDIATE")
    try:
        ranking_row = _fetch_ranking_row(conn, sentence_id)
        current_version = int(ranking_row["version"]) if ranking_row else 0
        if current_version != if_match_version:
            current_state = _serialize_current_state(conn, sentence_id, ranking_row)
            conn.execute("ROLLBACK")
            raise StaleRankingVersionError(current_version, current_state)

        new_version = current_version + 1
        status = _compute_status(entries)

        if ranking_row is None:
            conn.execute(
                """
                INSERT INTO rankings (sentence_id, notes, status, version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sentence_id, notes, status, new_version, now),
            )
        else:
            conn.execute(
                """
                UPDATE rankings SET notes=?, status=?, version=?, updated_at=?
                WHERE sentence_id=? AND version=?
                """,
                (notes, status, new_version, now, sentence_id, current_version),
            )
            conn.execute(
                "DELETE FROM ranking_entries WHERE sentence_id=?",
                (sentence_id,),
            )

        for entry in entries:
            ref = entry.candidate_ref
            if isinstance(ref, TranslationCandidateRef):
                conn.execute(
                    """
                    INSERT INTO ranking_entries
                      (sentence_id, position, rank, tied_with_above,
                       candidate_kind, translation_name, translation_verse_range,
                       claude_candidate_id)
                    VALUES (?, ?, ?, ?, 'translation', ?, ?, NULL)
                    """,
                    (
                        sentence_id,
                        entry.position,
                        entry.rank,
                        1 if entry.tied_with_above else 0,
                        ref.name,
                        ref.verse_range,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO ranking_entries
                      (sentence_id, position, rank, tied_with_above,
                       candidate_kind, translation_name, translation_verse_range,
                       claude_candidate_id)
                    VALUES (?, ?, ?, ?, 'claude', NULL, NULL, ?)
                    """,
                    (
                        sentence_id,
                        entry.position,
                        entry.rank,
                        1 if entry.tied_with_above else 0,
                        ref.candidate_id,
                    ),
                )
        conn.execute("COMMIT")
    except StaleRankingVersionError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_ranking(conn, sentence_id)


def hide_combo(
    conn: sqlite3.Connection,
    sentence_id: str,
    if_match_version: int,
    style_prompt_version: str,
    source_set_id: str,
    model: str,
) -> RankingResponse:
    """Insert a ``hidden_combos`` row + bump the rankings ``version``.

    Architect §Contracts: hide/unhide are OCC-protected writes that
    share the rankings row's ``version`` token.
    """
    _fetch_sentence(conn, sentence_id)
    now = _utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        ranking_row = _fetch_ranking_row(conn, sentence_id)
        current_version = int(ranking_row["version"]) if ranking_row else 0
        if current_version != if_match_version:
            current_state = _serialize_current_state(conn, sentence_id, ranking_row)
            conn.execute("ROLLBACK")
            raise StaleRankingVersionError(current_version, current_state)

        new_version = current_version + 1

        existing = conn.execute(
            """
            SELECT version FROM hidden_combos
            WHERE sentence_id=? AND style_prompt_version=? AND source_set_id=? AND model=?
            """,
            (sentence_id, style_prompt_version, source_set_id, model),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO hidden_combos
                  (sentence_id, style_prompt_version, source_set_id, model,
                   hidden_at, version)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sentence_id,
                    style_prompt_version,
                    source_set_id,
                    model,
                    now,
                    new_version,
                ),
            )
        else:
            # Idempotent re-hide: refresh hidden_at, advance version.
            conn.execute(
                """
                UPDATE hidden_combos
                SET hidden_at=?, version=?
                WHERE sentence_id=? AND style_prompt_version=? AND source_set_id=? AND model=?
                """,
                (
                    now,
                    new_version,
                    sentence_id,
                    style_prompt_version,
                    source_set_id,
                    model,
                ),
            )

        if ranking_row is None:
            conn.execute(
                """
                INSERT INTO rankings (sentence_id, notes, status, version, updated_at)
                VALUES (?, NULL, 'partial', ?, ?)
                """,
                (sentence_id, new_version, now),
            )
        else:
            conn.execute(
                """
                UPDATE rankings SET version=?, updated_at=?
                WHERE sentence_id=? AND version=?
                """,
                (new_version, now, sentence_id, current_version),
            )
        conn.execute("COMMIT")
    except StaleRankingVersionError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_ranking(conn, sentence_id)


def unhide_combo(
    conn: sqlite3.Connection,
    sentence_id: str,
    if_match_version: int,
    combo_id: str,
) -> RankingResponse:
    """Delete a ``hidden_combos`` row + bump the rankings ``version``.

    ``combo_id`` is the URL-safe composite ``"{style}|{source_set}|{model}"``
    — sufficient because the four-tuple is the natural key.
    """
    _fetch_sentence(conn, sentence_id)
    parts = combo_id.split("|")
    if len(parts) != 3:
        raise HiddenComboNotFoundError(combo_id)
    style_prompt_version, source_set_id, model = parts
    now = _utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        ranking_row = _fetch_ranking_row(conn, sentence_id)
        current_version = int(ranking_row["version"]) if ranking_row else 0
        if current_version != if_match_version:
            current_state = _serialize_current_state(conn, sentence_id, ranking_row)
            conn.execute("ROLLBACK")
            raise StaleRankingVersionError(current_version, current_state)

        existing = conn.execute(
            """
            SELECT 1 FROM hidden_combos
            WHERE sentence_id=? AND style_prompt_version=? AND source_set_id=? AND model=?
            """,
            (sentence_id, style_prompt_version, source_set_id, model),
        ).fetchone()
        if existing is None:
            conn.execute("ROLLBACK")
            raise HiddenComboNotFoundError(combo_id)

        conn.execute(
            """
            DELETE FROM hidden_combos
            WHERE sentence_id=? AND style_prompt_version=? AND source_set_id=? AND model=?
            """,
            (sentence_id, style_prompt_version, source_set_id, model),
        )
        new_version = current_version + 1
        if ranking_row is None:
            conn.execute(
                """
                INSERT INTO rankings (sentence_id, notes, status, version, updated_at)
                VALUES (?, NULL, 'partial', ?, ?)
                """,
                (sentence_id, new_version, now),
            )
        else:
            conn.execute(
                """
                UPDATE rankings SET version=?, updated_at=?
                WHERE sentence_id=? AND version=?
                """,
                (new_version, now, sentence_id, current_version),
            )
        conn.execute("COMMIT")
    except (StaleRankingVersionError, HiddenComboNotFoundError):
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_ranking(conn, sentence_id)


def post_tie_break(
    conn: sqlite3.Connection,
    sentence_id: str,
    if_match_version: int,
    request: TieBreakRequest,
) -> TieBreakResponse:
    """Append a ``tie_break_decisions`` row.

    Append-only history — ``PRIMARY KEY (sentence_id, resolved_at)``
    means each commit is a fresh row. The ``If-Match`` header is the
    rankings row's current ``version``; it bumps on commit so two open
    tie-break modals can't both succeed silently.
    """
    _fetch_sentence(conn, sentence_id)
    import json

    now = _utc_now()
    winner = request.winner
    tied_against_payload = [t.model_dump() for t in request.tied_against]
    tied_against_json = json.dumps(
        tied_against_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        ranking_row = _fetch_ranking_row(conn, sentence_id)
        current_version = int(ranking_row["version"]) if ranking_row else 0
        if current_version != if_match_version:
            current_state = _serialize_current_state(conn, sentence_id, ranking_row)
            conn.execute("ROLLBACK")
            raise StaleRankingVersionError(current_version, current_state)
        new_version = current_version + 1

        if isinstance(winner, TranslationCandidateRef):
            conn.execute(
                """
                INSERT INTO tie_break_decisions
                  (sentence_id, resolved_at, winner_kind,
                   winner_translation_name, winner_translation_verse_range,
                   winner_claude_candidate_id, tied_against_json,
                   reason, version)
                VALUES (?, ?, 'translation', ?, ?, NULL, ?, 'tie-broken-by-Gavin', ?)
                """,
                (
                    sentence_id,
                    now,
                    winner.name,
                    winner.verse_range,
                    tied_against_json,
                    new_version,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO tie_break_decisions
                  (sentence_id, resolved_at, winner_kind,
                   winner_translation_name, winner_translation_verse_range,
                   winner_claude_candidate_id, tied_against_json,
                   reason, version)
                VALUES (?, ?, 'claude', NULL, NULL, ?, ?, 'tie-broken-by-Gavin', ?)
                """,
                (
                    sentence_id,
                    now,
                    winner.candidate_id,
                    tied_against_json,
                    new_version,
                ),
            )

        if ranking_row is None:
            conn.execute(
                """
                INSERT INTO rankings (sentence_id, notes, status, version, updated_at)
                VALUES (?, NULL, 'partial', ?, ?)
                """,
                (sentence_id, new_version, now),
            )
        else:
            conn.execute(
                """
                UPDATE rankings SET version=?, updated_at=?
                WHERE sentence_id=? AND version=?
                """,
                (new_version, now, sentence_id, current_version),
            )
        conn.execute("COMMIT")
    except StaleRankingVersionError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return TieBreakResponse(
        sentence_id=sentence_id,
        resolved_at=now,
        winner=winner,
        tied_against=request.tied_against,
        reason="tie-broken-by-Gavin",
        version=new_version,
    )
