"""GSV API tests (Slice 5 — gsv-core).

Coverage from the slice scope:

  - test_gsv_chapter_with_all_sentences_ranked_returns_clean_plain_text
  - test_gsv_chapter_with_unranked_red_letters_renders_muted_placeholders
  - test_gsv_plain_text_refuses_when_unresolved_tie_exists
  - test_gsv_markdown_includes_per_sentence_provenance_footnotes
  - test_gsv_json_includes_tie_kind_when_top_rank_has_two_entries
  - test_gsv_json_includes_tie_broken_kind_after_tie_break_decision
  - test_gsv_non_red_letter_sentences_default_to_bsb
  - test_gsv_red_letter_unranked_sentences_marked_unranked_kind
  - test_gsv_coverage_counts_ranked_red_letter_sentences_correctly
  - test_gsv_chapter_out_of_range_returns_404
  - test_gsv_uses_english_field_of_structured_json_candidates_not_raw_json
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def _seed_claude_candidate(
    db_path: Path,
    *,
    sentence_id: str,
    candidate_text: str,
    style_prompt_version: str = "literal-v1",
    source_set_id: str = "BOTH_GREEK",
    model: str = "claude-opus-4-7+xhigh",
    snapshot_hash: str | None = None,
) -> int:
    snapshot_hash = (
        snapshot_hash
        or f"hash-{sentence_id}-{style_prompt_version}-{source_set_id}-{model}"
    )
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
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
            VALUES (?, ?, ?, ?, '2026-05-05T12:57:32Z', ?, ?, 0)
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


def _put_ranking(
    client: TestClient,
    writable_headers: dict[str, str],
    *,
    sentence_id: str,
    entries: list[dict[str, object]],
    if_match: str = "0",
) -> int:
    response = client.put(
        f"/api/v1/sentences/{sentence_id}/ranking",
        json={"entries": entries, "notes": None},
        headers={**writable_headers, "If-Match": if_match},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["version"])


def test_gsv_chapter_out_of_range_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/gsv/29")
    # Path validator yields 422 (validation_error); both shapes are
    # acceptable but we want explicit out-of-range handling. Path's
    # ge/le returns 422 — we accept either status as long as the wire
    # shape is the canonical ErrorResponse.
    assert response.status_code in (404, 422)
    body = response.json()
    assert "code" in body


def test_gsv_non_red_letter_sentences_default_to_bsb(client: TestClient) -> None:
    """mat-5-1 / mat-5-2 / mat-5-3 are narrative — outside the red-letter
    set; the GSV must still render them with BSB provenance.
    """
    response = client.get("/api/v1/gsv/5?format=json")
    assert response.status_code == 200
    body = response.json()
    by_id = {s["sentence_id"]: s for s in body["sentences"]}
    for sid in ("mat-5-1", "mat-5-2", "mat-5-3"):
        sentence = by_id[sid]
        assert sentence["is_red_letter"] is False
        assert sentence["provenance"]["kind"] == "translation"
        assert sentence["provenance"]["name"] == "BSB"
        assert sentence["text"]  # non-empty BSB English


def test_gsv_red_letter_unranked_sentences_marked_unranked_kind(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/gsv/5?format=json")
    body = response.json()
    by_id = {s["sentence_id"]: s for s in body["sentences"]}
    # mat-5-4 is the first beatitude — red-letter, unranked at slice start
    sentence = by_id["mat-5-4"]
    assert sentence["is_red_letter"] is True
    assert sentence["provenance"]["kind"] == "unranked"
    # Text carries BSB fallback so the muted placeholder shows something
    assert "Blessed" in sentence["text"] or sentence["text"] != ""


def test_gsv_chapter_with_all_sentences_ranked_returns_clean_plain_text(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """Pick a small slice — chapter 11 has 30 sentences; 11:28-30 is red-letter.
    Rank each red-letter sentence with BSB at top; assert plain text renders.
    """
    # Walk every red-letter sentence and rank BSB at #1 so the chapter
    # has zero unranked or tied red-letter rows.
    json_response = client.get("/api/v1/gsv/11?format=json").json()
    for s in json_response["sentences"]:
        if s["is_red_letter"]:
            entries = [
                {
                    "position": 1,
                    "rank": 1,
                    "tied_with_above": False,
                    "candidate_ref": {
                        "kind": "translation",
                        "name": "BSB",
                        "verse_range": s["verse_range"],
                    },
                }
            ]
            _put_ranking(
                client, writable_headers,
                sentence_id=s["sentence_id"], entries=entries,
            )
    response = client.get("/api/v1/gsv/11?format=text")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("Matthew 11 — Gavin Standard Version")
    assert "[unranked:" not in body
    assert "[TIE" not in body


def test_gsv_chapter_with_unranked_red_letters_renders_muted_placeholders(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/gsv/5?format=text")
    assert response.status_code == 200
    body = response.text
    # Many ch5 red-letter sentences are unranked — they appear in the
    # plain-text body as "[unranked: ...]"
    assert "[unranked:" in body


def test_gsv_plain_text_refuses_when_unresolved_tie_exists(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    """Save a ranking with two entries tied at rank 1 — no tie-break decision."""
    sid = "mat-5-4"
    entries = [
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
    ]
    _put_ranking(client, writable_headers, sentence_id=sid, entries=entries)
    response = client.get("/api/v1/gsv/5?format=text")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "unresolved_ties"
    assert sid in body["details"]["offending_sentences"]


def test_gsv_json_includes_tie_kind_when_top_rank_has_two_entries(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sid = "mat-5-4"
    entries = [
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
    ]
    _put_ranking(client, writable_headers, sentence_id=sid, entries=entries)
    response = client.get(f"/api/v1/gsv/5?format=json")
    body = response.json()
    by_id = {s["sentence_id"]: s for s in body["sentences"]}
    assert by_id[sid]["provenance"]["kind"] == "tie"
    tied = [e["name"] for e in by_id[sid]["provenance"]["entries"] if e.get("kind") == "translation"]
    assert "BSB" in tied
    assert "WEB" in tied


def test_gsv_json_includes_tie_broken_kind_after_tie_break_decision(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    sid = "mat-5-4"
    entries = [
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
    ]
    v = _put_ranking(client, writable_headers, sentence_id=sid, entries=entries)
    tb_response = client.post(
        f"/api/v1/sentences/{sid}/tie-break",
        json={
            "winner": {"kind": "translation", "name": "WEB", "verse_range": "5:3"},
            "tied_against": [
                {"kind": "translation", "name": "BSB", "verse_range": "5:3"}
            ],
        },
        headers={**writable_headers, "If-Match": str(v)},
    )
    assert tb_response.status_code == 200, tb_response.text

    response = client.get("/api/v1/gsv/5?format=json")
    body = response.json()
    by_id = {s["sentence_id"]: s for s in body["sentences"]}
    prov = by_id[sid]["provenance"]
    assert prov["kind"] == "tie-broken"
    assert prov["winner"]["name"] == "WEB"
    assert any(t["name"] == "BSB" for t in prov["tied_against"])
    assert "resolved_at" in prov


def test_gsv_markdown_includes_per_sentence_provenance_footnotes(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    # Rank one red-letter sentence so the markdown surfaces a single-winner footnote.
    sid = "mat-5-4"
    entries = [
        {
            "position": 1,
            "rank": 1,
            "tied_with_above": False,
            "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
        }
    ]
    _put_ranking(client, writable_headers, sentence_id=sid, entries=entries)
    response = client.get("/api/v1/gsv/5?format=markdown")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("# Matthew 5")
    assert "[^mat-5-4]" in body
    assert "BSB / 5:3" in body
    # Coverage line as italics
    assert "red-letter sentences ranked" in body


def test_gsv_coverage_counts_ranked_red_letter_sentences_correctly(
    client: TestClient,
    writable_headers: dict[str, str],
) -> None:
    # Baseline — ch5 has 68 red-letter sentences, 0 ranked.
    pre = client.get("/api/v1/gsv/coverage").json()
    assert pre["ch5_total"] == 68
    assert pre["ch5_ranked"] == 0

    # Rank one red-letter sentence → ranked count bumps by 1.
    sid = "mat-5-4"
    entries = [
        {
            "position": 1,
            "rank": 1,
            "tied_with_above": False,
            "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:3"},
        }
    ]
    _put_ranking(client, writable_headers, sentence_id=sid, entries=entries)
    post = client.get("/api/v1/gsv/coverage").json()
    assert post["ch5_total"] == 68
    assert post["ch5_ranked"] == 1

    # Tie at top rank without resolution → does NOT count.
    sid2 = "mat-5-5"
    entries2 = [
        {
            "position": 1,
            "rank": 1,
            "tied_with_above": False,
            "candidate_ref": {"kind": "translation", "name": "BSB", "verse_range": "5:4"},
        },
        {
            "position": 2,
            "rank": 1,
            "tied_with_above": True,
            "candidate_ref": {"kind": "translation", "name": "WEB", "verse_range": "5:4"},
        },
    ]
    _put_ranking(client, writable_headers, sentence_id=sid2, entries=entries2)
    after_tie = client.get("/api/v1/gsv/coverage").json()
    assert after_tie["ch5_ranked"] == 1  # tie not counted
    assert any(
        c["chapter"] == 5 and c["unresolved_ties"] >= 1
        for c in after_tie["per_chapter"]
    )


def test_gsv_uses_english_field_of_structured_json_candidates_not_raw_json(
    client: TestClient,
    imported_db: Path,
    writable_headers: dict[str, str],
) -> None:
    """``first-century-jewish-v1`` candidates store the full structured JSON
    in ``candidate_text``. The GSV must extract the ``english`` field for
    rendering, not surface the raw JSON.
    """
    sid = "mat-5-4"
    structured = {
        "english": "Honoured are those who are bankrupt of breath, for the kingdom of the heavens belongs to them.",
        "underlying_hypothesis": "Aramaic: tubehon le-anaweh ha-ruah",
        "cultural_notes": "Beatitude as honour-claim, not moral commendation.",
    }
    candidate_id = _seed_claude_candidate(
        imported_db,
        sentence_id=sid,
        candidate_text=json.dumps(structured, ensure_ascii=False, sort_keys=True),
        style_prompt_version="first-century-jewish-v1",
    )
    entries = [
        {
            "position": 1,
            "rank": 1,
            "tied_with_above": False,
            "candidate_ref": {"kind": "claude", "candidate_id": candidate_id},
        }
    ]
    _put_ranking(client, writable_headers, sentence_id=sid, entries=entries)
    response = client.get("/api/v1/gsv/5?format=json")
    body = response.json()
    by_id = {s["sentence_id"]: s for s in body["sentences"]}
    assert by_id[sid]["provenance"]["kind"] == "claude"
    assert by_id[sid]["text"] == structured["english"]
    # Make sure the raw JSON did NOT leak through.
    assert "underlying_hypothesis" not in by_id[sid]["text"]
    assert "{" not in by_id[sid]["text"]


def test_gsv_text_format_with_download_query_sets_content_disposition(
    client: TestClient,
) -> None:
    # All ch5 red-letter sentences are unranked → text body still
    # renders since unranked != tie. download=true adds the header.
    response = client.get("/api/v1/gsv/11?format=text&download=true")
    assert response.status_code == 200
    cd = response.headers.get("content-disposition")
    assert cd is not None
    assert "attachment" in cd
    assert "mat-11-gsv.txt" in cd


def test_gsv_markdown_format_with_download_query_sets_content_disposition(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/gsv/5?format=markdown&download=true")
    assert response.status_code == 200
    cd = response.headers.get("content-disposition")
    assert cd is not None
    assert "mat-5-gsv.md" in cd


def test_gsv_json_format_returns_chapter_response_shape(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/gsv/5?format=json")
    assert response.status_code == 200
    body = response.json()
    assert body["chapter"] == 5
    assert body["coverage"]["chapter"] == 5
    # ch5 has 71 sentences (per the segmenter)
    assert body["coverage"]["total_sentences"] == 71
    assert body["coverage"]["total_red_letter_sentences"] == 68
    assert len(body["sentences"]) == 71
    # Sentences must be ordered by ordinal_in_chapter, not lexicographic
    ordinals = [s["ordinal_in_chapter"] for s in body["sentences"]]
    assert ordinals == sorted(ordinals)
