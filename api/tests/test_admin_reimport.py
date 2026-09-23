"""Tests for ``POST /api/v1/admin/reimport``.

Covers Slice 6:
  - test_reimport_succeeds_returns_response
  - test_reimport_csrf_required
  - test_reimport_concurrent_returns_409_import_in_progress
  - test_reimport_with_hash_mismatch_returns_400_with_offending_file
  - test_reimport_detects_orphan_candidates_when_sentence_text_changes
  - test_reimport_with_force_and_delete_disposition_removes_orphans
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from api.admin import service as admin_service
from api.settings import get_commit_hash


@pytest.fixture
def admin_client(imported_db: Path) -> Iterator[TestClient]:
    get_commit_hash.cache_clear()
    from api.main import create_app

    app = create_app()
    test_client = TestClient(app, base_url="http://127.0.0.1:8000")
    yield test_client
    test_client.close()


def _seed_candidate(db_path: Path, *, sentence_id: str, candidate_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO source_snapshots (
                snapshot_hash, sentence_id, source_set_id, fixture_version,
                prompt_version, payload_json, byte_size,
                source_snapshot_canon_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"snap-{candidate_id}",
                sentence_id,
                "SBLGNT_ONLY",
                "fix-test",
                "literal-v1",
                "{}",
                2,
                "v1",
                "2026-05-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO claude_candidates (
                candidate_id, sentence_id, style_prompt_version, source_set_id,
                model, generated_at, candidate_text, source_snapshot_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                sentence_id,
                "literal-v1",
                "SBLGNT_ONLY",
                "test+model",
                "2026-05-01T00:00:00Z",
                "test candidate text for orphan tests",
                f"snap-{candidate_id}",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_reimport_succeeds_returns_response(
    admin_client: TestClient, writable_headers: dict[str, str]
) -> None:
    response = admin_client.post(
        "/api/v1/admin/reimport",
        json={"force": False},
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fixture_version"]
    assert body["sentences_built"] >= 1500
    assert body["red_letter_source_ranges"] == 2
    assert body["style_prompts"] >= 4
    assert isinstance(body["elapsed_ms"], int)


def test_reimport_csrf_required(admin_client: TestClient) -> None:
    """No Origin header → 403; no X-Requested-By → 403."""
    no_origin = admin_client.post("/api/v1/admin/reimport", json={"force": False})
    assert no_origin.status_code == 403
    assert no_origin.json()["code"] == "origin_required"

    no_xrb = admin_client.post(
        "/api/v1/admin/reimport",
        json={"force": False},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert no_xrb.status_code == 403
    assert no_xrb.json()["code"] == "x_requested_by_required"


def test_reimport_concurrent_returns_409_import_in_progress(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two simultaneous POSTs — one wins, one returns 409."""
    real_reimport = admin_service._reimport_sync
    started = threading.Event()
    release = threading.Event()

    def slow_reimport(*args: Any, **kwargs: Any) -> Any:
        started.set()
        release.wait(timeout=5)
        return real_reimport(*args, **kwargs)

    monkeypatch.setattr(admin_service, "_reimport_sync", slow_reimport)

    statuses: list[int] = []

    def first_call() -> None:
        r = admin_client.post(
            "/api/v1/admin/reimport",
            json={"force": False},
            headers=writable_headers,
        )
        statuses.append(r.status_code)

    t1 = threading.Thread(target=first_call)
    t1.start()
    started.wait(timeout=5)
    second = admin_client.post(
        "/api/v1/admin/reimport",
        json={"force": False},
        headers=writable_headers,
    )
    release.set()
    t1.join(timeout=10)
    statuses.append(second.status_code)
    assert sorted(statuses) == [200, 409]
    assert second.status_code == 409
    assert second.json()["code"] == "import_in_progress"


def test_reimport_with_hash_mismatch_returns_400_with_offending_file(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
) -> None:
    """Flip a fixture byte; the reimport returns 400 fixture_hash_mismatch."""
    sblgnt = project_root / "fixtures" / "sblgnt" / "matthew.txt"
    original = sblgnt.read_bytes()
    sblgnt.write_bytes(original + b"\n")
    try:
        response = admin_client.post(
            "/api/v1/admin/reimport",
            json={"force": False},
            headers=writable_headers,
        )
    finally:
        sblgnt.write_bytes(original)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "fixture_hash_mismatch"
    errors = body["details"]["errors"]
    assert any("sblgnt/matthew.txt" in e for e in errors)


def test_reimport_detects_orphan_candidates_when_sentence_text_changes(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
    db_path: Path,
) -> None:
    """Seed a candidate, mutate the SBLGNT fixture so the sentence text
    changes (and the manifest hash too), reimport without force, expect
    409 with ``orphan_summary.affected_candidates`` populated.
    """
    import hashlib

    sentence_row = sqlite3.connect(db_path).execute(
        "SELECT sentence_id FROM sentences ORDER BY chapter, ordinal_in_chapter LIMIT 1"
    ).fetchone()
    sid = sentence_row[0]
    _seed_candidate(db_path, sentence_id=sid, candidate_id=8001)

    # Mutate the SBLGNT fixture — replace the period at the end of 1:1
    # with a comma so the first two verses now form a single sentence
    # (different segmentation → different sentence text for mat-1-1).
    sblgnt = project_root / "fixtures" / "sblgnt" / "matthew.txt"
    original = sblgnt.read_text(encoding="utf-8")
    lines = original.split("\n")
    for idx, line in enumerate(lines):
        if line.strip().startswith("1:1 ") and line.rstrip().endswith("."):
            lines[idx] = line.rstrip()[:-1] + ","
            break
    new_text = "\n".join(lines)
    assert new_text != original, "fixture mutation must actually change the file"
    sblgnt.write_text(new_text, encoding="utf-8")

    new_sha = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
    manifest_path = project_root / "fixtures" / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for f in manifest_payload["files"]:
        if f["name"] == "sblgnt-matthew":
            f["sha256"] = new_sha
            break
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    response = admin_client.post(
        "/api/v1/admin/reimport",
        json={"force": False},
        headers=writable_headers,
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "orphans_detected"
    summary = body["details"]["orphan_summary"]
    assert summary["total"] >= 1
    candidate_ids = [c["candidate_id"] for c in summary["affected_candidates"]]
    assert 8001 in candidate_ids


def test_reimport_with_force_and_delete_disposition_removes_orphans(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
    db_path: Path,
) -> None:
    import hashlib

    sentence_row = sqlite3.connect(db_path).execute(
        "SELECT sentence_id FROM sentences ORDER BY chapter, ordinal_in_chapter LIMIT 1"
    ).fetchone()
    sid = sentence_row[0]
    _seed_candidate(db_path, sentence_id=sid, candidate_id=8002)

    # Mutate SBLGNT so candidate 8002 becomes an orphan via the
    # sentence-text-change path (1:1 ends with comma instead of period
    # → merges with 1:2 into one sentence with different text).
    sblgnt = project_root / "fixtures" / "sblgnt" / "matthew.txt"
    original = sblgnt.read_text(encoding="utf-8")
    lines = original.split("\n")
    for idx, line in enumerate(lines):
        if line.strip().startswith("1:1 ") and line.rstrip().endswith("."):
            lines[idx] = line.rstrip()[:-1] + ","
            break
    new_text = "\n".join(lines)
    assert new_text != original, "fixture mutation must actually change the file"
    sblgnt.write_text(new_text, encoding="utf-8")

    new_sha = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
    manifest_path = project_root / "fixtures" / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for f in manifest_payload["files"]:
        if f["name"] == "sblgnt-matthew":
            f["sha256"] = new_sha
            break
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Force=True with disposition=delete for the orphan. The candidate
    # row + ranking entries referencing it are dropped before the
    # importer runs.
    response = admin_client.post(
        "/api/v1/admin/reimport",
        json={
            "force": True,
            "dispositions": {"candidate:8002": "delete"},
        },
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT candidate_id FROM claude_candidates WHERE candidate_id = ?",
            (8002,),
        ).fetchone()
        assert row is None, "candidate should have been deleted under force+delete"
    finally:
        conn.close()
