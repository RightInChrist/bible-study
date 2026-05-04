"""Read-side service functions for runs + candidates.

The static-site builder calls these directly per Hard decision #6;
``api.runs.routes`` is a thin adapter.
"""
from __future__ import annotations

import sqlite3

from api.errors import DomainError
from api.runs.runner import _from_x10000
from api.runs.schemas import (
    CandidateResponse,
    RunListItem,
    RunListResponse,
    RunResponse,
    SentenceCandidatesResponse,
)


class RunNotFoundError(DomainError):
    status_code = 404
    code = "run_not_found"


def _item_status_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM generation_run_items
        WHERE run_id=?
        GROUP BY status
        """,
        (run_id,),
    ).fetchall()
    counts: dict[str, int] = {
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "interrupted": 0,
    }
    for row in rows:
        counts[row["status"]] = int(row["n"])
    return counts


def _ordered_sentence_ids(conn: sqlite3.Connection, run_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT sentence_id FROM generation_run_items
        WHERE run_id=? ORDER BY ordinal
        """,
        (run_id,),
    ).fetchall()
    return [r["sentence_id"] for r in rows]


def get_run(conn: sqlite3.Connection, run_id: str) -> RunResponse:
    row = conn.execute(
        """
        SELECT run_id, status, style_prompt_version, source_set_id, model,
               estimated_cost_usd_x10000, parent_run_id, created_at,
               started_at, completed_at
        FROM generation_runs WHERE run_id=?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise RunNotFoundError(
            message=f"run {run_id!r} not found",
            details={"run_id": run_id},
        )
    counts = _item_status_counts(conn, run_id)
    items_count = sum(counts.values())
    sentence_ids = _ordered_sentence_ids(conn, run_id)
    return RunResponse(
        run_id=row["run_id"],
        status=row["status"],
        style_prompt_version=row["style_prompt_version"],
        source_set_id=row["source_set_id"],
        model=row["model"],
        items_count=items_count,
        items_completed=counts["completed"],
        items_failed=counts["failed"],
        items_pending=counts["pending"],
        items_running=counts["running"],
        items_cancelled=counts["cancelled"],
        items_interrupted=counts["interrupted"],
        estimated_cost_usd=_from_x10000(int(row["estimated_cost_usd_x10000"])),
        estimated_cost_usd_band_pct=20,
        sentence_ids=sentence_ids,
        parent_run_id=row["parent_run_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> RunListResponse:
    rows = conn.execute(
        """
        SELECT run_id, status, style_prompt_version, source_set_id, model,
               estimated_cost_usd_x10000, created_at, completed_at
        FROM generation_runs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[RunListItem] = []
    for row in rows:
        counts = _item_status_counts(conn, row["run_id"])
        items_count = sum(counts.values())
        out.append(
            RunListItem(
                run_id=row["run_id"],
                status=row["status"],
                style_prompt_version=row["style_prompt_version"],
                source_set_id=row["source_set_id"],
                model=row["model"],
                items_count=items_count,
                items_completed=counts["completed"],
                items_failed=counts["failed"],
                estimated_cost_usd=_from_x10000(int(row["estimated_cost_usd_x10000"])),
                created_at=row["created_at"],
                completed_at=row["completed_at"],
            )
        )
    return RunListResponse(runs=out, limit=limit)


def list_sentence_candidates(
    conn: sqlite3.Connection, sentence_id: str
) -> SentenceCandidatesResponse:
    rows = conn.execute(
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
    candidates = [
        CandidateResponse(
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
        for row in rows
    ]
    return SentenceCandidatesResponse(sentence_id=sentence_id, candidates=candidates)
