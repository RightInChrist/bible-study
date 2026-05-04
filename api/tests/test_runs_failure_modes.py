"""Per-item failure modes from the runner (Reliability §Failure modes).

Slice 3a:
  - test_anthropic_429_marks_item_failed_with_rate_limit_error_code
  - test_anthropic_5xx_marks_item_failed_with_anthropic_api_error
  - test_invalid_response_marks_item_failed
  - test_hidden_combo_skips_anthropic_call
  - test_per_sentence_transactions_isolate_failures
  - test_source_snapshot_dedup_via_content_addressing

These all rely on ``runs_client`` injecting :class:`FakeClaudeClient`
with a custom ``responder`` callback so we can shape failures
per-sentence.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api.claude.client import AnthropicGenerationError, ClaudeCallRequest
from api.claude.schemas import CandidateGenerated
from api.tests.fakes import FakeClaudeClient


def _wait_run_settled(
    client: TestClient, run_id: str, *, timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/runs/{run_id}").json()
        if body["items_pending"] == 0 and body["items_running"] == 0:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id!r} did not settle within {timeout}s")


def _post_one_sentence(
    client: TestClient,
    headers: dict[str, str],
    sentence_id: str,
    *,
    style: str = "literal-v1",
    source_set: str = "BOTH_GREEK",
) -> dict:
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": style,
            "source_set_id": source_set,
            "model": "claude-sonnet-4-6",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _find_sentence_id(client: TestClient, start_verse: int) -> str:
    body = client.get("/api/v1/sentences?chapter=5").json()
    return next(s for s in body["sentences"] if s["start_verse"] == start_verse)["sentence_id"]


def _last_item_status(client: TestClient, run_id: str) -> dict:
    """Return the (only) item's status by querying via a follow-up route.

    There's no read API for raw items in this slice; we open the DB
    directly to inspect what the runner committed.
    """
    from api.db.connection import open_connection

    conn = open_connection()
    try:
        row = conn.execute(
            """
            SELECT status, error_code, error_message, candidate_id
            FROM generation_run_items WHERE run_id=? ORDER BY ordinal LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        conn.close()


def test_anthropic_429_marks_item_failed_with_rate_limit_error_code(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client

    async def responder(_req: ClaudeCallRequest) -> CandidateGenerated:
        raise AnthropicGenerationError("rate_limit", "anthropic 429")

    fake._responder = responder
    sentence_id = _find_sentence_id(client, 3)
    body = _post_one_sentence(client, writable_headers, sentence_id)
    _wait_run_settled(client, body["run_id"])
    item = _last_item_status(client, body["run_id"])
    assert item["status"] == "failed"
    assert item["error_code"] == "rate_limit"
    assert item["candidate_id"] is None


def test_anthropic_5xx_marks_item_failed_with_anthropic_api_error(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client

    async def responder(_req: ClaudeCallRequest) -> CandidateGenerated:
        raise AnthropicGenerationError("anthropic_api_error", "anthropic 503")

    fake._responder = responder
    sentence_id = _find_sentence_id(client, 3)
    body = _post_one_sentence(client, writable_headers, sentence_id)
    _wait_run_settled(client, body["run_id"])
    item = _last_item_status(client, body["run_id"])
    assert item["status"] == "failed"
    assert item["error_code"] == "anthropic_api_error"


def test_invalid_response_marks_item_failed(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client

    async def responder(_req: ClaudeCallRequest) -> CandidateGenerated:
        raise AnthropicGenerationError("invalid_response", "empty content blocks")

    fake._responder = responder
    sentence_id = _find_sentence_id(client, 3)
    body = _post_one_sentence(client, writable_headers, sentence_id)
    _wait_run_settled(client, body["run_id"])
    item = _last_item_status(client, body["run_id"])
    assert item["status"] == "failed"
    assert item["error_code"] == "invalid_response"


def test_hidden_combo_skips_anthropic_call(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 3)

    # Insert a hidden_combos row matching the run combo.
    from api.db.connection import open_connection

    conn = open_connection()
    try:
        conn.execute(
            """
            INSERT INTO hidden_combos
              (sentence_id, style_prompt_version, source_set_id, model, hidden_at)
            VALUES (?, 'literal-v1', 'BOTH_GREEK', 'claude-sonnet-4-6',
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (sentence_id,),
        )
    finally:
        conn.close()

    body = _post_one_sentence(client, writable_headers, sentence_id)
    _wait_run_settled(client, body["run_id"])
    item = _last_item_status(client, body["run_id"])
    # Hidden-combo skips are recorded as ``cancelled`` so the schema's
    # CHECK constraint is satisfied (no error_code on a non-failed status).
    assert item["status"] == "cancelled"
    assert item["candidate_id"] is None
    assert fake.calls == [], "Anthropic must not be called for hidden combos"


def test_per_sentence_transactions_isolate_failures(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    """One sentence fails; the other in the same run still completes."""
    client, fake = runs_client

    failing_sentence_id = _find_sentence_id(client, 3)
    succeeding_sentence_id = _find_sentence_id(client, 4)

    async def responder(req: ClaudeCallRequest) -> CandidateGenerated:
        if req.bundle.sentence_id == failing_sentence_id:
            raise AnthropicGenerationError("anthropic_api_error", "boom")
        from datetime import UTC, datetime

        return CandidateGenerated(
            candidate_text="ok",
            model=req.model,
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            latency_ms=2,
        )

    fake._responder = responder

    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "verse_range", "chapter": 5, "start_verse": 3, "end_verse": 4},
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-sonnet-4-6",
        },
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    body = _wait_run_settled(client, run_id)
    assert body["items_completed"] == 1
    assert body["items_failed"] == 1


def test_source_snapshot_dedup_via_content_addressing(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    """Run the same combo twice on the same sentence.

    Asserts: two ``claude_candidates`` rows but exactly one
    ``source_snapshots`` row referenced by both.
    """
    client, fake = runs_client
    fake._candidate_text = "deterministic-fake"
    sentence_id = _find_sentence_id(client, 3)

    body1 = _post_one_sentence(client, writable_headers, sentence_id)
    _wait_run_settled(client, body1["run_id"])
    body2 = _post_one_sentence(client, writable_headers, sentence_id)
    _wait_run_settled(client, body2["run_id"])

    from api.db.connection import open_connection

    conn = open_connection()
    try:
        snap_count_row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM source_snapshots
            WHERE sentence_id=? AND source_set_id='BOTH_GREEK'
              AND prompt_version='literal-v1'
            """,
            (sentence_id,),
        ).fetchone()
        cand_rows = conn.execute(
            """
            SELECT candidate_id, source_snapshot_hash FROM claude_candidates
            WHERE sentence_id=? AND style_prompt_version='literal-v1'
              AND source_set_id='BOTH_GREEK' AND model='claude-sonnet-4-6'
            """,
            (sentence_id,),
        ).fetchall()
    finally:
        conn.close()

    assert int(snap_count_row["n"]) == 1, "exactly one snapshot expected"
    assert len(cand_rows) == 2, "two candidates expected (re-generation creates a new row)"
    assert (
        cand_rows[0]["source_snapshot_hash"] == cand_rows[1]["source_snapshot_hash"]
    ), "both candidates must reference the same snapshot hash"
