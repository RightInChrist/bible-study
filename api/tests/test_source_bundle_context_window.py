"""Source-bundle context-window resolver tests (Slice 3c).

The resolver in :mod:`api.claude.sources` accepts ``wants_context_window``
+ before/after limits; when opt-in it attaches an ``adjacent_context``
block containing N preceding and N following sentences. Tests:

  - opt-in prompt yields populated ``adjacent_context``
  - opt-out prompt yields ``adjacent_context = None``
  - corpus-edge truncation (start of chapter 1, end of chapter 28)
  - window spans chapter boundaries
  - focal sentence does not appear in its own context
  - settings override changes the count
  - adjacent shape carries text_sblgnt + bsb_text + is_red_letter
  - canonical-JSON hash distinguishes different window sizes (dedup)
"""
from __future__ import annotations

from pathlib import Path

from api.claude.schemas import AdjacentSentence, ContextWindow, SourceBundle
from api.claude.sources import (
    canonicalize_bundle,
    hash_bundle,
    resolve_source_bundle,
)
from api.db.connection import open_connection


def _resolve(
    db_path: Path,
    *,
    sentence_id: str,
    wants_context_window: bool,
    before: int = 3,
    after: int = 3,
    source_set_id: str = "BOTH_GREEK",
    prompt_version: str = "first-century-jewish-v1",
) -> SourceBundle:
    conn = open_connection(db_path)
    try:
        return resolve_source_bundle(
            conn,
            sentence_id=sentence_id,
            source_set_id=source_set_id,  # type: ignore[arg-type]
            prompt_version=prompt_version,
            wants_context_window=wants_context_window,
            context_window_before=before,
            context_window_after=after,
        )
    finally:
        conn.close()


def _last_sentence_id_in_chapter(db_path: Path, chapter: int) -> str:
    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT sentence_id FROM sentences WHERE chapter = ? "
            "ORDER BY ordinal_in_chapter DESC LIMIT 1",
            (chapter,),
        ).fetchone()
        assert row is not None, f"no sentences in chapter {chapter}"
        return row["sentence_id"]
    finally:
        conn.close()


def _last_sentence_in_corpus(db_path: Path) -> str:
    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT sentence_id FROM sentences "
            "ORDER BY chapter DESC, ordinal_in_chapter DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        return row["sentence_id"]
    finally:
        conn.close()


def test_bundle_includes_window_when_prompt_opts_in(imported_db: Path) -> None:
    """A mid-chapter sentence with opt-in carries 3 before + 3 after."""
    bundle = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=True)
    assert bundle.adjacent_context is not None
    assert isinstance(bundle.adjacent_context, ContextWindow)
    assert len(bundle.adjacent_context.before) == 3
    assert len(bundle.adjacent_context.after) == 3
    # Each entry is the AdjacentSentence shape with the expected fields.
    for adj in bundle.adjacent_context.before + bundle.adjacent_context.after:
        assert isinstance(adj, AdjacentSentence)
        assert adj.text_sblgnt
        assert isinstance(adj.is_red_letter, bool)
        assert adj.chapter > 0
        assert adj.ordinal_in_chapter > 0


def test_bundle_omits_window_when_prompt_opts_out(imported_db: Path) -> None:
    """Opt-out prompt → ``adjacent_context`` is None and elided from JSON."""
    bundle = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=False)
    assert bundle.adjacent_context is None
    payload = canonicalize_bundle(bundle).decode("utf-8")
    assert "adjacent_context" not in payload


def test_window_truncates_at_corpus_start(imported_db: Path) -> None:
    """Focal at the very first sentence → empty before."""
    bundle = _resolve(imported_db, sentence_id="mat-1-1", wants_context_window=True)
    assert bundle.adjacent_context is not None
    assert bundle.adjacent_context.before == []
    # After is non-empty.
    assert len(bundle.adjacent_context.after) >= 1


def test_window_truncates_at_corpus_end(imported_db: Path) -> None:
    """Focal at the very last sentence → empty after."""
    last_id = _last_sentence_in_corpus(imported_db)
    bundle = _resolve(imported_db, sentence_id=last_id, wants_context_window=True)
    assert bundle.adjacent_context is not None
    assert bundle.adjacent_context.after == []
    assert len(bundle.adjacent_context.before) >= 1


def test_window_spans_chapter_boundary(imported_db: Path) -> None:
    """A focal near the end of chapter 4 has chapter-5 ``after`` neighbours."""
    last_ch4 = _last_sentence_id_in_chapter(imported_db, 4)
    bundle = _resolve(imported_db, sentence_id=last_ch4, wants_context_window=True)
    assert bundle.adjacent_context is not None
    after_chapters = {adj.chapter for adj in bundle.adjacent_context.after}
    assert 5 in after_chapters, (
        f"expected chapter 5 in after-neighbours of {last_ch4!r}; got {after_chapters}"
    )


def test_window_spans_chapter_boundary_before(imported_db: Path) -> None:
    """A focal at the start of chapter 6 has chapter-5 ``before`` neighbours."""
    bundle = _resolve(imported_db, sentence_id="mat-6-1", wants_context_window=True)
    assert bundle.adjacent_context is not None
    before_chapters = {adj.chapter for adj in bundle.adjacent_context.before}
    assert 5 in before_chapters, before_chapters


def test_window_omits_self(imported_db: Path) -> None:
    """The focal sentence_id never appears in its own adjacent_context."""
    bundle = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=True)
    assert bundle.adjacent_context is not None
    ids = {adj.sentence_id for adj in bundle.adjacent_context.before} | {
        adj.sentence_id for adj in bundle.adjacent_context.after
    }
    assert "mat-5-15" not in ids


def test_window_size_configurable(imported_db: Path, monkeypatch) -> None:
    """The before/after parameters change the count (settings override)."""
    bundle1 = _resolve(
        imported_db, sentence_id="mat-5-15", wants_context_window=True, before=1, after=1
    )
    assert bundle1.adjacent_context is not None
    assert len(bundle1.adjacent_context.before) == 1
    assert len(bundle1.adjacent_context.after) == 1

    bundle5 = _resolve(
        imported_db, sentence_id="mat-5-15", wants_context_window=True, before=5, after=5
    )
    assert bundle5.adjacent_context is not None
    assert len(bundle5.adjacent_context.before) == 5
    assert len(bundle5.adjacent_context.after) == 5


def test_adjacent_carries_bsb_and_red_letter(imported_db: Path) -> None:
    """Adjacent entries include readable English (BSB) and red-letter flag."""
    # mat-5-4 is the first beatitude (Matt 5:3); narrator framing precedes.
    bundle = _resolve(imported_db, sentence_id="mat-5-4", wants_context_window=True)
    assert bundle.adjacent_context is not None
    # bsb_text populated for at least one neighbour.
    bsb_present = [
        adj for adj in bundle.adjacent_context.before + bundle.adjacent_context.after
        if adj.bsb_text
    ]
    assert bsb_present, "expected at least one adjacent with bsb_text"
    # is_red_letter is exercised both ways — narrator framing before the
    # first beatitude should be False; subsequent beatitudes True.
    flags = {
        adj.sentence_id: adj.is_red_letter
        for adj in bundle.adjacent_context.before + bundle.adjacent_context.after
    }
    assert any(v is False for v in flags.values()), flags


def test_canonical_hash_changes_when_window_size_changes(imported_db: Path) -> None:
    """Same focal + different window size → different snapshot hash."""
    b1 = _resolve(
        imported_db, sentence_id="mat-5-15", wants_context_window=True, before=1, after=1
    )
    b3 = _resolve(
        imported_db, sentence_id="mat-5-15", wants_context_window=True, before=3, after=3
    )
    h1, _ = hash_bundle(b1)
    h3, _ = hash_bundle(b3)
    assert h1 != h3
    # Re-resolving with the same window is deterministic.
    b3_again = _resolve(
        imported_db, sentence_id="mat-5-15", wants_context_window=True, before=3, after=3
    )
    h3_again, _ = hash_bundle(b3_again)
    assert h3 == h3_again


def test_canonical_hash_changes_when_opt_in_flips(imported_db: Path) -> None:
    """Opt-out vs opt-in for the same focal → different hash (intended).

    Per task: switching a prompt's ``wants_context_window`` from false
    to true invalidates its prior snapshots — that's the content-
    addressing contract.
    """
    out = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=False)
    on = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=True)
    h_out, _ = hash_bundle(out)
    h_on, _ = hash_bundle(on)
    assert h_out != h_on


def test_focal_payload_unchanged_by_context_window(imported_db: Path) -> None:
    """Adding context must not alter the focal sentence's bundle fields."""
    out = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=False)
    on = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=True)
    assert out.sblgnt == on.sblgnt
    assert out.byzantine_verses == on.byzantine_verses
    assert out.verse_range == on.verse_range
    assert out.source_set_id == on.source_set_id
    assert out.fixture_version == on.fixture_version


def test_before_window_is_canonically_ordered(imported_db: Path) -> None:
    """``before`` is in ascending order (earliest first, closest-to-focal last)."""
    bundle = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=True)
    assert bundle.adjacent_context is not None
    before = bundle.adjacent_context.before
    keys = [(adj.chapter, adj.ordinal_in_chapter) for adj in before]
    assert keys == sorted(keys), keys


def test_after_window_is_canonically_ordered(imported_db: Path) -> None:
    """``after`` is in ascending order (closest-to-focal first)."""
    bundle = _resolve(imported_db, sentence_id="mat-5-15", wants_context_window=True)
    assert bundle.adjacent_context is not None
    after = bundle.adjacent_context.after
    keys = [(adj.chapter, adj.ordinal_in_chapter) for adj in after]
    assert keys == sorted(keys), keys
