"""Read-side service for chapter summaries.

Writes go through the runner via ``POST /api/v1/runs``; this module
owns the read paths and the static-site projection. Hard decision #6:
the static-site builder calls these functions directly, never the
route handlers.
"""
from __future__ import annotations

import json
import sqlite3

from api.chapters.schemas import (
    ChapterSummaryListResponse,
    ChapterSummaryResponse,
    parse_summary_payload,
)
from api.errors import DomainError


class ChapterSummaryNotFoundError(DomainError):
    status_code = 404
    code = "chapter_summary_not_found"


def _parse_consulted(blob: str | None) -> list[int]:
    if blob is None:
        return []
    try:
        parsed = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[int] = []
    for item in parsed:
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, str) and item.lstrip("-").isdigit():
            out.append(int(item))
    return out


def _row_to_response(row: sqlite3.Row) -> ChapterSummaryResponse:
    return ChapterSummaryResponse(
        summary_id=int(row["summary_id"]),
        chapter=int(row["chapter"]),
        prompt_version=str(row["prompt_version"]),
        source_set_id=str(row["source_set_id"]),
        model=str(row["model"]),
        source_snapshot_hash=str(row["source_snapshot_hash"]),
        summary=parse_summary_payload(str(row["summary_text"])),
        raw_summary_text=str(row["summary_text"]),
        generated_at=str(row["generated_at"]),
        run_id=row["run_id"],
        candidate_ids_consulted=_parse_consulted(row["candidate_ids_consulted"]),
        hidden_bool=bool(row["hidden_bool"]),
    )


def list_summaries_for_chapter(
    conn: sqlite3.Connection, chapter: int, *, include_hidden: bool = False
) -> ChapterSummaryListResponse:
    """Return all chapter summaries for a chapter, latest first.

    Hidden summaries are excluded by default; the schema's ``hidden_bool``
    flag is the v1 affordance for marking a bad summary without deleting
    it (no UI in this slice).
    """
    if chapter < 1 or chapter > 28:
        raise DomainError(
            message=f"chapter {chapter} out of range (1..28)",
            details={"chapter": chapter},
        )
    if include_hidden:
        sql = """
            SELECT summary_id, chapter, prompt_version, source_set_id, model,
                   source_snapshot_hash, summary_text, generated_at, run_id,
                   candidate_ids_consulted, hidden_bool
            FROM chapter_summaries
            WHERE chapter = ?
            ORDER BY generated_at DESC, summary_id DESC
        """
    else:
        sql = """
            SELECT summary_id, chapter, prompt_version, source_set_id, model,
                   source_snapshot_hash, summary_text, generated_at, run_id,
                   candidate_ids_consulted, hidden_bool
            FROM chapter_summaries
            WHERE chapter = ? AND hidden_bool = 0
            ORDER BY generated_at DESC, summary_id DESC
        """
    rows = conn.execute(sql, (chapter,)).fetchall()
    return ChapterSummaryListResponse(
        chapter=chapter,
        summaries=[_row_to_response(r) for r in rows],
    )


def get_summary(conn: sqlite3.Connection, summary_id: int) -> ChapterSummaryResponse:
    row = conn.execute(
        """
        SELECT summary_id, chapter, prompt_version, source_set_id, model,
               source_snapshot_hash, summary_text, generated_at, run_id,
               candidate_ids_consulted, hidden_bool
        FROM chapter_summaries
        WHERE summary_id = ?
        """,
        (summary_id,),
    ).fetchone()
    if row is None:
        raise ChapterSummaryNotFoundError(
            message=f"chapter summary {summary_id!r} not found",
            details={"summary_id": summary_id},
        )
    return _row_to_response(row)


__all__ = [
    "ChapterSummaryNotFoundError",
    "get_summary",
    "list_summaries_for_chapter",
]
