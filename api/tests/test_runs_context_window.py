"""``POST /api/v1/runs`` context-window opt-in tests (Slice 3c).

The runner inspects the active prompt's ``wants_context_window``
front-matter flag. When true (currently only first-century-jewish-v1)
the source-bundle resolver attaches an ``adjacent_context`` block sized
by ``CONTEXT_WINDOW_BEFORE`` / ``CONTEXT_WINDOW_AFTER`` settings. When
false (literal-v1, dynamic-v1, plainspoken-v1) the bundle is focal-only
and ``adjacent_context`` stays ``None``.

Tests verify:
  - first-century-jewish-v1 → bundle has populated adjacent_context, and
    the input.md text the runner serialises includes the adjacent SBLGNT
  - literal-v1 → bundle.adjacent_context is None, input.md has no
    adjacent payload
  - settings override changes the window size on the bundle
  - changing window size produces a different source_snapshot_hash
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api.claude.prompts import load_prompt
from api.claude.schemas import SentenceMeta
from api.claude.worktree import _build_input_text
from api.tests.fakes import FakeWorktreeSpawner


def _wait_for_run(
    client: TestClient, run_id: str, *, target: str = "completed", timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/runs/{run_id}")
        last = r.json()
        if last["status"] == target:
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id!r} never reached {target!r}; last={last}")


def _find_sentence_id(client: TestClient, chapter: int, start_verse: int) -> str:
    body = client.get(f"/api/v1/sentences?chapter={chapter}").json()
    matches = [s for s in body["sentences"] if s["start_verse"] == start_verse]
    assert matches, f"no sentence with start_verse={start_verse} in chapter {chapter}"
    return matches[0]["sentence_id"]


def test_runs_first_century_jewish_passes_context_to_spawner(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """The opt-in prompt → bundle with adjacent_context, passed through to spawner."""
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 13)

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
    _wait_for_run(client, response.json()["run_id"], target="completed")

    assert len(fake.calls) == 1
    bundle = fake.calls[0].bundle
    assert bundle.adjacent_context is not None
    assert len(bundle.adjacent_context.before) == 3
    assert len(bundle.adjacent_context.after) == 3

    # The input.md text the runner would write into the worktree must
    # include the adjacent SBLGNT — verify by re-rendering the same
    # bundle through the production helper.
    prompt = fake.calls[0].prompt
    sentence_meta = fake.calls[0].sentence_meta
    input_text = _build_input_text(
        prompt=prompt, bundle=bundle, sentence_meta=sentence_meta
    )
    assert "adjacent_context" in input_text
    # At least one adjacent SBLGNT appears verbatim in the rendered input.
    sample = bundle.adjacent_context.before[0].text_sblgnt
    assert sample in input_text


def test_runs_literal_does_not_pass_context_to_spawner(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
) -> None:
    """The opt-out prompt → focal-only bundle, no adjacent payload in input.md."""
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 13)

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
    _wait_for_run(client, response.json()["run_id"], target="completed")

    assert len(fake.calls) == 1
    bundle = fake.calls[0].bundle
    assert bundle.adjacent_context is None

    prompt = fake.calls[0].prompt
    input_text = _build_input_text(
        prompt=prompt,
        bundle=bundle,
        sentence_meta=SentenceMeta(
            sentence_id=fake.calls[0].sentence_meta.sentence_id,
            chapter=fake.calls[0].sentence_meta.chapter,
            start_verse=fake.calls[0].sentence_meta.start_verse,
            end_verse=fake.calls[0].sentence_meta.end_verse,
            verse_range=fake.calls[0].sentence_meta.verse_range,
        ),
    )
    assert "adjacent_context" not in input_text


def test_context_window_changes_snapshot_hash(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    """Same focal + same combo + different CONTEXT_WINDOW_BEFORE → different snapshot."""
    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 13)

    # First run with default before=3.
    r1 = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "first-century-jewish-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert r1.status_code == 200, r1.text
    _wait_for_run(client, r1.json()["run_id"], target="completed")

    # Now bump the window via env var and re-run.
    monkeypatch.setenv("CONTEXT_WINDOW_BEFORE", "5")
    monkeypatch.setenv("CONTEXT_WINDOW_AFTER", "5")
    from api.settings import reset_settings_cache

    reset_settings_cache()

    r2 = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "one_sentence", "sentence_id": sentence_id},
            "style_prompt_version": "first-century-jewish-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert r2.status_code == 200, r2.text
    _wait_for_run(client, r2.json()["run_id"], target="completed")

    candidates = client.get(f"/api/v1/sentences/{sentence_id}/candidates").json()[
        "candidates"
    ]
    # The two runs produced two candidates with different source_snapshot_hash.
    fcj_candidates = [
        c for c in candidates if c["style_prompt_version"] == "first-century-jewish-v1"
    ]
    assert len(fcj_candidates) >= 2
    hashes = {c["source_snapshot_hash"] for c in fcj_candidates}
    assert len(hashes) >= 2, (
        f"expected distinct snapshot hashes for different window sizes; got {hashes}"
    )


def test_settings_override_changes_window_size(
    runs_client: tuple[TestClient, FakeWorktreeSpawner],
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    """``CONTEXT_WINDOW_BEFORE`` / ``CONTEXT_WINDOW_AFTER`` env vars are honoured."""
    monkeypatch.setenv("CONTEXT_WINDOW_BEFORE", "1")
    monkeypatch.setenv("CONTEXT_WINDOW_AFTER", "2")
    from api.settings import reset_settings_cache

    reset_settings_cache()

    client, fake = runs_client
    sentence_id = _find_sentence_id(client, 5, 13)

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
    _wait_for_run(client, response.json()["run_id"], target="completed")

    bundle = fake.calls[0].bundle
    assert bundle.adjacent_context is not None
    assert len(bundle.adjacent_context.before) == 1
    assert len(bundle.adjacent_context.after) == 2
