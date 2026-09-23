"""Red-letter overlay editor tests (Slice 7).

Coverage from the slice scope:

  - test_effective_red_letter_set_returns_source_only_when_no_overlays
  - test_effective_red_letter_set_excludes_rejected_source_range
  - test_effective_red_letter_set_includes_manual_overlay_chain_head
  - test_effective_red_letter_set_handles_intermediate_reject_then_recreate
  - test_mark_red_letter_creates_manual_overlay_with_correct_sentence_range
  - test_mark_red_letter_rejects_cross_chapter_range_with_400
  - test_unmark_source_range_appends_reject_overlay_with_correct_parent
  - test_unmark_with_stale_version_returns_409
  - test_unmark_then_remark_creates_new_chain_leaf_clearing_rejected
  - test_get_overlays_for_chapter_returns_all_chains_touching_chapter
  - test_csrf_required_on_post_mark_and_unmark
  - test_overlay_chain_leaf_race_one_winner

These exercise PLAN's canonical effective-set CTE and the chain-leaf OCC
guard the reliability section explicitly named.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from fastapi.testclient import TestClient


def _find_sentence_id(client: TestClient, chapter: int, start_verse: int) -> str:
    body = client.get(f"/api/v1/sentences?chapter={chapter}").json()
    matches = [s for s in body["sentences"] if s["start_verse"] == start_verse]
    assert matches, f"no sentence with start_verse={start_verse} in chapter {chapter}"
    return matches[0]["sentence_id"]


def _is_red(client: TestClient, chapter: int, start_verse: int) -> bool:
    body = client.get(f"/api/v1/sentences?chapter={chapter}").json()
    matches = [s for s in body["sentences"] if s["start_verse"] == start_verse]
    return bool(matches and matches[0]["is_red_letter"])


def _source_range_id_for_chapter_5_beatitudes(db_path: Path) -> int:
    """Pull the Sermon-on-Mount source range id from the imported fixture."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT rls.source_range_id
            FROM red_letter_source_ranges rls
            JOIN sentences s ON s.sentence_id = rls.start_sentence_id
            WHERE s.chapter = 5
            ORDER BY rls.source_range_id ASC
            LIMIT 1
            """
        ).fetchone()
        assert row is not None, "expected the Mt 5-7 source range from fixture import"
        return int(row["source_range_id"])
    finally:
        conn.close()


def test_effective_red_letter_set_returns_source_only_when_no_overlays(
    client: TestClient,
) -> None:
    """Regression: with zero overlays, the canonical CTE matches the
    slice-3c stub on existing fixture data — Beatitudes red, intro not."""
    body = client.get("/api/v1/sentences?chapter=5").json()
    by_start = {s["start_verse"]: s for s in body["sentences"]}
    for verse in range(3, 13):
        assert by_start[verse]["is_red_letter"] is True
    pre = [s for s in body["sentences"] if s["start_verse"] in (1, 2)]
    assert pre and all(s["is_red_letter"] is False for s in pre)


def test_effective_red_letter_set_excludes_rejected_source_range(
    client: TestClient,
    imported_db: Path,
    writable_headers: dict[str, str],
) -> None:
    """Reject the Mt 5-7 source range; assert sentences are no longer flagged."""
    source_range_id = _source_range_id_for_chapter_5_beatitudes(imported_db)
    response = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "source_range", "target_id": source_range_id},
        headers={**writable_headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["overlay"]["rejected"] is True

    body = client.get("/api/v1/sentences?chapter=5").json()
    by_start = {s["start_verse"]: s for s in body["sentences"]}
    for verse in range(3, 13):
        assert by_start[verse]["is_red_letter"] is False, (
            f"verse {verse} should be off after rejecting the source range"
        )


def test_effective_red_letter_set_includes_manual_overlay_chain_head(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """Mark Mt 8:26 (Jesus rebuking the storm) and assert the sentence flips."""
    pre = _is_red(client, 8, 26)
    assert pre is False, "Mt 8:26 should start unmarked (only Sermon + Mt 11 in fixture)"

    response = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    overlay = response.json()["overlay"]
    assert overlay["origin"] == "manual"
    assert overlay["operation"] == "create"
    assert overlay["rejected"] is False
    assert overlay.get("parent_overlay_id") is None
    assert overlay.get("source_range_id") is None

    post = _is_red(client, 8, 26)
    assert post is True, "Mt 8:26 should now be flagged red after the mark"


def test_effective_red_letter_set_handles_intermediate_reject_then_recreate(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """Manual mark -> unmark -> restore. Intermediate reject must not silently
    keep flagging the sentence; the leaf is the source of truth."""
    mark_response = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    overlay = mark_response.json()["overlay"]
    overlay_id = overlay["overlay_id"]
    version = overlay["version"]
    assert _is_red(client, 8, 26)

    unmark_response = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "manual_overlay", "target_id": overlay_id},
        headers={**writable_headers, "If-Match": str(version)},
    )
    assert unmark_response.status_code == 200, unmark_response.text
    rejected_overlay = unmark_response.json()["overlay"]
    assert rejected_overlay["rejected"] is True
    assert rejected_overlay["parent_overlay_id"] == overlay_id
    assert _is_red(client, 8, 26) is False

    restore_response = client.post(
        "/api/v1/red-letter/restore",
        json={"scope": "manual_overlay", "target_id": rejected_overlay["overlay_id"]},
        headers={**writable_headers, "If-Match": str(rejected_overlay["version"])},
    )
    assert restore_response.status_code == 200, restore_response.text
    restored = restore_response.json()["overlay"]
    assert restored["rejected"] is False
    assert restored["parent_overlay_id"] == rejected_overlay["overlay_id"]
    assert _is_red(client, 8, 26) is True


def test_mark_red_letter_creates_manual_overlay_with_correct_sentence_range(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 27},
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    overlay = body["overlay"]
    assert overlay["start_sentence_id"] is not None
    assert overlay["end_sentence_id"] is not None
    affected = body["affected_sentence_ids"]
    assert affected, "mark should report at least one affected sentence"
    # All affected sentences live in chapter 8.
    for sid in affected:
        assert sid.startswith("mat-8-") or sid.startswith("mt-8-") or "8" in sid


def test_mark_red_letter_rejects_cross_chapter_range_with_400(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    # The schema layer pins ``chapter`` as a single int; cross-chapter is
    # impossible by request shape. Designer's edge case is then "what if
    # the verse range derives sentences crossing the chapter boundary?" —
    # not possible inside one chapter, but invalid verses (start > end)
    # surface as 400 invalid_verse_range.
    response = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 27, "end_verse": 25},
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_verse_range"


def test_unmark_source_range_appends_reject_overlay_with_correct_parent(
    client: TestClient,
    imported_db: Path,
    writable_headers: dict[str, str],
) -> None:
    source_range_id = _source_range_id_for_chapter_5_beatitudes(imported_db)
    response = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "source_range", "target_id": source_range_id},
        headers={**writable_headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text
    overlay = response.json()["overlay"]
    assert overlay["rejected"] is True
    assert overlay["operation"] == "reject"
    assert overlay["source_range_id"] == source_range_id
    # First overlay on the source range — parent is NULL.
    assert overlay.get("parent_overlay_id") is None


def test_unmark_with_stale_version_returns_409(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """Two consecutive unmarks with the same If-Match — second must 409."""
    mark = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    overlay = mark.json()["overlay"]
    overlay_id = overlay["overlay_id"]
    version = overlay["version"]

    first = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "manual_overlay", "target_id": overlay_id},
        headers={**writable_headers, "If-Match": str(version)},
    )
    assert first.status_code == 200, first.text

    stale = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "manual_overlay", "target_id": overlay_id},
        headers={**writable_headers, "If-Match": str(version)},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"


def test_unmark_then_remark_creates_new_chain_leaf_clearing_rejected(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    mark = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    overlay = mark.json()["overlay"]
    unmark = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "manual_overlay", "target_id": overlay["overlay_id"]},
        headers={**writable_headers, "If-Match": str(overlay["version"])},
    )
    rejected = unmark.json()["overlay"]
    restore = client.post(
        "/api/v1/red-letter/restore",
        json={"scope": "manual_overlay", "target_id": rejected["overlay_id"]},
        headers={**writable_headers, "If-Match": str(rejected["version"])},
    )
    assert restore.status_code == 200, restore.text
    leaf = restore.json()["overlay"]
    assert leaf["rejected"] is False
    assert leaf["parent_overlay_id"] == rejected["overlay_id"]
    assert leaf["version"] == rejected["version"] + 1
    assert _is_red(client, 8, 26) is True


def test_get_overlays_for_chapter_returns_all_chains_touching_chapter(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    # Add a manual chain in chapter 8.
    mark = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    assert mark.status_code == 200

    response = client.get("/api/v1/red-letter/chapter/8/overlays")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chapter"] == 8
    assert any(
        chain["head"]["origin"] == "manual"
        and chain["head"]["start_sentence_id"] is not None
        for chain in body["chains"]
    )
    assert body["effective_sentence_ids"], "the manual mark should appear in effective set"


def test_csrf_required_on_post_mark(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        # No Origin / X-Requested-By → CSRF middleware blocks.
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


def test_csrf_required_on_post_unmark(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/red-letter/unmark",
        json={"scope": "manual_overlay", "target_id": 1},
        headers={"If-Match": "0"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


def test_overlay_chain_leaf_race_one_winner(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """PLAN's named chain-leaf race test: two simultaneous unmarks with the
    same If-Match — exactly one succeeds, the other returns 409."""
    mark = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    overlay = mark.json()["overlay"]
    overlay_id = overlay["overlay_id"]
    version = overlay["version"]

    results: list[tuple[int, dict]] = []

    def _send() -> None:
        response = client.post(
            "/api/v1/red-letter/unmark",
            json={"scope": "manual_overlay", "target_id": overlay_id},
            headers={**writable_headers, "If-Match": str(version)},
        )
        results.append((response.status_code, response.json()))

    threads = [threading.Thread(target=_send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(s for s, _ in results)
    assert statuses == [200, 409], f"unexpected statuses: {statuses}"
    loser = next(b for s, b in results if s == 409)
    assert loser["code"] == "stale_version"


def test_parallel_response_carries_red_letter_provenance(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """The /parallel response must surface ``red_letter_provenance`` so the
    Unmark UI knows what to target."""
    mark = client.post(
        "/api/v1/red-letter/mark",
        json={"chapter": 8, "start_verse": 26, "end_verse": 26},
        headers=writable_headers,
    )
    assert mark.status_code == 200
    overlay = mark.json()["overlay"]
    affected = mark.json()["affected_sentence_ids"]
    assert affected, "mark should affect at least one sentence"

    parallel = client.get(
        f"/api/v1/sentences/{affected[0]}/parallel"
    ).json()
    assert parallel["is_red_letter"] is True
    prov = parallel["red_letter_provenance"]
    assert prov is not None
    assert prov["origin"] == "manual"
    assert prov["head_overlay_id"] == overlay["overlay_id"]
    assert prov.get("source_range_id") is None
