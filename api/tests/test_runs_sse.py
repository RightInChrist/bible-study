"""SSE replay protocol tests (Reliability §SSE replay protocol).

Slice 3a:
  - test_sse_replay_completed_run_emits_all_items_then_run_finished
  - test_sse_replay_in_progress_run_replays_then_attaches_live_tail

Both tests connect via ``TestClient.stream`` to the SSE endpoint and
parse the event stream into ``(event, data_json)`` tuples.

``sse-starlette`` keeps a process-wide ``AppStatus.should_exit_event``
bound to the event loop where the first SSE response was issued. The
TestClient creates a fresh loop per request, so we reset the event
between tests to avoid the "bound to a different event loop" error.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from api.tests.fakes import FakeWorktreeSpawner


@pytest.fixture(autouse=True)
def _reset_sse_starlette_event_loop_state() -> None:
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse a multi-event SSE response body."""
    events: list[tuple[str, dict]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for raw_line in text.splitlines():
        if raw_line == "":
            # Dispatch the buffered event.
            if current_event is not None and current_data:
                payload = "\n".join(current_data)
                try:
                    events.append((current_event, json.loads(payload)))
                except json.JSONDecodeError:
                    events.append((current_event, {}))
            current_event = None
            current_data = []
            continue
        if raw_line.startswith("event:"):
            current_event = raw_line[len("event:") :].strip()
        elif raw_line.startswith("data:"):
            current_data.append(raw_line[len("data:") :].lstrip())
    # Trailing event without blank line.
    if current_event is not None and current_data:
        payload = "\n".join(current_data)
        try:
            events.append((current_event, json.loads(payload)))
        except json.JSONDecodeError:
            events.append((current_event, {}))
    return events


def _read_sse_stream(client: TestClient, url: str, *, timeout: float = 5.0) -> str:
    """Stream the SSE response and return the accumulated text body.

    The server closes the stream when the run reaches a terminal state;
    if we observe one of the terminal events in-band we break early so
    a well-behaved test doesn't depend on the server's close timing.
    """
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    terminal_markers = ("run_finished", "run_failed", "run_cancelled", "run_interrupted")
    with client.stream("GET", url) as response:
        assert response.status_code == 200, response.read()
        for chunk in response.iter_text():
            if chunk:
                chunks.append(chunk)
            joined = "".join(chunks)
            if any(marker in joined for marker in terminal_markers):
                break
            if time.monotonic() >= deadline:
                break
    return "".join(chunks)


def _find_sentence_id(client: TestClient, start_verse: int) -> str:
    body = client.get("/api/v1/sentences?chapter=5").json()
    return next(s for s in body["sentences"] if s["start_verse"] == start_verse)["sentence_id"]


def _wait_for_completed(client: TestClient, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/api/v1/runs/{run_id}").json()["status"] == "completed":
            return
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not complete within {timeout}s")


def test_sse_replay_completed_run_emits_all_items_then_run_finished(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    client, _fake = runs_client
    sentence_id = _find_sentence_id(client, 3)
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-sonnet-4-6",
        },
        headers=writable_headers,
    )
    run_id = response.json()["run_id"]
    _wait_for_completed(client, run_id)

    text = _read_sse_stream(client, f"/api/v1/runs/{run_id}/stream", timeout=5.0)
    events = _parse_sse(text)
    event_names = [e[0] for e in events]
    assert event_names[0] == "replay_begin"
    assert "replay_end" in event_names
    assert "run_finished" in event_names
    # The completed item is replayed.
    item_completed = [e for e in events if e[0] == "item_completed"]
    assert len(item_completed) == 1
    assert item_completed[0][1]["sentence_id"] == sentence_id


def test_sse_replay_in_progress_run_replays_then_attaches_live_tail(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """Replay-then-tail behaviour for an active run.

    We can't reliably reproduce a true "client connects mid-run" race
    inside FastAPI's :class:`TestClient` (which serialises ASGI requests
    on a single anyio portal — the SSE GET does not interleave with the
    runner's async task tick the way a real HTTP server would). Instead,
    we exercise the equivalent invariant through the DB:

      1. Pre-seed a ``generation_runs`` row in ``running`` status with one
         ``completed`` item (i.e. simulate a snapshot of an active run).
      2. Connect to ``/runs/{run_id}/stream``.
      3. Assert: ``replay_begin`` → ``item_completed`` (replay) → ``replay_end``,
         and the stream stays open (no terminal event) because the run
         status is still ``running``.

    The completed-run terminal-replay path is covered by
    ``test_sse_replay_completed_run_emits_all_items_then_run_finished``;
    full reconnect-mid-run timing fidelity is deferred to the eval
    harness's ``bs/sse/`` seed where a real HTTP server makes the
    interleave deterministic.
    """
    client, _fake = runs_client
    sentence_id = _find_sentence_id(client, 3)

    # Pre-seed a fake snapshot inside the DB.
    from api.db.connection import open_connection

    run_id = "test-active-run-id"
    conn = open_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Style prompts are seeded by the importer; reuse literal-v1.
        conn.execute(
            """
            INSERT INTO source_snapshots (
                snapshot_hash, sentence_id, source_set_id, fixture_version,
                prompt_version, payload_json, byte_size,
                source_snapshot_canon_version, created_at
            ) VALUES (
                'fake-hash', ?, 'BOTH_GREEK', 'fake-fv', 'literal-v1',
                '{}', 2, 'v1', '2026-05-03T00:00:00Z'
            )
            """,
            (sentence_id,),
        )
        cur = conn.execute(
            """
            INSERT INTO claude_candidates (
                sentence_id, style_prompt_version, source_set_id, model,
                generated_at, candidate_text, source_snapshot_hash
            ) VALUES (?, 'literal-v1', 'BOTH_GREEK', 'claude-sonnet-4-6',
                      '2026-05-03T00:00:00Z', 'fake', 'fake-hash')
            """,
            (sentence_id,),
        )
        candidate_id = int(cur.lastrowid or 0)
        conn.execute(
            """
            INSERT INTO generation_runs (
                run_id, status, scope_json, style_prompt_version,
                source_set_id, model, estimated_worktree_count,
                created_at, started_at
            ) VALUES (?, 'running', '{}', 'literal-v1', 'BOTH_GREEK',
                      'claude-sonnet-4-6', 1,
                      '2026-05-03T00:00:00Z', '2026-05-03T00:00:00Z')
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO generation_run_items (
                run_id, ordinal, sentence_id, status, candidate_id, completed_at
            ) VALUES (?, 1, ?, 'completed', ?, '2026-05-03T00:00:01Z')
            """,
            (run_id, sentence_id, candidate_id),
        )
        # Add a still-pending item so the run is genuinely "in progress"
        # — replay must NOT include this row (it's filtered by
        # ``WHERE status != 'pending'``).
        conn.execute(
            """
            INSERT INTO generation_run_items (
                run_id, ordinal, sentence_id, status
            ) VALUES (?, 2, ?, 'pending')
            """,
            (run_id, sentence_id),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()

    # We don't expect a terminal event (the run is "running"). Read with a
    # short timeout; assert the replay events appear and the stream
    # doesn't just hang silently before emitting the replay header.
    text = _read_sse_stream(client, f"/api/v1/runs/{run_id}/stream", timeout=2.0)
    events = _parse_sse(text)
    event_names = [e[0] for e in events]
    assert event_names[0] == "replay_begin", f"events={event_names!r}"
    assert "item_completed" in event_names, f"events={event_names!r}"
    assert "replay_end" in event_names, f"events={event_names!r}"
    # Replay correctly excludes the pending item — only one item event.
    assert event_names.count("item_completed") == 1
    # No terminal event for an active run with a still-pending item.
    assert "run_finished" not in event_names
    assert "run_failed" not in event_names
