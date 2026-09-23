"""Ranking API tests (Slice 4 — rank-core).

Coverage from the slice scope:

  - test_get_ranking_returns_empty_when_unset
  - test_put_ranking_creates_with_version_1
  - test_put_ranking_with_stale_version_returns_409_with_current_state
  - test_put_ranking_persists_notes_and_entries_in_one_logical_save
  - test_get_ranking_excludes_hidden_combos_from_available
  - test_post_hidden_combo_persists_and_increments_version
  - test_delete_hidden_combo_re_includes_candidate
  - test_ranking_entries_with_tied_with_above_persist_correctly
  - test_post_tie_break_persists_decision
  - test_csrf_required_on_put_ranking + on_post_hidden_combo
  - test_available_candidates_includes_greek_translations_and_claude
  - test_put_ranking_atomic_under_concurrent_writes
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from api.db.connection import open_connection


def _find_sentence_id(client: TestClient, chapter: int, start_verse: int) -> str:
    body = client.get(f"/api/v1/sentences?chapter={chapter}").json()
    matches = [s for s in body["sentences"] if s["start_verse"] == start_verse]
    assert matches, f"no sentence with start_verse={start_verse} in chapter {chapter}"
    return matches[0]["sentence_id"]


def _seed_claude_candidate(
    db_path: Path,
    *,
    sentence_id: str,
    style_prompt_version: str = "literal-v1",
    source_set_id: str = "BOTH_GREEK",
    model: str = "claude-opus-4-7+xhigh",
    candidate_text: str = "Blessed are the poor in spirit.",
    snapshot_hash: str | None = None,
) -> int:
    """Insert a claude_candidates row directly. Returns the candidate_id.

    Bypasses the runner — this is a read-API smoke fixture, not a
    runner regression test.
    """
    snapshot_hash = snapshot_hash or f"hash-{sentence_id}-{style_prompt_version}-{source_set_id}-{model}"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # source_snapshots row first (FK target)
        conn.execute(
            """
            INSERT OR IGNORE INTO source_snapshots
              (snapshot_hash, sentence_id, source_set_id, fixture_version,
               prompt_version, payload_json, byte_size,
               source_snapshot_canon_version, created_at)
            VALUES (?, ?, ?, 'test', ?, '{}', 2, '1', '2026-05-05T00:00:00Z')
            """,
            (snapshot_hash, sentence_id, source_set_id, style_prompt_version),
        )
        cur = conn.execute(
            """
            INSERT INTO claude_candidates
              (sentence_id, style_prompt_version, source_set_id, model,
               generated_at, candidate_text, source_snapshot_hash, hidden_bool)
            VALUES (?, ?, ?, ?, '2026-05-05T00:00:00Z', ?, ?, 0)
            """,
            (
                sentence_id,
                style_prompt_version,
                source_set_id,
                model,
                candidate_text,
                snapshot_hash,
            ),
        )
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_get_ranking_returns_empty_when_unset(
    client: TestClient,
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.get(f"/api/v1/sentences/{sentence_id}/ranking")
    assert response.status_code == 200
    body = response.json()
    assert body["sentence_id"] == sentence_id
    assert body["version"] == 0
    assert body["entries"] == []
    # available_candidates always includes the open-source columns
    assert len(body["available_candidates"]) >= 5
    names = {c["name"] for c in body["available_candidates"] if c["kind"] == "translation"}
    assert names == {"SBLGNT", "BYZ", "BSB", "BLB", "WEB"}


def test_available_candidates_includes_greek_translations_and_claude(
    client: TestClient,
    imported_db: Path,
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    candidate_id = _seed_claude_candidate(imported_db, sentence_id=sentence_id)
    response = client.get(f"/api/v1/sentences/{sentence_id}/ranking")
    assert response.status_code == 200
    body = response.json()
    claude_cards = [c for c in body["available_candidates"] if c["kind"] == "claude"]
    assert len(claude_cards) == 1
    assert claude_cards[0]["candidate"]["candidate_id"] == candidate_id
    assert claude_cards[0]["candidate"]["candidate_text"].startswith("Blessed")


def test_put_ranking_creates_with_version_1(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    payload = {
        "entries": [
            {
                "position": 1,
                "rank": 1,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
            },
            {
                "position": 2,
                "rank": 2,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "BLB", "verse_range": "5:3"},
            },
        ],
        "notes": "BSB reads better aloud.",
    }
    response = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json=payload,
        headers={**writable_headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["notes"] == "BSB reads better aloud."
    assert len(body["entries"]) == 2
    assert body["entries"][0]["candidate_ref"]["name"] == "BSB"
    assert body["entries"][1]["candidate_ref"]["name"] == "BLB"


def test_put_ranking_with_stale_version_returns_409_with_current_state(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    base = {
        "entries": [
            {
                "position": 1,
                "rank": 1,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
            }
        ],
        "notes": "first",
    }
    r1 = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json=base,
        headers={**writable_headers, "If-Match": "0"},
    )
    assert r1.status_code == 200
    assert r1.json()["version"] == 1

    stale_attempt = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json={**base, "notes": "stale tab"},
        headers={**writable_headers, "If-Match": "0"},
    )
    assert stale_attempt.status_code == 409
    body = stale_attempt.json()
    assert body["code"] == "stale_version"
    details = body["details"]
    assert details["current_version"] == 1
    assert details["current_notes"] == "first"
    assert isinstance(details["current_entries"], list)
    assert len(details["current_entries"]) == 1


def test_put_ranking_persists_notes_and_entries_in_one_logical_save(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    payload = {
        "entries": [
            {
                "position": 1,
                "rank": 1,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "WEB", "verse_range": "5:3"},
            }
        ],
        "notes": "stitched as one save",
    }
    r1 = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json=payload,
        headers={**writable_headers, "If-Match": "0"},
    )
    assert r1.status_code == 200
    v1 = r1.json()["version"]
    assert v1 == 1
    notes_only = {
        "entries": payload["entries"],
        "notes": "notes-edit only",
    }
    r2 = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json=notes_only,
        headers={**writable_headers, "If-Match": str(v1)},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["version"] == 2
    assert body["notes"] == "notes-edit only"
    assert len(body["entries"]) == 1


def test_ranking_entries_with_tied_with_above_persist_correctly(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    payload = {
        "entries": [
            {
                "position": 1,
                "rank": 1,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
            },
            {
                "position": 2,
                "rank": 1,
                "tied_with_above": True,
                "candidate_ref": {"kind": "translation", "name": "WEB", "verse_range": "5:3"},
            },
            {
                "position": 3,
                "rank": 3,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "BLB", "verse_range": "5:3"},
            },
        ],
        "notes": None,
    }
    response = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json=payload,
        headers={**writable_headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    entries = body["entries"]
    assert [e["rank"] for e in entries] == [1, 1, 3]
    assert [e["tied_with_above"] for e in entries] == [False, True, False]


def test_get_ranking_excludes_hidden_combos_from_available(
    client: TestClient,
    imported_db: Path,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    candidate_id = _seed_claude_candidate(imported_db, sentence_id=sentence_id)
    pre = client.get(f"/api/v1/sentences/{sentence_id}/ranking").json()
    assert any(
        c["kind"] == "claude" and c["candidate"]["candidate_id"] == candidate_id
        for c in pre["available_candidates"]
    )

    hide_response = client.post(
        f"/api/v1/sentences/{sentence_id}/hidden-combos",
        json={
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7+xhigh",
        },
        headers={**writable_headers, "If-Match": "0"},
    )
    assert hide_response.status_code == 200, hide_response.text

    post = client.get(f"/api/v1/sentences/{sentence_id}/ranking").json()
    assert not any(
        c["kind"] == "claude" and c["candidate"]["candidate_id"] == candidate_id
        for c in post["available_candidates"]
    )
    assert len(post["hidden_combos"]) == 1


def test_post_hidden_combo_persists_and_increments_version(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.post(
        f"/api/v1/sentences/{sentence_id}/hidden-combos",
        json={
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7+xhigh",
        },
        headers={**writable_headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 1
    assert len(body["hidden_combos"]) == 1


def test_delete_hidden_combo_re_includes_candidate(
    client: TestClient,
    imported_db: Path,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    candidate_id = _seed_claude_candidate(imported_db, sentence_id=sentence_id)
    hide_response = client.post(
        f"/api/v1/sentences/{sentence_id}/hidden-combos",
        json={
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7+xhigh",
        },
        headers={**writable_headers, "If-Match": "0"},
    )
    assert hide_response.status_code == 200
    version_after_hide = hide_response.json()["version"]
    assert version_after_hide == 1

    combo_id = "literal-v1|BOTH_GREEK|claude-opus-4-7+xhigh"
    delete_response = client.delete(
        f"/api/v1/sentences/{sentence_id}/hidden-combos/{combo_id}",
        headers={**writable_headers, "If-Match": str(version_after_hide)},
    )
    assert delete_response.status_code == 200, delete_response.text
    body = delete_response.json()
    assert body["version"] == 2
    assert body["hidden_combos"] == []
    assert any(
        c["kind"] == "claude" and c["candidate"]["candidate_id"] == candidate_id
        for c in body["available_candidates"]
    )


def test_post_tie_break_persists_decision(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.post(
        f"/api/v1/sentences/{sentence_id}/tie-break",
        json={
            "winner": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
            "tied_against": [
                {"kind": "translation", "name": "WEB", "verse_range": "5:3"}
            ],
        },
        headers={**writable_headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sentence_id"] == sentence_id
    assert body["version"] == 1
    assert body["winner"]["name"] == "BSB"
    assert len(body["tied_against"]) == 1


def test_csrf_required_on_put_ranking(
    client: TestClient,
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json={"entries": [], "notes": None},
        headers={"If-Match": "0"},
    )
    # No Origin → 403 origin_required (CSRF middleware)
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


def test_csrf_required_on_post_hidden_combo(
    client: TestClient,
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.post(
        f"/api/v1/sentences/{sentence_id}/hidden-combos",
        json={
            "style_prompt_version": "literal-v1",
            "source_set_id": "BOTH_GREEK",
            "model": "claude-opus-4-7+xhigh",
        },
        headers={"If-Match": "0"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


def test_put_ranking_requires_if_match_header(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sentence_id = _find_sentence_id(client, 5, 3)
    response = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json={"entries": [], "notes": None},
        headers=writable_headers,  # no If-Match
    )
    assert response.status_code == 428
    assert response.json()["code"] == "if_match_required"


def test_put_ranking_atomic_under_concurrent_writes(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """Two PUTs racing on the same sentence — exactly one should win.

    The TestClient is synchronous; we still hit the DB through the same
    server, so concurrency is bounded by GIL + thread interleaving over
    SQLite ``BEGIN IMMEDIATE``. The real OCC enforcement is the
    in-transaction ``SELECT version`` followed by the UPDATE (version
    incremented or DELETE+INSERT). That path is exercised by issuing two
    requests with the same ``If-Match`` and asserting one returns 409.
    """
    sentence_id = _find_sentence_id(client, 5, 3)
    payload = {
        "entries": [
            {
                "position": 1,
                "rank": 1,
                "tied_with_above": False,
                "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
            }
        ],
        "notes": "tab-A",
    }
    results: list[tuple[int, dict]] = []

    def _send(notes: str) -> None:
        response = client.put(
            f"/api/v1/sentences/{sentence_id}/ranking",
            json={**payload, "notes": notes},
            headers={**writable_headers, "If-Match": "0"},
        )
        results.append((response.status_code, response.json()))

    threads = [
        threading.Thread(target=_send, args=("tab-A",)),
        threading.Thread(target=_send, args=("tab-B",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(s for s, _ in results)
    # Exactly one wins (200), exactly one loses (409 stale_version).
    assert statuses == [200, 409], f"unexpected statuses: {statuses}"
    winner = next(b for s, b in results if s == 200)
    loser = next(b for s, b in results if s == 409)
    assert winner["version"] == 1
    assert loser["code"] == "stale_version"
    assert loser["details"]["current_version"] == 1
