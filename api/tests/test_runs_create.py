"""``POST /api/v1/runs`` happy-path + rejection tests.

Slice 3a-redo:
  - test_create_run_with_one_sentence_scope
  - test_create_run_rejects_when_claude_cli_unavailable
  - test_create_run_rejects_when_max_runs_per_day_exceeded
  - test_create_run_rejects_when_sentences_exceeds_max_per_run
  - test_csrf_required_on_post_runs
  - test_style_prompt_compatible_source_sets_validated_at_run_creation
  - test_runs_with_first_century_jewish_v1_parses_structured_json

Model+effort flag wiring:
  - test_runs_pass_model_and_effort_to_spawner — defaults
  - test_runs_accept_model_and_effort_overrides — explicit overrides
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from api.tests.fakes import FakeWorktreeSpawner


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
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    client, _fake = runs_client
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
    assert body["estimated_worktree_count"] == 1

    final = _wait_for_run(client, body["run_id"], target="completed")
    assert final["items_completed"] == 1
    assert final["items_failed"] == 0
    assert final["estimated_worktree_count"] == 1

    cand_response = client.get(f"/api/v1/sentences/{sentence_id}/candidates")
    assert cand_response.status_code == 200
    candidates = cand_response.json()["candidates"]
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand["sentence_id"] == sentence_id
    assert cand["style_prompt_version"] == "literal-v1"
    assert cand["source_set_id"] == "BOTH_GREEK"
    # The fake spawner mirrors the real one: it reports
    # ``"{model}+{effort}"`` from the request.
    assert cand["model"] == "claude-sonnet-4-6+xhigh"
    assert cand["generated_at"]
    # literal-v1 is output_format=text; candidate_text is plain English.
    assert "[fake]" in cand["candidate_text"]


def test_create_run_rejects_when_claude_cli_unavailable(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client
    fake.cli_available_returns = False
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
    assert body["code"] == "claude_cli_unavailable"


def test_create_run_rejects_when_max_runs_per_day_exceeded(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    """Pre-seed the daily run cap with a synthetic generation_runs row."""
    client, _fake = runs_client
    monkeypatch.setenv("MAX_RUNS_PER_DAY", "1")
    from api.settings import reset_settings_cache

    reset_settings_cache()

    from api.db.connection import open_connection

    conn = open_connection()
    try:
        conn.execute(
            """
            INSERT INTO generation_runs (
                run_id, status, scope_json, style_prompt_version, source_set_id,
                model, estimated_worktree_count,
                created_at
            ) VALUES (
                'seed-daily-cap', 'completed', '{}', 'literal-v1', 'BOTH_GREEK',
                'claude-sonnet-4-6', 1,
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
    assert response.json()["code"] == "max_runs_per_day_exceeded"


def test_create_run_rejects_when_sentences_exceeds_max_per_run(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    client, _fake = runs_client
    monkeypatch.setenv("MAX_WORKTREES_PER_RUN", "2")
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
    assert response.json()["code"] == "max_worktrees_per_run_exceeded"


def test_csrf_required_on_post_runs(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
) -> None:
    client, _fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)
    body = {
        "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
        "style_prompt_version": "literal-v1",
        "source_set_id": "BOTH_GREEK",
        "model": "claude-sonnet-4-6",
    }
    response = client.post("/api/v1/runs", json=body, headers={"X-Requested-By": "bible-study-ui"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"
    response = client.post(
        "/api/v1/runs", json=body, headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "x_requested_by_required"


def test_style_prompt_compatible_source_sets_validated_at_run_creation(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
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


def test_runs_with_first_century_jewish_v1_parses_structured_json(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """``first-century-jewish-v1`` is ``output_format=json``.

    The fake spawner returns a structured-fields stub; the runner must
    serialise it as JSON into ``candidate_text`` so the read API can
    parse it back into the documented shape.
    """
    client, _fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)

    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "first-century-jewish-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    final = _wait_for_run(client, response.json()["run_id"], target="completed")
    assert final["items_completed"] == 1

    cand_response = client.get(f"/api/v1/sentences/{sentence_id}/candidates")
    assert cand_response.status_code == 200
    candidates = cand_response.json()["candidates"]
    assert candidates, "expected at least one candidate"
    cand = candidates[0]
    assert cand["style_prompt_version"] == "first-century-jewish-v1"

    parsed = json.loads(cand["candidate_text"])
    expected_keys = {
        "english",
        "underlying_hypothesis",
        "cultural_notes",
        "intertexts",
        "audience",
        "pragmatic_act",
        "confidence",
    }
    assert expected_keys.issubset(parsed.keys()), (
        f"missing keys: {expected_keys - set(parsed.keys())}"
    )
    assert isinstance(parsed["intertexts"], list)
    assert "english" in parsed and isinstance(parsed["english"], str)


def test_runs_pass_model_and_effort_to_spawner(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """When the body omits ``effort``, the schema default is forwarded."""
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)

    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    _wait_for_run(client, run_id, target="completed")

    assert len(fake.calls) == 1, fake.calls
    call = fake.calls[0]
    assert call.model == "claude-opus-4-7"
    assert call.effort == "xhigh"

    # Run-level provenance reflects the composite.
    run_response = client.get(f"/api/v1/runs/{run_id}").json()
    assert run_response["model"] == "claude-opus-4-7+xhigh"

    cand_response = client.get(f"/api/v1/sentences/{sentence_id}/candidates").json()
    assert cand_response["candidates"][0]["model"] == "claude-opus-4-7+xhigh"


def test_runs_accept_model_and_effort_overrides(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """Explicit ``model`` + ``effort`` flow through to the spawner."""
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)

    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-sonnet-4-6",
            "effort": "high",
        },
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    _wait_for_run(client, run_id, target="completed")

    assert len(fake.calls) == 1, fake.calls
    call = fake.calls[0]
    assert call.model == "claude-sonnet-4-6"
    assert call.effort == "high"

    run_response = client.get(f"/api/v1/runs/{run_id}").json()
    assert run_response["model"] == "claude-sonnet-4-6+high"

    cand_response = client.get(f"/api/v1/sentences/{sentence_id}/candidates").json()
    assert cand_response["candidates"][0]["model"] == "claude-sonnet-4-6+high"


def test_runs_use_default_model_and_effort_when_omitted(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """Body without ``model``/``effort`` falls back to schema defaults."""
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 3)

    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
        },
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    _wait_for_run(client, run_id, target="completed")

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.model == "claude-opus-4-7"
    assert call.effort == "xhigh"

    cand_response = client.get(f"/api/v1/sentences/{sentence_id}/candidates").json()
    assert cand_response["candidates"][0]["model"] == "claude-opus-4-7+xhigh"
