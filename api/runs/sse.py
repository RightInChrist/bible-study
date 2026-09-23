"""SSE replay-then-tail for ``GET /api/v1/runs/{run_id}/stream``.

Reliability §SSE replay protocol pins event types and sequencing:

  1. ``replay_begin`` (always first).
  2. zero or more ``item_*`` in ``ordinal`` order.
  3. ``replay_end``.
  4. terminal-state runs: one of
     ``run_finished``/``run_cancelled``/``run_interrupted``/``run_failed``,
     stream closes.
  5. active runs: live tail of ``item_*`` + ``keepalive`` until terminal.

Server keepalive cadence: 15s — pinned by ``sse-starlette``'s
``ping=15``. The keepalive is sent only during the live tail per the
PLAN ("Sent every 15s while the live tail is attached, never during
replay").
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import AsyncIterator

from sse_starlette.sse import EventSourceResponse

from api.db.connection import open_connection
from api.runs.runner import _state, get_broadcaster
from api.runs.schemas import RunItemSnapshot


_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


def _format_event(event: str, data: dict[str, object]) -> dict[str, str]:
    """Render an event dict for ``EventSourceResponse``.

    ``sse-starlette`` accepts a dict with ``event`` + ``data`` keys; we
    render ``data`` as JSON so reducers parse one shape regardless of
    event type.
    """
    return {
        "event": event,
        "data": json.dumps(data, ensure_ascii=False),
    }


def _read_run_status(run_id: str) -> str | None:
    conn = open_connection()
    try:
        row = conn.execute(
            "SELECT status FROM generation_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return str(row["status"])
    finally:
        conn.close()


def _read_replay_items(run_id: str) -> list[RunItemSnapshot]:
    """Replay reads non-``pending`` AND non-``running`` rows.

    Reliability §SSE replay protocol pinned ``WHERE status != 'pending'``;
    we also exclude ``running`` because items briefly in that state are
    a transient artefact (the runner is mid-call) and replaying them
    would tell the client about work that hasn't terminated yet — the
    same rationale Reliability uses for excluding ``pending``.
    """
    conn = open_connection()
    try:
        rows = conn.execute(
            """
            SELECT run_id, ordinal, sentence_id, chapter, status, candidate_id,
                   error_code, error_message, started_at, completed_at
            FROM generation_run_items
            WHERE run_id=? AND status NOT IN ('pending', 'running')
            ORDER BY ordinal
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        RunItemSnapshot(
            run_id=row["run_id"],
            ordinal=int(row["ordinal"]),
            sentence_id=row["sentence_id"],
            chapter=int(row["chapter"]) if row["chapter"] is not None else None,
            status=row["status"],
            candidate_id=int(row["candidate_id"]) if row["candidate_id"] is not None else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
        for row in rows
    ]


def _read_total_items(run_id: str) -> int:
    conn = open_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM generation_run_items WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["n"])
    finally:
        conn.close()


def _replay_item_event(item: RunItemSnapshot) -> dict[str, str]:
    base: dict[str, object] = {
        "run_id": item.run_id,
        "ordinal": item.ordinal,
    }
    if item.sentence_id is not None:
        base["sentence_id"] = item.sentence_id
    if item.chapter is not None:
        base["chapter"] = item.chapter
    if item.status == "completed":
        return _format_event(
            "item_completed",
            {
                **base,
                "candidate_id": item.candidate_id,
                "completed_at": item.completed_at,
            },
        )
    if item.status == "failed":
        return _format_event(
            "item_failed",
            {
                **base,
                "error_code": item.error_code,
                "error_message": item.error_message,
            },
        )
    if item.status == "cancelled":
        return _format_event("item_cancelled", base)
    if item.status == "interrupted":
        return _format_event("item_interrupted", base)
    # ``running`` should only appear momentarily in the DB; replay treats
    # it the same as ``pending`` and excludes it. Defensive fallback:
    return _format_event("item_running", base)


async def stream_run(run_id: str) -> EventSourceResponse:
    """Build the SSE response for ``GET /api/v1/runs/{run_id}/stream``."""
    initial_status = _read_run_status(run_id)
    if initial_status is None:
        # Caller's HTTP handler should have raised RunNotFoundError before
        # us, but the SSE coroutine guards anyway — return an empty stream
        # rather than a half-rendered one.
        async def _empty() -> AsyncIterator[dict[str, str]]:
            return
            yield  # pragma: no cover

        return EventSourceResponse(_empty())

    total_items = _read_total_items(run_id)
    broadcaster = get_broadcaster(run_id)
    queue = broadcaster.subscribe() if broadcaster is not None else None

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        # 1. replay_begin
        yield _format_event(
            "replay_begin",
            {"run_id": run_id, "total_items": total_items, "run_status": initial_status},
        )

        # 2. replay items (status != pending) in ordinal order
        for item in _read_replay_items(run_id):
            yield _replay_item_event(item)

        # 3. replay_end
        replayed = sum(1 for _ in _read_replay_items(run_id))  # cheap; could pass count
        yield _format_event("replay_end", {"run_id": run_id, "replayed": replayed})

        # Re-read the run status here — between step 1 and step 3, items
        # could have completed and pushed the run terminal. The replay
        # we just emitted reflects DB state, so what matters now is
        # whether to attach live or close.
        post_replay_status = _read_run_status(run_id) or initial_status

        if post_replay_status in _TERMINAL_RUN_STATUSES:
            terminal = _terminal_event(post_replay_status, run_id)
            if terminal is not None:
                yield terminal
            return

        # 4. live tail
        if queue is None:
            # No broadcaster (run is active per DB, but the in-process
            # registry has no entry — likely the runner crashed mid-flight
            # and we'll catch up via DB polling on the next reconnect).
            return

        try:
            while True:
                payload = await queue.get()
                event = str(payload.get("event", "item"))
                data = payload.get("data", {}) or {}
                yield _format_event(event, data if isinstance(data, dict) else {})
                if event in ("run_finished", "run_failed", "run_cancelled", "run_interrupted"):
                    break
        finally:
            if broadcaster is not None and queue is not None:
                broadcaster.unsubscribe(queue)

    return EventSourceResponse(event_stream(), ping=15)


def _terminal_event(status: str, run_id: str) -> dict[str, str] | None:
    if status == "completed":
        return _format_event("run_finished", {"run_id": run_id, "status": "completed"})
    if status == "failed":
        return _format_event("run_failed", {"run_id": run_id})
    if status == "cancelled":
        return _format_event("run_cancelled", {"run_id": run_id})
    if status == "interrupted":
        return _format_event("run_interrupted", {"run_id": run_id})
    return None


__all__ = ["stream_run"]
