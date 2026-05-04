"""Sentence read API tests.

Slice scope:
  - test_get_sentence_parallel_returns_all_columns_for_matthew_5_3
  - test_get_sentences_in_chapter_5_returns_beatitudes
  - test_starts_at_verse_boundary_flag_set_correctly_for_matthew_5_3

"Matthew 5:3" here means the SBLGNT sentence whose start_verse=3 — i.e.
the first beatitude ("Blessed are the poor in spirit"). The segmenter
splits Matthew 5:1 into multiple sentences so the per-chapter ordinal of
the first beatitude is not 3; we look it up by start_verse.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _find_sentence_by_start_verse(client: TestClient, chapter: int, start_verse: int) -> dict:
    body = client.get(f"/api/v1/sentences?chapter={chapter}").json()
    matches = [s for s in body["sentences"] if s["start_verse"] == start_verse]
    assert matches, f"no sentence found with start_verse={start_verse} in chapter {chapter}"
    return matches[0]


def test_get_sentences_in_chapter_5_returns_beatitudes(client: TestClient) -> None:
    response = client.get("/api/v1/sentences?chapter=5")
    assert response.status_code == 200
    body = response.json()
    assert body["chapter"] == 5
    sentences = body["sentences"]

    # Each Beatitude (verses 3..12) should appear as at least one SBLGNT sentence.
    start_verses_present = {s["start_verse"] for s in sentences}
    for verse in range(3, 13):
        assert verse in start_verses_present, f"Matthew 5:{verse} missing from chapter list"

    # The first beatitude (verse 3) must include some preview text from the
    # canonical Greek opening word.
    poor_in_spirit = _find_sentence_by_start_verse(client, 5, 3)
    assert "Μακάριοι" in poor_in_spirit["text_preview"]


def test_starts_at_verse_boundary_flag_set_correctly_for_matthew_5_3(client: TestClient) -> None:
    """Each beatitude is a clean case: it begins and ends inside its own verse,
    so both verse-boundary flags should be true."""
    sentence = _find_sentence_by_start_verse(client, 5, 3)
    assert sentence["starts_at_verse_boundary"] is True
    assert sentence["ends_at_verse_boundary"] is True
    # Sanity: Matthew 5:1 itself splits across the `·` mid-verse — the second
    # SBLGNT sentence in chapter 5 starts mid-verse 1.
    body = client.get("/api/v1/sentences?chapter=5").json()
    chapter_starts = [s for s in body["sentences"] if s["start_verse"] == 1]
    # At least one chapter-5-verse-1 sentence does NOT start at the verse boundary.
    flags = [s["starts_at_verse_boundary"] for s in chapter_starts]
    assert flags.count(False) >= 1, "expected the second clause of Matthew 5:1 to be mid-verse"


def test_get_sentence_parallel_returns_all_columns_for_matthew_5_3(client: TestClient) -> None:
    poor_in_spirit = _find_sentence_by_start_verse(client, 5, 3)
    sentence_id = poor_in_spirit["sentence_id"]
    response = client.get(f"/api/v1/sentences/{sentence_id}/parallel")
    assert response.status_code == 200
    body = response.json()

    assert body["sentence_id"] == sentence_id
    assert body["start_verse"] == 3
    assert body["end_verse"] == 3
    assert body["starts_at_verse_boundary"] is True
    assert body["ends_at_verse_boundary"] is True

    # SBLGNT text content
    assert "Μακάριοι" in body["text_sblgnt"]
    assert "πτωχοὶ" in body["text_sblgnt"]

    # Byzantine — verse 3 covered. The upstream we ingest
    # (``byztxt/byzantine-majority-text`` / ``csv-unicode/strongs/no-parsing``)
    # is lowercase + unaccented by design, so we assert the canonical
    # lowercase form of "Blessed" rather than the SBLGNT polytonic form.
    assert any(v["chapter"] == 5 and v["verse"] == 3 for v in body["byzantine"])
    assert any("μακαριοι" in v["text"].lower() for v in body["byzantine"])

    # English columns: BSB + BLB + WEB all present
    translations_seen = {col["translation"] for col in body["english"]}
    assert translations_seen == {"BSB", "BLB", "WEB"}
    for col in body["english"]:
        assert col["verses"], f"{col['translation']} returned no verses"
        assert all(v["chapter"] == 5 and v["verse"] == 3 for v in col["verses"])

    # BIB interlinear — verse 3 has 12 word rows
    bib_words = [w for w in body["bib_interlinear"] if w["verse"] == 3]
    assert len(bib_words) == 12
    assert any(w["greek_form"].lower().startswith("μακ") for w in bib_words)
    assert any(w["strong_id"] == "G3107" for w in bib_words)


def test_sentence_parallel_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/api/v1/sentences/mat-99-99/parallel")
    assert response.status_code == 404
    body = response.json()
    # Architect §HTTP API: errors return ``ErrorResponse {code, message,
    # details}`` — never nested under ``detail``.
    assert set(body.keys()) == {"code", "message", "details"}
    assert body["code"] == "sentence_not_found"
    assert isinstance(body["message"], str) and body["message"]
    # Designer's "Sentence not found" empty state shows the current fixture
    # version so Gavin can tell whether the deep-link is stale.
    details = body["details"]
    assert details["sentence_id"] == "mat-99-99"
    assert "fixture_version" in details
    assert details["fixture_version"]  # populated post-import


def test_chapter_5_red_letter_membership_matches_beatitudes(client: TestClient) -> None:
    """Designer Flow 1 step 4 needs ``is_red_letter`` on every list row.

    The fixture marks Matthew 5:3-12 (Beatitudes) red. The introduction
    (5:1-2) is not red — the segmenter splits 5:1 into multiple sentences,
    none of which should carry the flag.
    """
    body = client.get("/api/v1/sentences?chapter=5").json()
    by_start = {s["start_verse"]: s for s in body["sentences"]}
    # Each Beatitude (verses 3..12) is flagged red.
    for verse in range(3, 13):
        assert by_start[verse]["is_red_letter"] is True, (
            f"Matthew 5:{verse} should be red-letter (Beatitudes)"
        )
    # The chapter-5 introduction (verses 1..2) is not red.
    pre_beatitudes = [s for s in body["sentences"] if s["start_verse"] in (1, 2)]
    assert pre_beatitudes, "expected at least one sentence in 5:1-5:2"
    for s in pre_beatitudes:
        assert s["is_red_letter"] is False, (
            f"sentence {s['sentence_id']} (5:{s['start_verse']}) should not be red"
        )


def test_parallel_response_carries_is_red_letter(client: TestClient) -> None:
    """``GET /sentences/{id}/parallel`` carries the same flag for the
    sentence detail drawer (Designer Flow 1 step 5)."""
    body = client.get("/api/v1/sentences?chapter=5").json()
    poor_in_spirit = next(s for s in body["sentences"] if s["start_verse"] == 3)
    response = client.get(f"/api/v1/sentences/{poor_in_spirit['sentence_id']}/parallel")
    assert response.status_code == 200
    assert response.json()["is_red_letter"] is True
