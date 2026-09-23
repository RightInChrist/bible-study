"""Chapter-summary slice (Slice 8) tests.

Covers the new code paths end-to-end with the FakeWorktreeSpawner:
  - chapter-bundle resolver structure (narrative_text + red_letter_candidates)
  - top-ranked flagging
  - canonical JSON dedup via snapshot hash
  - run-creation dispatches one worktree
  - completion writes to chapter_summaries
  - read-side endpoints (list + single)
  - text-format candidate falls back to raw english
  - max_runs_per_day cap counts chapter_summary as 1
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.tests.fakes import FakeWorktreeSpawner


def _wait_for_run(
    client: TestClient, run_id: str, *, target: str = "completed", timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == target:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"run {run_id!r} did not reach status={target!r} in {timeout}s; last={last}"
    )


def _post_chapter_summary(
    client: TestClient,
    headers: dict[str, str],
    chapter: int,
    *,
    style: str = "chapter-summary-v1",
) -> dict:
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "chapter_summary", "chapter": chapter},
            "style_prompt_version": style,
            "source_set_id": "CHAPTER_BUNDLE",
            "model": "claude-opus-4-7",
            "effort": "xhigh",
        },
        headers=headers,
    )
    return {"status_code": response.status_code, "body": response.json()}


def _seed_per_sentence_candidates(
    db_path: Path, sentence_ids: list[str], *, structured: bool = True
) -> list[int]:
    """Insert mock per-sentence candidates so the chapter bundle has substrate.

    Each candidate is a structured-JSON candidate matching the
    first-century-jewish-v1 shape so the bundle's red_letter_candidates
    section gets fully populated.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Insert a synthetic source_snapshot row first (FK from claude_candidates).
        snapshot_hash = "synthetic-snapshot-for-chapter-test"
        anchor = conn.execute(
            "SELECT sentence_id FROM sentences ORDER BY chapter LIMIT 1"
        ).fetchone()
        assert anchor is not None
        conn.execute(
            """
            INSERT OR IGNORE INTO source_snapshots (
                snapshot_hash, sentence_id, source_set_id, fixture_version,
                prompt_version, payload_json, byte_size,
                source_snapshot_canon_version, created_at
            ) VALUES (?, ?, 'BOTH_GREEK', 'fv-test', 'first-century-jewish-v1',
                      '{}', 2, 'v1', '2026-05-08T12:00:00Z')
            """,
            (snapshot_hash, anchor["sentence_id"]),
        )
        ids: list[int] = []
        for idx, sentence_id in enumerate(sentence_ids):
            if structured:
                candidate_text = json.dumps(
                    {
                        "english": f"Test cultural-grounded translation of {sentence_id}",
                        "underlying_hypothesis": f"Aramaic substrate test {idx}",
                        "cultural_notes": f"cultural notes test {idx}",
                        "intertexts": [
                            {
                                "reference": "Isa 61:1",
                                "type": "allusion",
                                "note": "test echo",
                            }
                        ],
                        "audience": "the disciples and crowds",
                        "pragmatic_act": "pronouncing a blessing",
                        "confidence": "moderate",
                    },
                    sort_keys=True,
                )
                style = "first-century-jewish-v1"
            else:
                candidate_text = f"plain text translation for {sentence_id}"
                style = "literal-v1"
            cur = conn.execute(
                """
                INSERT INTO claude_candidates (
                    sentence_id, style_prompt_version, source_set_id, model,
                    generated_at, candidate_text, source_snapshot_hash,
                    hidden_bool, latency_ms
                ) VALUES (?, ?, 'BOTH_GREEK', 'claude-opus-4-7+xhigh',
                          ?, ?, ?, 0, 1500)
                """,
                (
                    sentence_id,
                    style,
                    f"2026-05-08T12:00:0{idx}Z",
                    candidate_text,
                    snapshot_hash,
                ),
            )
            ids.append(int(cur.lastrowid or 0))
        conn.commit()
        return ids
    finally:
        conn.close()


def _save_top_ranking(db_path: Path, sentence_id: str, candidate_id: int) -> None:
    """Mark the given claude candidate as rank-1 for the sentence."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO rankings (sentence_id, status, version, updated_at)
            VALUES (?, 'ranked', 1, '2026-05-08T12:00:00Z')
            """,
            (sentence_id,),
        )
        conn.execute(
            """
            INSERT INTO ranking_entries (
                sentence_id, position, rank, tied_with_above,
                candidate_kind, claude_candidate_id
            ) VALUES (?, 1, 1, 0, 'claude', ?)
            """,
            (sentence_id, candidate_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Chapter-bundle resolver
# ---------------------------------------------------------------------------


def test_chapter_bundle_includes_narrative_text_and_red_letter_candidates(
    imported_db: Path,
) -> None:
    from api.claude.sources import build_chapter_bundle
    from api.db.connection import open_connection

    # Seed candidates for some red-letter sentences in chapter 5
    candidate_ids = _seed_per_sentence_candidates(imported_db, ["mat-5-4", "mat-5-5"])
    assert len(candidate_ids) == 2

    conn = open_connection(imported_db)
    try:
        bundle = build_chapter_bundle(conn, chapter=5, prompt_version="chapter-summary-v1")
    finally:
        conn.close()

    assert bundle.chapter == 5
    assert bundle.source_set_id == "CHAPTER_BUNDLE"
    assert bundle.prompt_version == "chapter-summary-v1"
    assert len(bundle.narrative_text) >= 1
    # narrative covers every sentence in the chapter, in order
    sentence_ids = [n.sentence_id for n in bundle.narrative_text]
    assert sentence_ids == sorted(sentence_ids, key=lambda s: int(s.split("-")[2]))
    assert any(n.is_red_letter for n in bundle.narrative_text)
    # red_letter_candidates contains only sentences with non-hidden candidates
    rl_sentence_ids = {s.sentence_id for s in bundle.red_letter_candidates}
    assert rl_sentence_ids == {"mat-5-4", "mat-5-5"}


def test_chapter_bundle_marks_top_ranked_candidates_correctly(
    imported_db: Path,
) -> None:
    from api.claude.sources import build_chapter_bundle
    from api.db.connection import open_connection

    cand_ids = _seed_per_sentence_candidates(imported_db, ["mat-5-4", "mat-5-4"])
    # Two candidates for mat-5-4; mark the second as rank-1.
    _save_top_ranking(imported_db, "mat-5-4", cand_ids[1])

    conn = open_connection(imported_db)
    try:
        bundle = build_chapter_bundle(conn, chapter=5, prompt_version="chapter-summary-v1")
    finally:
        conn.close()

    rl = next(s for s in bundle.red_letter_candidates if s.sentence_id == "mat-5-4")
    assert len(rl.candidates) == 2
    top = [c for c in rl.candidates if c.is_top_ranked]
    not_top = [c for c in rl.candidates if not c.is_top_ranked]
    assert len(top) == 1
    assert top[0].candidate_id == cand_ids[1]
    # Top-ranked is sorted first.
    assert rl.candidates[0].is_top_ranked is True
    assert len(not_top) == 1


def test_chapter_bundle_omits_sentences_without_candidates_from_red_letter_section(
    imported_db: Path,
) -> None:
    from api.claude.sources import build_chapter_bundle
    from api.db.connection import open_connection

    # Seed one candidate; the other red-letter sentences in chapter 5 should
    # be missing from red_letter_candidates.
    _seed_per_sentence_candidates(imported_db, ["mat-5-4"])

    conn = open_connection(imported_db)
    try:
        bundle = build_chapter_bundle(conn, chapter=5, prompt_version="chapter-summary-v1")
    finally:
        conn.close()

    rl_sentence_ids = {s.sentence_id for s in bundle.red_letter_candidates}
    assert rl_sentence_ids == {"mat-5-4"}


def test_chapter_bundle_returns_empty_red_letter_when_chapter_has_no_candidates(
    imported_db: Path,
) -> None:
    from api.claude.sources import build_chapter_bundle
    from api.db.connection import open_connection

    conn = open_connection(imported_db)
    try:
        bundle = build_chapter_bundle(conn, chapter=8, prompt_version="chapter-summary-v1")
    finally:
        conn.close()

    assert bundle.red_letter_candidates == []
    assert len(bundle.narrative_text) >= 1


def test_chapter_bundle_canonical_json_dedups_via_snapshot_hash(
    imported_db: Path,
) -> None:
    from api.claude.sources import (
        build_chapter_bundle,
        canonicalize_chapter_bundle,
        hash_chapter_bundle,
    )
    from api.db.connection import open_connection

    _seed_per_sentence_candidates(imported_db, ["mat-5-4"])

    conn = open_connection(imported_db)
    try:
        b1 = build_chapter_bundle(conn, chapter=5, prompt_version="chapter-summary-v1")
        b2 = build_chapter_bundle(conn, chapter=5, prompt_version="chapter-summary-v1")
    finally:
        conn.close()

    h1, payload1 = hash_chapter_bundle(b1)
    h2, payload2 = hash_chapter_bundle(b2)
    assert h1 == h2
    assert payload1 == payload2

    # JSON parses back without raising and has expected top-level keys.
    parsed = json.loads(canonicalize_chapter_bundle(b1))
    assert parsed["chapter"] == 5
    assert parsed["source_set_id"] == "CHAPTER_BUNDLE"
    assert "narrative_text" in parsed
    assert "red_letter_candidates" in parsed


def test_chapter_bundle_with_unparseable_per_sentence_candidate_falls_back_to_text(
    imported_db: Path,
) -> None:
    from api.claude.sources import build_chapter_bundle
    from api.db.connection import open_connection

    # text-format candidate path
    _seed_per_sentence_candidates(imported_db, ["mat-5-4"], structured=False)

    conn = open_connection(imported_db)
    try:
        bundle = build_chapter_bundle(conn, chapter=5, prompt_version="chapter-summary-v1")
    finally:
        conn.close()

    rl = next(s for s in bundle.red_letter_candidates if s.sentence_id == "mat-5-4")
    assert len(rl.candidates) == 1
    cand = rl.candidates[0]
    # Plain-text candidate: english carries the raw text, structured fields None.
    assert "plain text translation" in cand.english
    assert cand.underlying_hypothesis is None
    assert cand.cultural_notes is None


# ---------------------------------------------------------------------------
# Run creation + dispatch
# ---------------------------------------------------------------------------


def test_create_run_with_chapter_summary_scope_dispatches_one_worktree(
    runs_client,
    writable_headers: dict[str, str],
) -> None:
    client, fake = runs_client
    out = _post_chapter_summary(client, writable_headers, 5)
    assert out["status_code"] == 200, out["body"]
    body = out["body"]
    assert body["items_count"] == 1
    assert body["estimated_worktree_count"] == 1
    assert body["chapter"] == 5
    assert body["sentence_ids"] == []

    final = _wait_for_run(client, body["run_id"], target="completed")
    assert final["items_completed"] == 1
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.chapter_meta is not None
    assert call.chapter_meta.chapter == 5
    assert call.sentence_meta is None


def test_chapter_summary_completion_writes_to_chapter_summaries_table(
    runs_client,
    writable_headers: dict[str, str],
    imported_db: Path,
) -> None:
    client, _fake = runs_client
    # Seed one candidate so candidate_ids_consulted lands non-empty.
    cand_ids = _seed_per_sentence_candidates(imported_db, ["mat-5-4"])

    out = _post_chapter_summary(client, writable_headers, 5)
    assert out["status_code"] == 200
    run_id = out["body"]["run_id"]
    final = _wait_for_run(client, run_id, target="completed")
    assert final["items_completed"] == 1

    list_response = client.get("/api/v1/chapters/5/summaries")
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["chapter"] == 5
    assert len(body["summaries"]) >= 1
    summary = body["summaries"][0]
    assert summary["chapter"] == 5
    assert summary["prompt_version"] == "chapter-summary-v1"
    assert summary["source_set_id"] == "CHAPTER_BUNDLE"
    assert summary["run_id"] == run_id
    assert summary["model"] == "claude-opus-4-7+xhigh"
    assert summary["candidate_ids_consulted"] == cand_ids
    assert summary["hidden_bool"] is False
    # Structured summary fields parse cleanly.
    assert isinstance(summary["summary"], dict)


def test_chapter_summary_invalid_chapter_returns_400(
    runs_client,
    writable_headers: dict[str, str],
) -> None:
    client, _fake = runs_client
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "chapter_summary", "chapter": 99},
            "style_prompt_version": "chapter-summary-v1",
            "source_set_id": "CHAPTER_BUNDLE",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"


def test_chapter_summary_rejects_per_sentence_source_set(
    runs_client,
    writable_headers: dict[str, str],
) -> None:
    """``chapter_summary`` scope MUST come with source_set_id=CHAPTER_BUNDLE."""
    client, _fake = runs_client
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "chapter_summary", "chapter": 5},
            "style_prompt_version": "chapter-summary-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "incompatible_source_set"


def test_chapter_summary_rejects_per_sentence_prompt(
    runs_client,
    writable_headers: dict[str, str],
) -> None:
    """A per-sentence prompt cannot drive a chapter_summary scope."""
    client, _fake = runs_client
    response = client.post(
        "/api/v1/runs",
        json={
            "scope": {"kind": "chapter_summary", "chapter": 5},
            "style_prompt_version": "first-century-jewish-v1",
            "source_set_id": "CHAPTER_BUNDLE",
            "model": "claude-opus-4-7",
        },
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "incompatible_source_set"


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


def test_get_chapter_summaries_returns_latest_first(
    runs_client,
    writable_headers: dict[str, str],
) -> None:
    client, _fake = runs_client
    # Run twice — both should land in the table; latest first.
    out1 = _post_chapter_summary(client, writable_headers, 5)
    _wait_for_run(client, out1["body"]["run_id"], target="completed")
    out2 = _post_chapter_summary(client, writable_headers, 5)
    _wait_for_run(client, out2["body"]["run_id"], target="completed")

    list_response = client.get("/api/v1/chapters/5/summaries")
    assert list_response.status_code == 200
    body = list_response.json()
    assert len(body["summaries"]) == 2
    # generated_at descending
    ts = [s["generated_at"] for s in body["summaries"]]
    assert ts == sorted(ts, reverse=True)


def test_get_chapter_summaries_excludes_hidden_by_default(
    runs_client,
    writable_headers: dict[str, str],
    imported_db: Path,
) -> None:
    client, _fake = runs_client
    out = _post_chapter_summary(client, writable_headers, 5)
    _wait_for_run(client, out["body"]["run_id"], target="completed")
    summary_id = client.get("/api/v1/chapters/5/summaries").json()["summaries"][0][
        "summary_id"
    ]
    # Hide the summary directly (no UI in v1).
    conn = sqlite3.connect(str(imported_db))
    try:
        conn.execute(
            "UPDATE chapter_summaries SET hidden_bool=1 WHERE summary_id=?", (summary_id,)
        )
        conn.commit()
    finally:
        conn.close()
    body = client.get("/api/v1/chapters/5/summaries").json()
    assert body["summaries"] == []


def test_get_chapter_summary_by_id(
    runs_client,
    writable_headers: dict[str, str],
) -> None:
    client, _fake = runs_client
    out = _post_chapter_summary(client, writable_headers, 5)
    _wait_for_run(client, out["body"]["run_id"], target="completed")
    summary = client.get("/api/v1/chapters/5/summaries").json()["summaries"][0]
    sid = summary["summary_id"]
    # Single fetch
    resp = client.get(f"/api/v1/chapters/5/summaries/{sid}")
    assert resp.status_code == 200
    assert resp.json()["summary_id"] == sid

    # Wrong chapter returns 404
    resp_wrong = client.get(f"/api/v1/chapters/6/summaries/{sid}")
    assert resp_wrong.status_code == 404


# ---------------------------------------------------------------------------
# Cap interaction
# ---------------------------------------------------------------------------


def test_chapter_summary_run_count_caps_against_max_runs_per_day(
    runs_client,
    writable_headers: dict[str, str],
    monkeypatch,
) -> None:
    """A chapter_summary run counts as 1 toward MAX_RUNS_PER_DAY."""
    client, _fake = runs_client
    monkeypatch.setenv("MAX_RUNS_PER_DAY", "1")
    from api.settings import reset_settings_cache

    reset_settings_cache()

    out1 = _post_chapter_summary(client, writable_headers, 5)
    assert out1["status_code"] == 200, out1["body"]
    _wait_for_run(client, out1["body"]["run_id"], target="completed")

    out2 = _post_chapter_summary(client, writable_headers, 6)
    assert out2["status_code"] == 400, out2["body"]
    assert out2["body"]["code"] == "max_runs_per_day_exceeded"
