"""0001_init: bootstrap schema for Matthew

Revision ID: 0001_init
Revises:
Create Date: 2026-05-03

Creates every table named in PLAN §Data — sentences, words, byzantine_verses,
english_verses, bib_interlinear_words, red_letter_source_ranges,
red_letter_overlays, style_prompts, claude_candidates, source_snapshots,
hidden_combos, rankings, ranking_entries, tie_break_decisions,
generation_runs, generation_run_items, fixture_version. Includes every
CHECK constraint, FK behavior, and index Data named, including
``'disk_full'`` in the ``generation_run_items.error_code`` CHECK enum.
"""
from __future__ import annotations

from alembic import op


revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE fixture_version (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          manifest_hash TEXT NOT NULL,
          manifest_version TEXT NOT NULL,
          segmenter_rule TEXT NOT NULL,
          word_tokenizer_rule TEXT NOT NULL,
          byzantine_mode TEXT NOT NULL,
          source_snapshot_canon_version TEXT NOT NULL,
          last_imported_at TEXT NOT NULL,
          files_imported INT NOT NULL,
          sentences_built INT NOT NULL,
          words_built INT NOT NULL,
          red_letter_source_ranges INT NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sentences (
          sentence_id TEXT PRIMARY KEY,
          chapter INT NOT NULL CHECK (chapter BETWEEN 1 AND 28),
          ordinal_in_chapter INT NOT NULL CHECK (ordinal_in_chapter >= 1),
          text_sblgnt TEXT NOT NULL,
          start_chapter INT NOT NULL,
          start_verse INT NOT NULL,
          end_chapter INT NOT NULL,
          end_verse INT NOT NULL,
          starts_at_verse_boundary INT NOT NULL CHECK (starts_at_verse_boundary IN (0, 1)),
          ends_at_verse_boundary INT NOT NULL CHECK (ends_at_verse_boundary IN (0, 1)),
          word_count INT NOT NULL CHECK (word_count >= 1),
          byte_size INT NOT NULL,
          CHECK (start_chapter <= end_chapter),
          CHECK (start_chapter < end_chapter OR start_verse <= end_verse),
          UNIQUE (chapter, ordinal_in_chapter)
        )
        """
    )
    op.execute("CREATE INDEX idx_sentences_chapter ON sentences (chapter, ordinal_in_chapter)")
    op.execute(
        "CREATE INDEX idx_sentences_verse_range "
        "ON sentences (start_chapter, start_verse, end_chapter, end_verse)"
    )

    op.execute(
        """
        CREATE TABLE words (
          sentence_id TEXT NOT NULL,
          ordinal INT NOT NULL CHECK (ordinal >= 1),
          text TEXT NOT NULL,
          strong_id TEXT,
          byte_start INT NOT NULL,
          byte_end INT NOT NULL,
          CHECK (byte_end > byte_start),
          PRIMARY KEY (sentence_id, ordinal),
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_words_strong ON words (strong_id) WHERE strong_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE byzantine_verses (
          chapter INT NOT NULL CHECK (chapter BETWEEN 1 AND 28),
          verse INT NOT NULL CHECK (verse >= 1),
          text TEXT NOT NULL,
          byte_size INT NOT NULL,
          PRIMARY KEY (chapter, verse)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE english_verses (
          translation TEXT NOT NULL CHECK (translation IN ('BSB', 'BLB', 'WEB')),
          chapter INT NOT NULL CHECK (chapter BETWEEN 1 AND 28),
          verse INT NOT NULL CHECK (verse >= 1),
          text TEXT NOT NULL,
          byte_size INT NOT NULL,
          PRIMARY KEY (translation, chapter, verse)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_english_verses_chapter ON english_verses (chapter, verse, translation)"
    )

    op.execute(
        """
        CREATE TABLE bib_interlinear_words (
          chapter INT NOT NULL,
          verse INT NOT NULL,
          position INT NOT NULL,
          greek_form TEXT NOT NULL,
          strong_id TEXT NOT NULL,
          transliteration TEXT NOT NULL,
          english_gloss TEXT NOT NULL,
          inflected_meaning TEXT,
          PRIMARY KEY (chapter, verse, position)
        )
        """
    )
    op.execute("CREATE INDEX idx_bib_strong ON bib_interlinear_words (strong_id)")
    op.execute(
        "CREATE INDEX idx_bib_greek ON bib_interlinear_words (chapter, verse, greek_form)"
    )

    op.execute(
        """
        CREATE TABLE red_letter_source_ranges (
          source_range_id INTEGER PRIMARY KEY,
          start_sentence_id TEXT NOT NULL,
          start_word_offset INT NOT NULL CHECK (start_word_offset >= 1),
          end_sentence_id TEXT NOT NULL,
          end_word_offset INT NOT NULL,
          origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('berean', 'manual')),
          note TEXT,
          imported_at TEXT NOT NULL,
          FOREIGN KEY (start_sentence_id) REFERENCES sentences(sentence_id),
          FOREIGN KEY (end_sentence_id) REFERENCES sentences(sentence_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_rls_start ON red_letter_source_ranges (start_sentence_id)")
    op.execute("CREATE INDEX idx_rls_end ON red_letter_source_ranges (end_sentence_id)")

    op.execute(
        """
        CREATE TABLE red_letter_overlays (
          overlay_id INTEGER PRIMARY KEY,
          parent_overlay_id INTEGER,
          source_range_id INTEGER,
          operation TEXT NOT NULL CHECK (operation IN ('split', 'merge', 'extend', 'retract', 'reject', 'create')),
          start_sentence_id TEXT,
          start_word_offset INT,
          end_sentence_id TEXT,
          end_word_offset INT,
          rejected INT NOT NULL DEFAULT 0 CHECK (rejected IN (0, 1)),
          origin TEXT NOT NULL CHECK (origin IN ('berean', 'manual')),
          created_at TEXT NOT NULL,
          version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
          FOREIGN KEY (parent_overlay_id) REFERENCES red_letter_overlays(overlay_id),
          FOREIGN KEY (source_range_id) REFERENCES red_letter_source_ranges(source_range_id),
          FOREIGN KEY (start_sentence_id) REFERENCES sentences(sentence_id),
          FOREIGN KEY (end_sentence_id) REFERENCES sentences(sentence_id),
          CHECK (
            (rejected = 1 AND start_sentence_id IS NULL AND end_sentence_id IS NULL)
            OR
            (rejected = 0 AND start_sentence_id IS NOT NULL AND end_sentence_id IS NOT NULL
             AND start_word_offset IS NOT NULL AND end_word_offset IS NOT NULL)
          )
        )
        """
    )
    op.execute("CREATE INDEX idx_rlo_parent ON red_letter_overlays (parent_overlay_id)")
    op.execute("CREATE INDEX idx_rlo_source ON red_letter_overlays (source_range_id)")
    op.execute(
        "CREATE INDEX idx_rlo_start ON red_letter_overlays (start_sentence_id, start_word_offset) "
        "WHERE rejected = 0"
    )

    op.execute(
        """
        CREATE TABLE style_prompts (
          prompt_version TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL,
          requires_greek INT NOT NULL CHECK (requires_greek IN (0, 1)),
          compatible_source_sets TEXT NOT NULL,
          body_path TEXT NOT NULL,
          body_sha256 TEXT NOT NULL,
          imported_at TEXT NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE source_snapshots (
          snapshot_hash TEXT PRIMARY KEY,
          sentence_id TEXT NOT NULL,
          source_set_id TEXT NOT NULL,
          fixture_version TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          byte_size INT NOT NULL,
          source_snapshot_canon_version TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_ss_sentence_combo "
        "ON source_snapshots (sentence_id, source_set_id, prompt_version)"
    )
    op.execute("CREATE INDEX idx_ss_fixture_version ON source_snapshots (fixture_version)")

    op.execute(
        """
        CREATE TABLE claude_candidates (
          candidate_id INTEGER PRIMARY KEY,
          sentence_id TEXT NOT NULL,
          style_prompt_version TEXT NOT NULL,
          source_set_id TEXT NOT NULL CHECK (source_set_id IN (
            'SBLGNT_ONLY', 'BYZ_ONLY', 'BOTH_GREEK',
            'GREEK_PLUS_BIB', 'GREEK_PLUS_BLB', 'GREEK_PLUS_BSB',
            'ENGLISH_ONLY_BSB'
          )),
          model TEXT NOT NULL,
          generated_at TEXT NOT NULL,
          candidate_text TEXT NOT NULL,
          source_snapshot_hash TEXT NOT NULL,
          hidden_bool INT NOT NULL DEFAULT 0 CHECK (hidden_bool IN (0, 1)),
          latency_ms INT,
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id),
          FOREIGN KEY (style_prompt_version) REFERENCES style_prompts(prompt_version),
          FOREIGN KEY (source_snapshot_hash) REFERENCES source_snapshots(snapshot_hash)
        )
        """
    )
    op.execute("CREATE INDEX idx_cc_sentence ON claude_candidates (sentence_id)")
    op.execute(
        "CREATE INDEX idx_cc_combo "
        "ON claude_candidates (sentence_id, style_prompt_version, source_set_id, model)"
    )
    op.execute("CREATE INDEX idx_cc_snapshot ON claude_candidates (source_snapshot_hash)")
    op.execute("CREATE INDEX idx_cc_generated_at ON claude_candidates (generated_at)")

    op.execute(
        """
        CREATE TABLE hidden_combos (
          sentence_id TEXT NOT NULL,
          style_prompt_version TEXT NOT NULL,
          source_set_id TEXT NOT NULL,
          model TEXT NOT NULL,
          hidden_at TEXT NOT NULL,
          version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
          PRIMARY KEY (sentence_id, style_prompt_version, source_set_id, model),
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_hc_sentence ON hidden_combos (sentence_id)")

    op.execute(
        """
        CREATE TABLE rankings (
          sentence_id TEXT PRIMARY KEY,
          notes TEXT,
          status TEXT NOT NULL CHECK (status IN ('partial', 'ranked', 'skipped')) DEFAULT 'partial',
          version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
          updated_at TEXT NOT NULL,
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE ranking_entries (
          sentence_id TEXT NOT NULL,
          position INT NOT NULL CHECK (position >= 1),
          rank INT NOT NULL CHECK (rank >= 1),
          tied_with_above INT NOT NULL DEFAULT 0 CHECK (tied_with_above IN (0, 1)),
          candidate_kind TEXT NOT NULL CHECK (candidate_kind IN ('translation', 'claude')),
          translation_name TEXT,
          translation_verse_range TEXT,
          claude_candidate_id INTEGER,
          PRIMARY KEY (sentence_id, position),
          FOREIGN KEY (sentence_id) REFERENCES rankings(sentence_id) ON DELETE CASCADE,
          FOREIGN KEY (claude_candidate_id) REFERENCES claude_candidates(candidate_id),
          CHECK (
            (candidate_kind = 'translation' AND translation_name IS NOT NULL AND claude_candidate_id IS NULL)
            OR
            (candidate_kind = 'claude' AND claude_candidate_id IS NOT NULL AND translation_name IS NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_re_candidate ON ranking_entries (claude_candidate_id) "
        "WHERE claude_candidate_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE tie_break_decisions (
          sentence_id TEXT NOT NULL,
          resolved_at TEXT NOT NULL,
          winner_kind TEXT NOT NULL CHECK (winner_kind IN ('translation', 'claude')),
          winner_translation_name TEXT,
          winner_translation_verse_range TEXT,
          winner_claude_candidate_id INTEGER,
          tied_against_json TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT 'tie-broken-by-Gavin',
          version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
          PRIMARY KEY (sentence_id, resolved_at),
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id),
          FOREIGN KEY (winner_claude_candidate_id) REFERENCES claude_candidates(candidate_id),
          CHECK (
            (winner_kind = 'translation' AND winner_translation_name IS NOT NULL AND winner_claude_candidate_id IS NULL)
            OR
            (winner_kind = 'claude' AND winner_claude_candidate_id IS NOT NULL AND winner_translation_name IS NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_tbd_sentence ON tie_break_decisions (sentence_id, resolved_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE generation_runs (
          run_id TEXT PRIMARY KEY,
          status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'interrupted')),
          scope_json TEXT NOT NULL,
          style_prompt_version TEXT NOT NULL,
          source_set_id TEXT NOT NULL,
          model TEXT NOT NULL,
          estimated_cost_usd_x10000 INT NOT NULL,
          estimated_input_units INT NOT NULL,
          parent_run_id TEXT,
          created_at TEXT NOT NULL,
          started_at TEXT,
          completed_at TEXT,
          FOREIGN KEY (parent_run_id) REFERENCES generation_runs(run_id),
          FOREIGN KEY (style_prompt_version) REFERENCES style_prompts(prompt_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_gr_status ON generation_runs (status) "
        "WHERE status IN ('running', 'pending')"
    )
    op.execute("CREATE INDEX idx_gr_created_at ON generation_runs (created_at)")
    op.execute(
        "CREATE INDEX idx_gr_parent ON generation_runs (parent_run_id) "
        "WHERE parent_run_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE generation_run_items (
          run_id TEXT NOT NULL,
          ordinal INT NOT NULL CHECK (ordinal >= 1),
          sentence_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'interrupted')),
          candidate_id INTEGER,
          error_code TEXT CHECK (error_code IN (
            'anthropic_api_error', 'anthropic_refusal', 'timeout', 'rate_limit', 'invalid_response', 'disk_full', 'internal'
          )),
          error_message TEXT,
          started_at TEXT,
          completed_at TEXT,
          PRIMARY KEY (run_id, ordinal),
          FOREIGN KEY (run_id) REFERENCES generation_runs(run_id) ON DELETE CASCADE,
          FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id),
          FOREIGN KEY (candidate_id) REFERENCES claude_candidates(candidate_id),
          CHECK (
            (status = 'completed' AND candidate_id IS NOT NULL AND error_code IS NULL)
            OR
            (status = 'failed' AND candidate_id IS NULL AND error_code IS NOT NULL)
            OR
            (status IN ('pending', 'running', 'cancelled', 'interrupted') AND candidate_id IS NULL AND error_code IS NULL)
          )
        )
        """
    )
    op.execute("CREATE INDEX idx_gri_sentence ON generation_run_items (sentence_id)")
    op.execute("CREATE INDEX idx_gri_status ON generation_run_items (run_id, status)")


def downgrade() -> None:
    """Drop every table created in upgrade(). Deliberate, destructive,
    explicit-Alembic-invocation-only — equivalent to ``rm data/bible_study.db``.
    """
    for table in (
        "generation_run_items",
        "generation_runs",
        "tie_break_decisions",
        "ranking_entries",
        "rankings",
        "hidden_combos",
        "claude_candidates",
        "source_snapshots",
        "style_prompts",
        "red_letter_overlays",
        "red_letter_source_ranges",
        "bib_interlinear_words",
        "english_verses",
        "byzantine_verses",
        "words",
        "sentences",
        "fixture_version",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
