"""Service layer for GSV compilation + export (Slice 5).

The compilation flow per chapter:

  1. Iterate ``sentences`` ordered by ``(chapter, ordinal_in_chapter)``
     — NOT by ``sentence_id`` lexicographically (Data §Effective red
     letter set: ordinal is the canonical sort key).
  2. For each sentence determine its provenance:
     * Red-letter sentence with at least one rank-1 entry:
         - one entry → translation/claude single-winner.
         - multiple → check ``tie_break_decisions`` for the latest row
           whose tied set matches; emit ``tie-broken`` if so, else
           ``tie``.
     * Red-letter sentence with no ranking row (or ranking row with no
       entries) → ``unranked``.
     * Non-red-letter sentence → ``translation`` provenance pointing at
       BSB (default English readable surface).
  3. Resolve text per provenance kind:
     * ``translation`` — concatenate verses from ``english_verses`` /
       ``byzantine_verses`` / SBLGNT.
     * ``claude`` — fetch the candidate row; for JSON-format candidates
       (those whose ``candidate_text`` parses as a dict with an
       ``english`` key), extract that field. For text-format candidates,
       use ``candidate_text`` verbatim.
     * ``tie`` — text=''; the rendered Markdown surfaces ``[TIE: see
       provenance]`` placeholder; plain-text refuses (the route layer
       raises ``UnresolvedTiesError``).
     * ``tie-broken`` — same as the inner winner.
     * ``unranked`` — fall back to BSB so muted placeholders still carry
       a readable English line.

The renderers (``render_plain_text``, ``render_markdown``) wrap the
structured form. JSON consumers use ``compile_chapter_gsv`` directly.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from api.gsv.errors import GsvChapterOutOfRangeError, UnresolvedTiesError
from api.gsv.schemas import (
    ClaudeWinner,
    GsvChapterResponse,
    GsvClaudeProvenance,
    GsvCoverage,
    GsvCoverageResponse,
    GsvPerChapterCoverage,
    GsvProvenance,
    GsvSentence,
    GsvTieBrokenProvenance,
    GsvTieProvenance,
    GsvTranslationProvenance,
    GsvUnrankedProvenance,
    SingleWinner,
    TranslationWinner,
)


def _verse_range_label(
    start_chapter: int, start_verse: int, end_chapter: int, end_verse: int
) -> str:
    if start_chapter == end_chapter and start_verse == end_verse:
        return f"{start_chapter}:{start_verse}"
    if start_chapter == end_chapter:
        return f"{start_chapter}:{start_verse}-{end_verse}"
    return f"{start_chapter}:{start_verse}-{end_chapter}:{end_verse}"


def _red_letter_sentence_ids(conn: sqlite3.Connection) -> set[str]:
    """v1 effective red-letter set — same shape used by ``api.sentences.service``.

    The next overlay-aware slice will swap this for PLAN's canonical
    effective-set CTE; Slice 5 follows the existing pattern so the GSV
    coverage matches what the parallel reader paints as red-letter.
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
        sentence_ids.update(_sentence_ids_between(conn, row["start_sid"], row["end_sid"]))
    return sentence_ids


def _sentence_ids_between(
    conn: sqlite3.Connection, start_sentence_id: str, end_sentence_id: str
) -> list[str]:
    bookends = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter
        FROM sentences
        WHERE sentence_id IN (?, ?)
        """,
        (start_sentence_id, end_sentence_id),
    ).fetchall()
    by_id = {r["sentence_id"]: r for r in bookends}
    if start_sentence_id == end_sentence_id:
        return [start_sentence_id] if start_sentence_id in by_id else []
    if len(bookends) < 2:
        return []
    a = by_id[start_sentence_id]
    b = by_id[end_sentence_id]
    rows = conn.execute(
        """
        SELECT sentence_id FROM sentences
        WHERE (chapter > ? OR (chapter = ? AND ordinal_in_chapter >= ?))
          AND (chapter < ? OR (chapter = ? AND ordinal_in_chapter <= ?))
        ORDER BY chapter, ordinal_in_chapter
        """,
        (
            a["chapter"], a["chapter"], a["ordinal_in_chapter"],
            b["chapter"], b["chapter"], b["ordinal_in_chapter"],
        ),
    ).fetchall()
    return [r["sentence_id"] for r in rows]


def _english_text_for_range(
    conn: sqlite3.Connection,
    translation: str,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> str:
    rows = conn.execute(
        """
        SELECT text FROM english_verses
        WHERE translation=?
          AND (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (translation, start_chapter, start_chapter, start_verse,
         end_chapter, end_chapter, end_verse),
    ).fetchall()
    return " ".join(r["text"] for r in rows)


def _byzantine_text_for_range(
    conn: sqlite3.Connection,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> str:
    rows = conn.execute(
        """
        SELECT text FROM byzantine_verses
        WHERE (chapter > ? OR (chapter = ? AND verse >= ?))
          AND (chapter < ? OR (chapter = ? AND verse <= ?))
        ORDER BY chapter, verse
        """,
        (start_chapter, start_chapter, start_verse,
         end_chapter, end_chapter, end_verse),
    ).fetchall()
    return " ".join(r["text"] for r in rows)


def _claude_candidate_row(
    conn: sqlite3.Connection, candidate_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT candidate_id, sentence_id, style_prompt_version, source_set_id,
               model, generated_at, candidate_text
        FROM claude_candidates WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()


def _english_from_candidate_text(candidate_text: str) -> str:
    """Extract the English string from a candidate's ``candidate_text``.

    PLAN §Generation mechanism + runner: ``output_format='text'`` prompts
    store the raw English string; ``output_format='json'`` prompts store
    canonical JSON whose top-level ``english`` field is the readable
    English. We don't have an ``output_format`` column on the candidate
    row, so we detect by parsing — if it parses to a dict with an
    ``english`` key we treat it as JSON, otherwise it's plain text.
    This is the load-bearing detection that makes
    ``first-century-jewish-v1`` candidates renderable in the GSV.
    """
    text = candidate_text or ""
    stripped = text.strip()
    if not stripped or stripped[0] != "{":
        return text
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get("english"), str):
        return parsed["english"]
    return text


def _entries_at_top_rank(
    conn: sqlite3.Connection, sentence_id: str
) -> list[sqlite3.Row]:
    """Return ranking_entries rows where rank == minimum rank (== 1 if any)."""
    return list(
        conn.execute(
            """
            SELECT position, rank, candidate_kind,
                   translation_name, translation_verse_range, claude_candidate_id
            FROM ranking_entries
            WHERE sentence_id=? AND rank = (
                SELECT MIN(rank) FROM ranking_entries WHERE sentence_id=?
            )
            ORDER BY position ASC
            """,
            (sentence_id, sentence_id),
        ).fetchall()
    )


def _row_to_winner(row: sqlite3.Row, conn: sqlite3.Connection) -> SingleWinner:
    if row["candidate_kind"] == "translation":
        return TranslationWinner(
            name=row["translation_name"],
            verse_range=row["translation_verse_range"] or "",
        )
    cand = _claude_candidate_row(conn, int(row["claude_candidate_id"]))
    if cand is None:
        # FK invariants make this impossible at runtime; defensive fallback
        # so a corrupted DB still produces a renderable structure.
        return ClaudeWinner(
            candidate_id=int(row["claude_candidate_id"]),
            style_prompt_version="",
            source_set_id="SBLGNT_ONLY",
            model="",
            generated_at="",
        )
    return ClaudeWinner(
        candidate_id=int(cand["candidate_id"]),
        style_prompt_version=cand["style_prompt_version"],
        source_set_id=cand["source_set_id"],
        model=cand["model"],
        generated_at=cand["generated_at"],
    )


def _winner_signature(winner: SingleWinner) -> tuple[str, ...]:
    if isinstance(winner, TranslationWinner):
        return ("translation", winner.name, winner.verse_range)
    return ("claude", str(winner.candidate_id))


def _signature_set(winners: list[SingleWinner]) -> frozenset[tuple[str, ...]]:
    return frozenset(_winner_signature(w) for w in winners)


def _latest_tie_break(
    conn: sqlite3.Connection, sentence_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT resolved_at, winner_kind,
               winner_translation_name, winner_translation_verse_range,
               winner_claude_candidate_id, tied_against_json
        FROM tie_break_decisions
        WHERE sentence_id=?
        ORDER BY resolved_at DESC LIMIT 1
        """,
        (sentence_id,),
    ).fetchone()


def _tie_break_winner(
    row: sqlite3.Row, conn: sqlite3.Connection
) -> SingleWinner:
    if row["winner_kind"] == "translation":
        return TranslationWinner(
            name=row["winner_translation_name"],
            verse_range=row["winner_translation_verse_range"] or "",
        )
    cand = _claude_candidate_row(conn, int(row["winner_claude_candidate_id"]))
    if cand is None:
        return ClaudeWinner(
            candidate_id=int(row["winner_claude_candidate_id"]),
            style_prompt_version="",
            source_set_id="SBLGNT_ONLY",
            model="",
            generated_at="",
        )
    return ClaudeWinner(
        candidate_id=int(cand["candidate_id"]),
        style_prompt_version=cand["style_prompt_version"],
        source_set_id=cand["source_set_id"],
        model=cand["model"],
        generated_at=cand["generated_at"],
    )


def _tied_against_from_json(
    raw: str | None, conn: sqlite3.Connection
) -> list[SingleWinner]:
    if not raw:
        return []
    try:
        parsed: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[SingleWinner] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind == "translation":
            name = entry.get("name")
            vr = entry.get("verse_range") or ""
            if isinstance(name, str):
                out.append(TranslationWinner(name=name, verse_range=str(vr)))  # type: ignore[arg-type]
        elif kind == "claude":
            cid = entry.get("candidate_id")
            if isinstance(cid, int):
                cand = _claude_candidate_row(conn, cid)
                if cand is None:
                    out.append(
                        ClaudeWinner(
                            candidate_id=cid,
                            style_prompt_version="",
                            source_set_id="SBLGNT_ONLY",
                            model="",
                            generated_at="",
                        )
                    )
                else:
                    out.append(
                        ClaudeWinner(
                            candidate_id=int(cand["candidate_id"]),
                            style_prompt_version=cand["style_prompt_version"],
                            source_set_id=cand["source_set_id"],
                            model=cand["model"],
                            generated_at=cand["generated_at"],
                        )
                    )
    return out


def _resolve_text(
    winner: SingleWinner,
    conn: sqlite3.Connection,
    *,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
    sblgnt_text: str,
) -> str:
    """Resolve the rendered English text for a single-winner provenance."""
    if isinstance(winner, TranslationWinner):
        if winner.name == "SBLGNT":
            return sblgnt_text
        if winner.name == "BYZ":
            return _byzantine_text_for_range(
                conn, start_chapter, start_verse, end_chapter, end_verse
            )
        return _english_text_for_range(
            conn, winner.name, start_chapter, start_verse, end_chapter, end_verse
        )
    cand = _claude_candidate_row(conn, winner.candidate_id)
    if cand is None:
        return ""
    return _english_from_candidate_text(cand["candidate_text"])


def _winner_to_provenance(
    winner: SingleWinner,
) -> GsvTranslationProvenance | GsvClaudeProvenance:
    if isinstance(winner, TranslationWinner):
        return GsvTranslationProvenance(
            name=winner.name, verse_range=winner.verse_range
        )
    return GsvClaudeProvenance(
        candidate_id=winner.candidate_id,
        style_prompt_version=winner.style_prompt_version,
        source_set_id=winner.source_set_id,
        model=winner.model,
        generated_at=winner.generated_at,
    )


def compile_chapter_gsv(
    conn: sqlite3.Connection, chapter: int
) -> GsvChapterResponse:
    """Compile the GSV for one chapter (Designer Flow 6 step 1).

    Returns the structured form. ``GsvChapterResponse.coverage`` is the
    chapter's slice of repository-wide coverage; ``compute_coverage`` is
    the dedicated multi-chapter aggregator used by ``GET /coverage``.
    """
    if not 1 <= chapter <= 28:
        raise GsvChapterOutOfRangeError(chapter)

    sentence_rows = conn.execute(
        """
        SELECT sentence_id, chapter, ordinal_in_chapter, text_sblgnt,
               start_chapter, start_verse, end_chapter, end_verse
        FROM sentences
        WHERE chapter = ?
        ORDER BY chapter, ordinal_in_chapter
        """,
        (chapter,),
    ).fetchall()

    red_letter_ids = _red_letter_sentence_ids(conn)

    sentences: list[GsvSentence] = []
    ranked_red_letter = 0
    unresolved_ties = 0
    total_red_letter = 0

    for row in sentence_rows:
        sentence_id = row["sentence_id"]
        is_red_letter = sentence_id in red_letter_ids
        if is_red_letter:
            total_red_letter += 1
        verse_range = _verse_range_label(
            row["start_chapter"], row["start_verse"],
            row["end_chapter"], row["end_verse"],
        )

        provenance, text, counts_as_ranked, is_unresolved_tie = _resolve_sentence(
            conn,
            sentence_id=sentence_id,
            is_red_letter=is_red_letter,
            start_chapter=row["start_chapter"],
            start_verse=row["start_verse"],
            end_chapter=row["end_chapter"],
            end_verse=row["end_verse"],
            sblgnt_text=row["text_sblgnt"],
        )
        if is_red_letter and counts_as_ranked:
            ranked_red_letter += 1
        if is_unresolved_tie:
            unresolved_ties += 1
        sentences.append(
            GsvSentence(
                sentence_id=sentence_id,
                chapter=row["chapter"],
                ordinal_in_chapter=row["ordinal_in_chapter"],
                verse_range=verse_range,
                is_red_letter=is_red_letter,
                text=text,
                provenance=provenance,
            )
        )

    coverage = GsvCoverage(
        chapter=chapter,
        total_sentences=len(sentence_rows),
        total_red_letter_sentences=total_red_letter,
        ranked_red_letter_sentences=ranked_red_letter,
        unresolved_ties=unresolved_ties,
    )
    return GsvChapterResponse(
        chapter=chapter, coverage=coverage, sentences=sentences
    )


def _resolve_sentence(
    conn: sqlite3.Connection,
    *,
    sentence_id: str,
    is_red_letter: bool,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
    sblgnt_text: str,
) -> tuple[GsvProvenance, str, bool, bool]:
    """Resolve provenance + text for one sentence.

    Returns ``(provenance, text, counts_as_ranked, is_unresolved_tie)``.
    ``counts_as_ranked`` is True iff this sentence has a saved ranking
    that resolves to a non-tied winner OR a tie-broken winner — the
    coverage numerator definition.
    """
    if is_red_letter:
        top_entries = _entries_at_top_rank(conn, sentence_id)
        if not top_entries:
            # Red-letter, unranked → muted placeholder; carry BSB as the
            # readable fallback so consumers can show *something*.
            bsb_text = _english_text_for_range(
                conn, "BSB", start_chapter, start_verse, end_chapter, end_verse
            )
            return GsvUnrankedProvenance(), bsb_text, False, False
        winners = [_row_to_winner(r, conn) for r in top_entries]
        if len(winners) == 1:
            winner = winners[0]
            text = _resolve_text(
                winner, conn,
                start_chapter=start_chapter, start_verse=start_verse,
                end_chapter=end_chapter, end_verse=end_verse,
                sblgnt_text=sblgnt_text,
            )
            return _winner_to_provenance(winner), text, True, False
        # multiple winners at top rank — check for tie-break decision
        tb_row = _latest_tie_break(conn, sentence_id)
        if tb_row is not None:
            tb_winner = _tie_break_winner(tb_row, conn)
            tied_against = _tied_against_from_json(
                tb_row["tied_against_json"], conn
            )
            text = _resolve_text(
                tb_winner, conn,
                start_chapter=start_chapter, start_verse=start_verse,
                end_chapter=end_chapter, end_verse=end_verse,
                sblgnt_text=sblgnt_text,
            )
            return (
                GsvTieBrokenProvenance(
                    winner=tb_winner,
                    tied_against=tied_against,
                    resolved_at=tb_row["resolved_at"],
                ),
                text,
                True,
                False,
            )
        return (
            GsvTieProvenance(entries=winners),
            "",
            False,
            True,
        )
    # Non-red-letter sentence → BSB default per slice spec.
    bsb_text = _english_text_for_range(
        conn, "BSB", start_chapter, start_verse, end_chapter, end_verse
    )
    verse_range = _verse_range_label(
        start_chapter, start_verse, end_chapter, end_verse
    )
    return (
        GsvTranslationProvenance(name="BSB", verse_range=verse_range),
        bsb_text,
        False,
        False,
    )


# ---------------------------------------------------------------------------
# Coverage (repository-wide)
# ---------------------------------------------------------------------------


def compute_coverage(conn: sqlite3.Connection) -> GsvCoverageResponse:
    """Compute repository-wide GSV coverage stats.

    Designer Flow 6 step 2 surfaces these verbatim — denominator is the
    effective red-letter set, numerator is sentences that resolve to a
    non-tied winner OR a tie-broken winner.
    """
    red_letter_ids = _red_letter_sentence_ids(conn)
    chapters = conn.execute(
        "SELECT DISTINCT chapter FROM sentences ORDER BY chapter"
    ).fetchall()

    per_chapter: list[GsvPerChapterCoverage] = []
    total_ranked = 0
    total_red_letter = 0
    ch5_ranked = 0
    ch5_total = 0
    for ch_row in chapters:
        chapter = int(ch_row["chapter"])
        rows = conn.execute(
            """
            SELECT sentence_id FROM sentences
            WHERE chapter = ?
            ORDER BY ordinal_in_chapter
            """,
            (chapter,),
        ).fetchall()
        chapter_total = len(rows)
        chapter_red_letter = 0
        chapter_ranked = 0
        chapter_ties = 0
        for r in rows:
            sid = r["sentence_id"]
            if sid not in red_letter_ids:
                continue
            chapter_red_letter += 1
            top_entries = _entries_at_top_rank(conn, sid)
            if not top_entries:
                continue
            if len(top_entries) == 1:
                chapter_ranked += 1
                continue
            tb = _latest_tie_break(conn, sid)
            if tb is not None:
                chapter_ranked += 1
            else:
                chapter_ties += 1
        per_chapter.append(
            GsvPerChapterCoverage(
                chapter=chapter,
                total_sentences=chapter_total,
                total_red_letter_sentences=chapter_red_letter,
                ranked_red_letter_sentences=chapter_ranked,
                unresolved_ties=chapter_ties,
            )
        )
        total_ranked += chapter_ranked
        total_red_letter += chapter_red_letter
        if chapter == 5:
            ch5_ranked = chapter_ranked
            ch5_total = chapter_red_letter

    return GsvCoverageResponse(
        ranked_sentences=total_ranked,
        total_red_letter_sentences=total_red_letter,
        ch5_ranked=ch5_ranked,
        ch5_total=ch5_total,
        per_chapter=per_chapter,
    )


# ---------------------------------------------------------------------------
# Renderers (text/markdown)
# ---------------------------------------------------------------------------


def render_plain_text(response: GsvChapterResponse) -> str:
    """Render the chapter GSV as plain prose.

    Refuses to render if any unresolved tie exists — the caller (route
    layer) is expected to call this only after checking
    ``response.coverage.unresolved_ties == 0`` and to raise
    ``UnresolvedTiesError`` otherwise.
    """
    if response.coverage.unresolved_ties > 0:
        offending = [
            s.sentence_id
            for s in response.sentences
            if s.provenance.kind == "tie"  # type: ignore[attr-defined]
        ]
        raise UnresolvedTiesError(response.chapter, offending)

    lines: list[str] = []
    lines.append(f"Matthew {response.chapter} — Gavin Standard Version")
    lines.append("")
    for s in response.sentences:
        kind = s.provenance.kind  # type: ignore[attr-defined]
        if kind == "unranked":
            # Italic-ish placeholder marker; plain-text has no real italics.
            lines.append(f"[unranked: {s.text}]")
        else:
            lines.append(s.text)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_markdown(response: GsvChapterResponse) -> str:
    """Render the chapter GSV as Markdown.

    Designer Flow 6 step 4 + Architect §GSV export contract:
      - chapter header (`# Matthew {n}`)
      - coverage line as italics
      - one paragraph per sentence with verse-range label
      - per-sentence provenance footnote
      - unresolved ties surface as `[TIE: see provenance]`

    Footnote IDs are stable per sentence (`mat-{ch}-{ord}`) so the
    output is reproducible across regenerations.
    """
    lines: list[str] = []
    lines.append(f"# Matthew {response.chapter}")
    lines.append("")
    coverage = response.coverage
    lines.append(
        f"_Matthew GSV — {coverage.ranked_red_letter_sentences} / "
        f"{coverage.total_red_letter_sentences} red-letter sentences ranked_"
    )
    lines.append("")
    footnotes: list[str] = []
    for s in response.sentences:
        anchor = s.sentence_id
        kind = s.provenance.kind  # type: ignore[attr-defined]
        if kind == "tie":
            paragraph = f"**{s.verse_range}** [TIE: see provenance][^{anchor}]"
        elif kind == "unranked":
            paragraph = (
                f"> **{s.verse_range}** _{s.text}_ "
                f"_(unranked — rank at /sentence/{s.sentence_id}/rank)_"
                f"[^{anchor}]"
            )
        else:
            paragraph = f"**{s.verse_range}** {s.text}[^{anchor}]"
        lines.append(paragraph)
        lines.append("")
        footnotes.append(f"[^{anchor}]: {_provenance_footnote(s.provenance)}")
    lines.append("")
    lines.extend(footnotes)
    return "\n".join(lines).rstrip("\n") + "\n"


def _provenance_footnote(provenance: GsvProvenance) -> str:
    kind = provenance.kind  # type: ignore[attr-defined]
    if kind == "translation":
        return f"{provenance.name} / {provenance.verse_range}"  # type: ignore[attr-defined]
    if kind == "claude":
        return (
            f"claude / {provenance.model} / "  # type: ignore[attr-defined]
            f"{provenance.style_prompt_version} / "  # type: ignore[attr-defined]
            f"{provenance.source_set_id} / "  # type: ignore[attr-defined]
            f"{provenance.generated_at}"  # type: ignore[attr-defined]
        )
    if kind == "tie-broken":
        winner = provenance.winner  # type: ignore[attr-defined]
        if isinstance(winner, TranslationWinner):
            inner = f"{winner.name} / {winner.verse_range}"
        else:
            inner = (
                f"claude / {winner.model} / {winner.style_prompt_version} / "
                f"{winner.source_set_id} / {winner.generated_at}"
            )
        return (
            f"tie-broken (resolved {provenance.resolved_at}) winner: {inner}"  # type: ignore[attr-defined]
        )
    if kind == "tie":
        names = []
        for entry in provenance.entries:  # type: ignore[attr-defined]
            if isinstance(entry, TranslationWinner):
                names.append(f"{entry.name}@{entry.verse_range}")
            else:
                names.append(f"claude#{entry.candidate_id}")
        return f"unresolved tie among: {', '.join(names)}"
    return "unranked (no ranking saved yet)"
