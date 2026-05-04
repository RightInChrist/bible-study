"""``POST /api/v1/runs`` happy-path + rejection tests.

Slice 3a scope (Reliability test list):
  - test_create_run_with_one_sentence_scope
  - test_create_run_rejects_when_anthropic_key_missing
  - test_create_run_rejects_when_estimated_cost_exceeds_cap
  - test_create_run_rejects_when_daily_cap_exceeded
  - test_create_run_rejects_when_sentences_exceeds_max_per_run
  - test_csrf_required_on_post_runs
  - test_style_prompt_compatible_source_sets_validated_at_run_creation

The fake Claude client is configured to return a fixed candidate. The
test waits for the run to settle by polling ``GET /runs/{run_id}``;
SSE coverage lives in ``test_sse.py``.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api.tests.fakes import FakeClaudeClient


def _wait_for_run(
    client: TestClient, run_id: str, *, target: str = "completed", timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] == target:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"run {run_id!r} did not reach status={target!r} in {timeout}s; last={last}"
    )


def _find_sentence_id(client: TestClient, chapter: int, start_verse: int) -> str:
    body = client.get(f"/api/v1/sentences?chapter={chapter}").json()
    matches = [s for s in body["sentences"] if s["start_verse"] == start_verse]
    assert matches, f"no sentence with start_verse={start_verse} in chapter {chapter}"
    return matches[0]["sentence_id"]


def test_create_run_with_one_sentence_scope(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client
    fake._candidate_text = "Blessed are the poor in spirit (fake translation)."
    sentence_id = _find_sentence_id(client, 5, 3)

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
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items_count"] == 1
    assert body["sentence_ids"] == [sentence_id]
    assert body["estimated_cost_usd"] >= 0.0

    final = _wait_for_run(client, body["run_id"], target="completed")
    assert final["items_completed"] == 1
    assert final["items_failed"] == 0

    # Candidate stored with all five identity fields.
    cand_response = client.get(f"/api/v1/sentences/{sentence_id}/candidates")
    assert cand_response.status_code == 200
    candidates = cand_response.json()["candidates"]
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand["sentence_id"] == sentence_id
    assert cand["style_prompt_version"] == "literal-v1"
    assert cand["source_set_id"] == "BOTH_GREEK"
    assert cand["model"] == "claude-sonnet-4-6"
    assert cand["generated_at"]
    assert cand["candidate_text"].startswith("Blessed are the poor")


def test_create_run_rejects_when_anthropic_key_missing(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client
    fake._api_key_set = False
    sentence_id = _find_sentence_id(client, 5, 3)
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
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "anthropic_key_missing"


def test_create_run_rejects_when_estimated_cost_exceeds_cap(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    client, _fake = runs_client
    monkeypatch.setenv("MAX_RUN_COST_USD", "0.000001")
    from api.settings import reset_settings_cache

    reset_settings_cache()
    sentence_id = _find_sentence_id(client, 5, 3)
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
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "estimated_cost_exceeds_cap"


def test_create_run_rejects_when_daily_cap_exceeded(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    """Pre-seed a fake run consuming most of the daily cap, then post a new
    run and expect ``daily_cost_cap_exceeded``.
    """
    client, _fake = runs_client
    monkeypatch.setenv("MAX_DAILY_COST_USD", "1.0")
    from api.settings import reset_settings_cache

    reset_settings_cache()

    # Pre-seed a synthetic generation_runs row burning the daily budget.
    from api.db.connection import open_connection

    conn = open_connection()
    try:
        # 1.0 USD = 10000 in x10000 units
        conn.execute(
            """
            INSERT INTO generation_runs (
                run_id, status, scope_json, style_prompt_version, source_set_id,
                model, estimated_cost_usd_x10000, estimated_input_units,
                created_at
            ) VALUES (
                'seed-daily-cap', 'completed', '{}', 'literal-v1', 'BOTH_GREEK',
                'claude-sonnet-4-6', 10000, 1000,
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
            """
        )
    finally:
        conn.close()

    sentence_id = _find_sentence_id(client, 5, 3)
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
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "daily_cost_cap_exceeded"


def test_create_run_rejects_when_sentences_exceeds_max_per_run(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    client, _fake = runs_client
    monkeypatch.setenv("MAX_SENTENCES_PER_RUN", "2")
    from api.settings import reset_settings_cache

    reset_settings_cache()

    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "whole_chapter", "chapter": 5},
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-sonnet-4-6",
        },
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "max_sentences_per_run_exceeded"


def test_csrf_required_on_post_runs(
    runs_client: tuple[TestClient, FakeClaudeClient],
) -> None:
    client, _fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)
    body = {
        "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
        "style_prompt_version": "literal-v1",
        "source_set_id": "BOTH_GREEK",
        "model": "claude-sonnet-4-6",
    }
    # Missing Origin → 403 origin_required.
    response = client.post("/api/v1/runs", json=body, headers={"X-Requested-By": "bible-study-ui"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"
    # Missing X-Requested-By → 403.
    response = client.post(
        "/api/v1/runs", json=body, headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "x_requested_by_required"


def test_style_prompt_compatible_source_sets_validated_at_run_creation(
    runs_client: tuple[TestClient, FakeClaudeClient],
    writable_headers: dict[str, str],
) -> None:
    """``literal-v1`` requires Greek; using ``ENGLISH_ONLY_BSB`` must
    fail ``compatible_source_sets`` validation **before** any Anthropic
    call (Security §test #12).
    """
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "literal-v1",
            "source_set_id": "ENGLISH_ONLY_BSB",
            "model": "claude-sonnet-4-6",
        },
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "incompatible_source_set"
    assert fake.calls == []
