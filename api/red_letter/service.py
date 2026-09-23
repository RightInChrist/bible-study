"""Service layer for the red-letter overlay editor (Slice 7).

PLAN §red_letter_overlays pins the effective-set computation as a CTE
that:
  1. Materialises chain heads (leaves: rows with no child).
  2. UNION ALLs source-anchored heads (gated on
     ``COALESCE(h.rejected, 0) = 0``) with manual-origin heads
     (``source_range_id IS NULL AND rejected = 0``).
  3. Falls back to the source range's bounds when no overlay edits the
     chain yet (``COALESCE(h.start_sentence_id, s.start_sentence_id)``).

OCC under WAL (PLAN §OCC):
  - Read the chain head; capture ``head.version``.
  - ``BEGIN IMMEDIATE``.
  - Re-read the head with the same ``parent_overlay_id`` shape.
  - INSERT the new leaf with ``parent_overlay_id = head.overlay_id`` and
    ``version = head.version + 1`` only if the head still has no child
    (``WHERE NOT EXISTS (SELECT 1 FROM red_letter_overlays c WHERE
    c.parent_overlay_id = ?)`` — but SQLite doesn't allow that on INSERT,
    so we use a SELECT-then-INSERT inside ``BEGIN IMMEDIATE`` and verify
    no child appeared by re-reading after the lock).
  - COMMIT.

A new chain (``parent_overlay_id IS NULL``) is created with
``If-Match: 0`` semantics — the caller asserts no head exists; if the
SELECT finds an existing chain, the write 409s.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from api.red_letter.errors import (
    CrossChapterRangeError,
    InvalidVerseRangeError,
    OverlayNotFoundError,
    SourceRangeNotFoundError,
    StaleOverlayVersionError,
)
from api.red_letter.schemas import (
    ChapterOverlaysResponse,
    MarkRedLetterRequest,
    MarkRedLetterResponse,
    Overlay,
    OverlayChain,
    OverlayChainResponse,
    RedLetterProvenance,
    RestoreRedLetterRequest,
    UnmarkRedLetterRequest,
    UnmarkRedLetterResponse,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Canonical effective-set CTE (PLAN §Effective set computation)
# ---------------------------------------------------------------------------


_EFFECTIVE_SET_SQL = """
WITH overlay_heads AS (
  SELECT o.* FROM red_letter_overlays o
  WHERE NOT EXISTS (
    SELECT 1 FROM red_letter_overlays c WHERE c.parent_overlay_id = o.overlay_id
  )
)
SELECT
  COALESCE(h.start_sentence_id, s.start_sentence_id) AS start_sentence_id,
  COALESCE(h.start_word_offset, s.start_word_offset) AS start_word_offset,
  COALESCE(h.end_sentence_id, s.end_sentence_id) AS end_sentence_id,
  COALESCE(h.end_word_offset, s.end_word_offset) AS end_word_offset,
  COALESCE(h.origin, s.origin) AS origin,
  s.source_range_id AS source_range_id,
  h.overlay_id AS head_overlay_id
FROM red_letter_source_ranges s
LEFT JOIN overlay_heads h ON h.source_range_id = s.source_range_id
WHERE COALESCE(h.rejected, 0) = 0
UNION ALL
SELECT
  start_sentence_id,
  start_word_offset,
  end_sentence_id,
  end_word_offset,
  origin,
  NULL AS source_range_id,
  overlay_id AS head_overlay_id
FROM overlay_heads
WHERE source_range_id IS NULL AND rejected = 0
"""


def effective_red_letter_set(conn: sqlite3.Connection) -> set[str]:
    """Return every ``sentence_id`` flagged red by the effective-set CTE.

    Replaces the slice-3c stub that walked ``red_letter_source_ranges``
    directly. Now consumes both Berean source ranges (gated on the
    overlay head's ``rejected`` column) and manual-origin overlay heads.
    """
    rows = conn.execute(_EFFECTIVE_SET_SQL).fetchall()
    if not rows:
        return set()
    sentence_ids: set[str] = set()
    for row in rows:
        start_sid = row["start_sentence_id"]
        end_sid = row["end_sentence_id"]
        if start_sid is None or end_sid is None:
            continue
        sentence_ids.update(_sentence_ids_between(conn, start_sid, end_sid))
    return sentence_ids


def effective_red_letter_provenance(
    conn: sqlite3.Connection,
) -> dict[str, RedLetterProvenance]:
    """Map ``sentence_id -> RedLetterProvenance`` for every red-letter sentence.

    Used by the parallel response so the unmark UI knows what to target.
    When two overlapping ranges flag the same sentence, the manual chain
    wins (it's the more recent edit). Source-only flags use the source
    range's row; chain-edited source ranges report ``head_overlay_id``
    so the UI can surface the chain history.
    """
    rows = conn.execute(_EFFECTIVE_SET_SQL).fetchall()
    out: dict[str, RedLetterProvenance] = {}
    # Iterate manual chains last so they win on overlap.
    ordered = sorted(rows, key=lambda r: 1 if r["source_range_id"] is None else 0)
    for row in ordered:
        start_sid = row["start_sentence_id"]
        end_sid = row["end_sentence_id"]
        if start_sid is None or end_sid is None:
            continue
        origin = str(row["origin"])
        source_range_id = (
            int(row["source_range_id"]) if row["source_range_id"] is not None else None
        )
        head_overlay_id = (
            int(row["head_overlay_id"]) if row["head_overlay_id"] is not None else None
        )
        provenance = RedLetterProvenance(
            origin=origin,  # type: ignore[arg-type]
            source_range_id=source_range_id,
            head_overlay_id=head_overlay_id,
        )
        for sid in _sentence_ids_between(conn, start_sid, end_sid):
            out[sid] = provenance
    return out


def _sentence_ids_between(
    conn: sqlite3.Connection, start_sentence_id: str, end_sentence_id: str
) -> list[str]:
    """Resolve every sentence_id in the chapter-ordinal interval ``[start, end]``.

    Mirrors the slice-3c helper from ``api.sentences.service``. Handles
    multi-sentence ranges within a chapter; cross-chapter ranges work
    too because the importer never authors them but the effective-set
    CTE shape is the same.
    """
    bookends = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter
        FROM sentences
        WHERE sentence_id IN (?, ?)
        """,
        (start_sentence_id, end_sentence_id),
    ).fetchall()
    if start_sentence_id == end_sentence_id:
        return [start_sentence_id] if any(
            r["sentence_id"] == start_sentence_id for r in bookends
        ) else []
    if len(bookends) < 2:
        return []
    by_id = {r["sentence_id"]: r for r in bookends}
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


# ---------------------------------------------------------------------------
# Sentence range derivation from verse range
# ---------------------------------------------------------------------------


def _sentences_intersecting_verses(
    conn: sqlite3.Connection,
    chapter: int,
    start_verse: int,
    end_verse: int,
) -> list[sqlite3.Row]:
    """Return sentences whose verse span intersects ``[start_verse, end_verse]``
    in the given chapter, in canonical (chapter, ordinal_in_chapter) order.

    A sentence intersects the verse range when its ``[start_verse,
    end_verse]`` overlaps the requested ``[start_verse, end_verse]`` —
    SQL: ``sentence.start_verse <= req.end_verse AND sentence.end_verse
    >= req.start_verse`` (within the same chapter; cross-chapter sentences
    are excluded by the chapter filter).
    """
    return conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter, start_verse, end_verse, word_count
        FROM sentences
        WHERE chapter = ?
          AND start_chapter = ?
          AND end_chapter = ?
          AND start_verse <= ?
          AND end_verse >= ?
        ORDER BY ordinal_in_chapter
        """,
        (chapter, chapter, chapter, end_verse, start_verse),
    ).fetchall()


# ---------------------------------------------------------------------------
# Overlay row read/serialise
# ---------------------------------------------------------------------------


def _row_to_overlay(row: sqlite3.Row) -> Overlay:
    return Overlay(
        overlay_id=int(row["overlay_id"]),
        parent_overlay_id=(
            int(row["parent_overlay_id"])
            if row["parent_overlay_id"] is not None
            else None
        ),
        source_range_id=(
            int(row["source_range_id"]) if row["source_range_id"] is not None else None
        ),
        operation=row["operation"],
        start_sentence_id=row["start_sentence_id"],
        start_word_offset=(
            int(row["start_word_offset"])
            if row["start_word_offset"] is not None
            else None
        ),
        end_sentence_id=row["end_sentence_id"],
        end_word_offset=(
            int(row["end_word_offset"]) if row["end_word_offset"] is not None else None
        ),
        rejected=bool(row["rejected"]),
        origin=row["origin"],
        created_at=row["created_at"],
        version=int(row["version"]),
    )


def _fetch_chain_for_source_range(
    conn: sqlite3.Connection, source_range_id: int
) -> list[Overlay]:
    rows = conn.execute(
        """
        SELECT overlay_id, parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version
        FROM red_letter_overlays
        WHERE source_range_id = ?
        ORDER BY overlay_id ASC
        """,
        (source_range_id,),
    ).fetchall()
    return [_row_to_overlay(r) for r in rows]


def _fetch_chain_for_overlay_id(
    conn: sqlite3.Connection, overlay_id: int
) -> list[Overlay]:
    """Walk the chain that contains ``overlay_id`` (back to the root)
    and return every member in insertion order (oldest first).

    Recursive CTE so we don't bounce N round-trips between Python and
    SQLite for a deep chain.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE walk_back(overlay_id, parent_overlay_id) AS (
          SELECT overlay_id, parent_overlay_id FROM red_letter_overlays
          WHERE overlay_id = ?
          UNION ALL
          SELECT p.overlay_id, p.parent_overlay_id
          FROM red_letter_overlays p
          JOIN walk_back w ON w.parent_overlay_id = p.overlay_id
        )
        SELECT o.overlay_id, o.parent_overlay_id, o.source_range_id, o.operation,
               o.start_sentence_id, o.start_word_offset,
               o.end_sentence_id, o.end_word_offset,
               o.rejected, o.origin, o.created_at, o.version
        FROM red_letter_overlays o
        WHERE o.overlay_id IN (SELECT overlay_id FROM walk_back)
        """,
        (overlay_id,),
    ).fetchall()
    if not rows:
        return []
    # Walk forward from the chain root via parent links, then collect any
    # branches off the chain.
    by_id = {int(r["overlay_id"]): r for r in rows}
    # Find the root (NULL parent or parent not in subset — the recursive
    # CTE walks back from `overlay_id`; the row whose parent_overlay_id
    # is NULL or not in by_id is the root).
    root_ids = [
        oid
        for oid, r in by_id.items()
        if r["parent_overlay_id"] is None or int(r["parent_overlay_id"]) not in by_id
    ]
    # Source range may have started a chain — load any other rows on the
    # same source_range_id so the history is complete.
    source_range_id = None
    for r in rows:
        if r["source_range_id"] is not None:
            source_range_id = int(r["source_range_id"])
            break
    if source_range_id is not None:
        return _fetch_chain_for_source_range(conn, source_range_id)
    # Manual chain: load every row whose overlay_id is reachable forward
    # from any root via parent_overlay_id traversal.
    forward_rows = conn.execute(
        """
        WITH RECURSIVE walk_forward(overlay_id) AS (
          SELECT overlay_id FROM red_letter_overlays
          WHERE overlay_id IN ({roots})
          UNION ALL
          SELECT c.overlay_id
          FROM red_letter_overlays c
          JOIN walk_forward w ON c.parent_overlay_id = w.overlay_id
        )
        SELECT overlay_id, parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version
        FROM red_letter_overlays
        WHERE overlay_id IN (SELECT overlay_id FROM walk_forward)
        ORDER BY overlay_id ASC
        """.format(roots=",".join("?" for _ in root_ids)),
        tuple(root_ids),
    ).fetchall()
    return [_row_to_overlay(r) for r in forward_rows]


def _head_of_chain(chain: list[Overlay]) -> Overlay | None:
    """Return the chain leaf (no child within the chain).

    The chain is materialised in insertion order; the leaf is whichever
    overlay no other overlay points at via ``parent_overlay_id``.
    """
    if not chain:
        return None
    parents = {o.parent_overlay_id for o in chain if o.parent_overlay_id is not None}
    leaves = [o for o in chain if o.overlay_id not in parents]
    if not leaves:
        # Cyclic chain — impossible by FK self-reference shape, but be defensive.
        return chain[-1]
    # Multiple leaves can only happen mid-race with a stale read; pick the
    # newest as the head. Real chains under OCC have exactly one leaf.
    leaves.sort(key=lambda o: o.overlay_id, reverse=True)
    return leaves[0]


def _build_chain_response(
    chain_rows: list[Overlay],
    source_range_id: int | None,
) -> OverlayChain | None:
    head = _head_of_chain(chain_rows)
    if head is None:
        return None
    history = [o for o in chain_rows if o.overlay_id != head.overlay_id]
    history.sort(key=lambda o: o.overlay_id)
    return OverlayChain(
        source_range_id=source_range_id,
        head=head,
        history=history,
    )


# ---------------------------------------------------------------------------
# Public read APIs
# ---------------------------------------------------------------------------


def get_chapter_overlays(
    conn: sqlite3.Connection, chapter: int
) -> ChapterOverlaysResponse:
    """List every overlay chain that *might* affect this chapter.

    Chains touch a chapter when any sentence in the chain's bounds (or
    its anchored source range's bounds) lives in the chapter. The
    ``effective_sentence_ids`` field surfaces which sentences are
    currently flagged red — derived from the canonical effective-set CTE.
    """
    # Source ranges intersecting the chapter.
    source_rows = conn.execute(
        """
        SELECT rls.source_range_id
        FROM red_letter_source_ranges rls
        JOIN sentences s_start ON s_start.sentence_id = rls.start_sentence_id
        JOIN sentences s_end ON s_end.sentence_id = rls.end_sentence_id
        WHERE s_start.chapter <= ? AND s_end.chapter >= ?
        ORDER BY rls.source_range_id ASC
        """,
        (chapter, chapter),
    ).fetchall()
    chains: list[OverlayChain] = []
    seen_source_ranges: set[int] = set()
    for srow in source_rows:
        source_range_id = int(srow["source_range_id"])
        seen_source_ranges.add(source_range_id)
        chain_rows = _fetch_chain_for_source_range(conn, source_range_id)
        if chain_rows:
            built = _build_chain_response(chain_rows, source_range_id)
            if built is not None:
                chains.append(built)
        else:
            chains.append(_synthesise_source_only_chain(conn, source_range_id))

    # Manual chains (no source_range_id). To filter by chapter when the
    # head is rejected (NULL bounds), walk each manual chain and look for
    # any row whose bounds reach the chapter — that is the chain's
    # natural anchor regardless of whether the head is currently active
    # or rejected.
    manual_root_rows = conn.execute(
        """
        SELECT overlay_id FROM red_letter_overlays
        WHERE source_range_id IS NULL AND parent_overlay_id IS NULL
        ORDER BY overlay_id ASC
        """
    ).fetchall()
    for mrow in manual_root_rows:
        root_id = int(mrow["overlay_id"])
        chain_rows = _fetch_chain_for_overlay_id(conn, root_id)
        if not chain_rows:
            continue
        if not _chain_touches_chapter(conn, chain_rows, chapter):
            continue
        built = _build_chain_response(chain_rows, None)
        if built is not None:
            chains.append(built)

    effective = sorted(
        sid
        for sid in effective_red_letter_set(conn)
        if conn.execute(
            "SELECT chapter FROM sentences WHERE sentence_id = ?", (sid,)
        ).fetchone()["chapter"]
        == chapter
    )
    return ChapterOverlaysResponse(
        chapter=chapter, chains=chains, effective_sentence_ids=effective
    )


def _chain_touches_chapter(
    conn: sqlite3.Connection, chain_rows: list[Overlay], chapter: int
) -> bool:
    """Return True iff any non-rejected row in ``chain_rows`` has bounds
    that intersect the chapter. A fully-rejected chain (every row has
    NULL bounds) never appears in chapter listings — the bounds were
    cleared at every reject step."""
    for o in chain_rows:
        if o.start_sentence_id is None or o.end_sentence_id is None:
            continue
        rows = conn.execute(
            """
            SELECT chapter FROM sentences
            WHERE sentence_id IN (?, ?)
            """,
            (o.start_sentence_id, o.end_sentence_id),
        ).fetchall()
        chapters = {int(r["chapter"]) for r in rows}
        if any(c == chapter for c in chapters):
            return True
        if any(c < chapter for c in chapters) and any(c > chapter for c in chapters):
            return True
    return False


def _synthesise_source_only_chain(
    conn: sqlite3.Connection, source_range_id: int
) -> OverlayChain:
    """Render a source range with no overlay history as a chain whose
    head is the synthetic source row (``overlay_id=0`` sentinel).

    This keeps the editor UX uniform — every visible range is a "chain"
    even if Gavin hasn't edited it yet. The synthetic head is purely
    presentational; the OCC contract for the first edit uses
    ``parent_overlay_id=NULL`` and ``source_range_id`` to link to the
    source row.
    """
    row = conn.execute(
        """
        SELECT source_range_id, start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset, origin, imported_at
        FROM red_letter_source_ranges
        WHERE source_range_id = ?
        """,
        (source_range_id,),
    ).fetchone()
    head = Overlay(
        overlay_id=0,  # Sentinel — not an actual row; UI keys off source_range_id.
        parent_overlay_id=None,
        source_range_id=int(row["source_range_id"]),
        operation="create",
        start_sentence_id=row["start_sentence_id"],
        start_word_offset=int(row["start_word_offset"]),
        end_sentence_id=row["end_sentence_id"],
        end_word_offset=int(row["end_word_offset"]),
        rejected=False,
        origin=row["origin"],
        created_at=row["imported_at"],
        version=0,  # If-Match=0 indicates "no overlay edits yet."
    )
    return OverlayChain(source_range_id=int(row["source_range_id"]), head=head)


def get_source_range_chain(
    conn: sqlite3.Connection, source_range_id: int
) -> OverlayChainResponse:
    row = conn.execute(
        "SELECT source_range_id FROM red_letter_source_ranges WHERE source_range_id = ?",
        (source_range_id,),
    ).fetchone()
    if row is None:
        raise SourceRangeNotFoundError(source_range_id)
    chain_rows = _fetch_chain_for_source_range(conn, source_range_id)
    if chain_rows:
        built = _build_chain_response(chain_rows, source_range_id)
        if built is None:
            raise SourceRangeNotFoundError(source_range_id)
        return OverlayChainResponse(chain=built)
    return OverlayChainResponse(
        chain=_synthesise_source_only_chain(conn, source_range_id)
    )


def get_overlay_chain(
    conn: sqlite3.Connection, overlay_id: int
) -> OverlayChainResponse:
    row = conn.execute(
        "SELECT overlay_id FROM red_letter_overlays WHERE overlay_id = ?",
        (overlay_id,),
    ).fetchone()
    if row is None:
        raise OverlayNotFoundError(overlay_id)
    chain_rows = _fetch_chain_for_overlay_id(conn, overlay_id)
    if not chain_rows:
        raise OverlayNotFoundError(overlay_id)
    source_range_id = chain_rows[0].source_range_id
    built = _build_chain_response(chain_rows, source_range_id)
    if built is None:
        raise OverlayNotFoundError(overlay_id)
    return OverlayChainResponse(chain=built)


# ---------------------------------------------------------------------------
# Write APIs (OCC inside BEGIN IMMEDIATE)
# ---------------------------------------------------------------------------


def _affected_sentences_for_overlay(
    conn: sqlite3.Connection, overlay: Overlay
) -> list[str]:
    """Return the sentence_ids the overlay's bounds cover (empty when rejected)."""
    if (
        overlay.rejected
        or overlay.start_sentence_id is None
        or overlay.end_sentence_id is None
    ):
        return []
    return _sentence_ids_between(
        conn, overlay.start_sentence_id, overlay.end_sentence_id
    )


def mark_red_letter(
    conn: sqlite3.Connection, request: MarkRedLetterRequest
) -> MarkRedLetterResponse:
    """High-level "mark this verse range red" — creates a new manual chain.

    Designer Flow 3 / Error states pin the cross-chapter rejection; this
    request body carries a single chapter so cross-chapter is impossible
    by shape (rejected at the schema layer with ``chapter: int``).
    Validation here covers ``start_verse > end_verse`` and "no sentence
    intersects" cases.
    """
    if request.start_verse > request.end_verse:
        raise InvalidVerseRangeError(
            "start_verse must be <= end_verse",
            details={
                "chapter": request.chapter,
                "start_verse": request.start_verse,
                "end_verse": request.end_verse,
            },
        )
    sentences = _sentences_intersecting_verses(
        conn, request.chapter, request.start_verse, request.end_verse
    )
    if not sentences:
        raise InvalidVerseRangeError(
            "no SBLGNT sentence intersects the requested verse range",
            details={
                "chapter": request.chapter,
                "start_verse": request.start_verse,
                "end_verse": request.end_verse,
            },
        )
    start_row = sentences[0]
    end_row = sentences[-1]
    if int(start_row["chapter"]) != int(end_row["chapter"]):
        raise CrossChapterRangeError(
            int(start_row["chapter"]), int(end_row["chapter"])
        )
    now = _utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            """
            INSERT INTO red_letter_overlays
              (parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version)
            VALUES (NULL, NULL, 'create', ?, ?, ?, ?, 0, 'manual', ?, 1)
            """,
            (
                start_row["sentence_id"],
                1,
                end_row["sentence_id"],
                int(end_row["word_count"]),
                now,
            ),
        )
        overlay_id = int(cursor.lastrowid)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    overlay_row = conn.execute(
        """
        SELECT overlay_id, parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version
        FROM red_letter_overlays WHERE overlay_id = ?
        """,
        (overlay_id,),
    ).fetchone()
    overlay = _row_to_overlay(overlay_row)
    return MarkRedLetterResponse(
        overlay=overlay,
        affected_sentence_ids=_affected_sentences_for_overlay(conn, overlay),
    )


def _resolve_chain_head(
    conn: sqlite3.Connection, scope: str, target_id: int
) -> tuple[Overlay | None, int | None]:
    """Resolve the current chain head + ``source_range_id`` for an unmark/restore.

    ``scope='source_range'``: target a Berean source range by ID. The
    chain may not have any overlay rows yet; return ``(None, source_range_id)``
    so the caller knows to insert the *first* leaf with
    ``parent_overlay_id=NULL`` and ``source_range_id=target``.

    ``scope='manual_overlay'``: target a manual chain by any overlay_id
    in the chain. Returns the head + ``source_range_id`` (None for
    manual chains).
    """
    if scope == "source_range":
        row = conn.execute(
            "SELECT source_range_id FROM red_letter_source_ranges WHERE source_range_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise SourceRangeNotFoundError(target_id)
        chain_rows = _fetch_chain_for_source_range(conn, target_id)
        head = _head_of_chain(chain_rows)
        return head, target_id
    elif scope == "manual_overlay":
        row = conn.execute(
            "SELECT overlay_id FROM red_letter_overlays WHERE overlay_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise OverlayNotFoundError(target_id)
        chain_rows = _fetch_chain_for_overlay_id(conn, target_id)
        head = _head_of_chain(chain_rows)
        source_range_id = head.source_range_id if head else None
        return head, source_range_id
    else:  # pragma: no cover — schema enum already restricts this.
        raise ValueError(f"unknown scope {scope!r}")


def _bounds_for_source_range(
    conn: sqlite3.Connection, source_range_id: int
) -> tuple[str, int, str, int, str]:
    row = conn.execute(
        """
        SELECT start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset, origin
        FROM red_letter_source_ranges WHERE source_range_id = ?
        """,
        (source_range_id,),
    ).fetchone()
    if row is None:
        raise SourceRangeNotFoundError(source_range_id)
    return (
        row["start_sentence_id"],
        int(row["start_word_offset"]),
        row["end_sentence_id"],
        int(row["end_word_offset"]),
        row["origin"],
    )


def _has_child(conn: sqlite3.Connection, parent_id: int | None) -> bool:
    """Chain-leaf race guard.

    For ``parent_id=None`` (a fresh-chain create on a source range), we
    instead check ``source_range_id`` exclusivity in the caller — there
    is no parent to race against, but two simultaneous "first overlays"
    on the same source range would each insert with
    ``parent_overlay_id=NULL, source_range_id=X``; the caller adds a
    ``WHERE NOT EXISTS`` clause shaped on that.
    """
    if parent_id is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM red_letter_overlays WHERE parent_overlay_id = ? LIMIT 1",
        (parent_id,),
    ).fetchone()
    return row is not None


def unmark_red_letter(
    conn: sqlite3.Connection,
    request: UnmarkRedLetterRequest,
    if_match_version: int,
) -> UnmarkRedLetterResponse:
    """Append a ``rejected=1`` leaf to the chain identified by ``scope`` + ``target_id``.

    OCC contract:
      - If a chain head exists, ``If-Match`` must equal ``head.version``.
      - If no head yet (source range with no overlay edits), ``If-Match=0``.
      - Inside ``BEGIN IMMEDIATE`` the head is re-read; if its
        ``version`` no longer matches OR a child appeared since the
        outer read, raise ``StaleOverlayVersionError`` (the chain-leaf
        race protection that PLAN names explicitly).
    """
    head, source_range_id = _resolve_chain_head(conn, request.scope, request.target_id)
    if head is None:
        # First overlay on a source range — must be source_range scope.
        if request.scope != "source_range":
            raise OverlayNotFoundError(request.target_id)
        if if_match_version != 0:
            raise StaleOverlayVersionError(0, {"reason": "no_chain_head_yet"})
        return _insert_first_reject_for_source(conn, request.target_id)
    # head.version of 0 only happens for synthetic source-only chains
    # we never write — _resolve_chain_head returns the real head row.
    if head.version != if_match_version:
        raise StaleOverlayVersionError(head.version, {"reason": "version_mismatch"})
    return _insert_child_leaf(
        conn,
        parent=head,
        source_range_id=source_range_id,
        rejected=True,
        # Rejected leaves carry NULL bounds per the table CHECK.
        start_sentence_id=None,
        start_word_offset=None,
        end_sentence_id=None,
        end_word_offset=None,
        operation="reject",
    )


def restore_red_letter(
    conn: sqlite3.Connection,
    request: RestoreRedLetterRequest,
    if_match_version: int,
) -> UnmarkRedLetterResponse:
    """Restore a rejected chain by appending a ``rejected=0`` leaf with the
    bounds of the most recent non-rejected ancestor (or the source range
    when the chain anchors on one)."""
    head, source_range_id = _resolve_chain_head(conn, request.scope, request.target_id)
    if head is None:
        # No chain to restore — reject with 404. (A source range with no
        # overlay history is already "active"; nothing to do.)
        raise OverlayNotFoundError(request.target_id)
    if head.version != if_match_version:
        raise StaleOverlayVersionError(head.version, {"reason": "version_mismatch"})
    if not head.rejected:
        # Already active; restore is a no-op error so the UI doesn't
        # silently do nothing on a stale view.
        raise StaleOverlayVersionError(
            head.version, {"reason": "head_not_rejected"}
        )
    bounds = _restore_bounds_from_chain(conn, head, source_range_id)
    return _insert_child_leaf(
        conn,
        parent=head,
        source_range_id=source_range_id,
        rejected=False,
        start_sentence_id=bounds[0],
        start_word_offset=bounds[1],
        end_sentence_id=bounds[2],
        end_word_offset=bounds[3],
        operation="create",
    )


def _restore_bounds_from_chain(
    conn: sqlite3.Connection, head: Overlay, source_range_id: int | None
) -> tuple[str, int, str, int]:
    """Walk back through the chain to find the most recent non-rejected
    bounds; fall back to the source range's bounds when one exists."""
    if head.source_range_id is not None or source_range_id is not None:
        sid = source_range_id or head.source_range_id
        if sid is None:  # pragma: no cover — guarded above.
            raise ValueError("no source range for restore")
        rows = _fetch_chain_for_source_range(conn, sid)
        for o in sorted(rows, key=lambda r: r.overlay_id, reverse=True):
            if not o.rejected and o.start_sentence_id is not None:
                return (
                    o.start_sentence_id,
                    int(o.start_word_offset or 1),
                    o.end_sentence_id or o.start_sentence_id,
                    int(o.end_word_offset or 1),
                )
        bounds = _bounds_for_source_range(conn, sid)
        return bounds[0], bounds[1], bounds[2], bounds[3]
    rows = _fetch_chain_for_overlay_id(conn, head.overlay_id)
    for o in sorted(rows, key=lambda r: r.overlay_id, reverse=True):
        if not o.rejected and o.start_sentence_id is not None:
            return (
                o.start_sentence_id,
                int(o.start_word_offset or 1),
                o.end_sentence_id or o.start_sentence_id,
                int(o.end_word_offset or 1),
            )
    raise OverlayNotFoundError(head.overlay_id)


def _insert_first_reject_for_source(
    conn: sqlite3.Connection, source_range_id: int
) -> UnmarkRedLetterResponse:
    """Insert the first overlay row on a source range as ``rejected=1``.

    Two simultaneous first-rejects must not both succeed — the
    chain-leaf race for an empty chain. Guard: SELECT inside the txn
    asserting no overlay row references this ``source_range_id`` yet.
    """
    now = _utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT 1 FROM red_letter_overlays WHERE source_range_id = ? LIMIT 1",
            (source_range_id,),
        ).fetchone()
        if existing is not None:
            conn.execute("ROLLBACK")
            raise StaleOverlayVersionError(
                1, {"reason": "chain_leaf_race", "source_range_id": source_range_id}
            )
        cursor = conn.execute(
            """
            INSERT INTO red_letter_overlays
              (parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version)
            VALUES (NULL, ?, 'reject', NULL, NULL, NULL, NULL, 1, 'manual', ?, 1)
            """,
            (source_range_id, now),
        )
        overlay_id = int(cursor.lastrowid)
        conn.execute("COMMIT")
    except StaleOverlayVersionError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    overlay = _fetch_overlay(conn, overlay_id)
    return UnmarkRedLetterResponse(
        overlay=overlay, affected_sentence_ids=[]
    )


def _insert_child_leaf(
    conn: sqlite3.Connection,
    *,
    parent: Overlay,
    source_range_id: int | None,
    rejected: bool,
    start_sentence_id: str | None,
    start_word_offset: int | None,
    end_sentence_id: str | None,
    end_word_offset: int | None,
    operation: str,
) -> UnmarkRedLetterResponse:
    """Append a child overlay to ``parent``, with the chain-leaf race guard.

    PLAN's named guard: "no other child has me as parent." Inside the
    txn we re-read children of ``parent.overlay_id``; if any row exists,
    409. The new leaf is inserted with ``version = parent.version + 1``.
    """
    now = _utc_now()
    new_version = parent.version + 1
    conn.execute("BEGIN IMMEDIATE")
    try:
        re_read = conn.execute(
            "SELECT version FROM red_letter_overlays WHERE overlay_id = ?",
            (parent.overlay_id,),
        ).fetchone()
        if re_read is None or int(re_read["version"]) != parent.version:
            conn.execute("ROLLBACK")
            current_version = (
                int(re_read["version"]) if re_read is not None else 0
            )
            raise StaleOverlayVersionError(
                current_version, {"reason": "version_changed_in_txn"}
            )
        if _has_child(conn, parent.overlay_id):
            conn.execute("ROLLBACK")
            raise StaleOverlayVersionError(
                parent.version, {"reason": "chain_leaf_race"}
            )
        cursor = conn.execute(
            """
            INSERT INTO red_letter_overlays
              (parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parent.overlay_id,
                source_range_id,
                operation,
                start_sentence_id,
                start_word_offset,
                end_sentence_id,
                end_word_offset,
                1 if rejected else 0,
                parent.origin,
                now,
                new_version,
            ),
        )
        overlay_id = int(cursor.lastrowid)
        conn.execute("COMMIT")
    except StaleOverlayVersionError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    overlay = _fetch_overlay(conn, overlay_id)
    return UnmarkRedLetterResponse(
        overlay=overlay,
        affected_sentence_ids=_affected_sentences_for_overlay(conn, overlay),
    )


def _fetch_overlay(conn: sqlite3.Connection, overlay_id: int) -> Overlay:
    row = conn.execute(
        """
        SELECT overlay_id, parent_overlay_id, source_range_id, operation,
               start_sentence_id, start_word_offset,
               end_sentence_id, end_word_offset,
               rejected, origin, created_at, version
        FROM red_letter_overlays WHERE overlay_id = ?
        """,
        (overlay_id,),
    ).fetchone()
    if row is None:  # pragma: no cover
        raise OverlayNotFoundError(overlay_id)
    return _row_to_overlay(row)
