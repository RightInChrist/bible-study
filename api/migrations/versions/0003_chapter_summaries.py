"""0003_chapter_summaries: chapter-level synthesis storage (Slice 8).

Revision ID: 0003_chapter_summaries
Revises: 0002_runs_no_dollar_cost
Create Date: 2026-05-08

CLAUDE.md JTBD #6: chapter summaries are a candidate kind in their own
right — synthesised across a chapter from per-sentence cultural-grounding
candidates plus narrative scaffolding. Stored with full provenance
(chapter, prompt version, model+effort, source-snapshot hash, list of
candidate IDs the synthesis drew on).

The ``source_set_id`` is always the synthetic literal ``'CHAPTER_BUNDLE'``
— signals the bundle shape contains chapter narrative + per-sentence
candidates rather than the per-sentence Greek/translations enum. The
existing ``claude_candidates.source_set_id`` CHECK enum is left untouched;
chapter summaries are their own table with their own constraints.

The chapter-summary scope writes one row per ``generation_run_items`` —
``sentence_id`` is reused as ``chapter:N`` (a sentinel pseudo-ID),
which doesn't collide with the real ``mat-N-M`` sentence IDs and keeps
the existing FK happy by inserting a synthetic sentinel row beforehand
on first use. **However**, since SQLite's CHECK constraint on
``generation_run_items`` requires a non-NULL sentence_id and we don't
want to inject pseudo-rows into ``sentences``, we relax that FK by
making ``sentence_id`` nullable on ``generation_run_items`` and adding
a parallel ``chapter`` column for chapter-summary items. Forward-only.
"""
from __future__ import annotations

from alembic import op


revision = "0003_chapter_summaries"
down_revision = "0002_runs_no_dollar_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``chapter_summaries`` table — one row per generated chapter summary.
    op.execute(
        """
        CREATE TABLE chapter_summaries (
          summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
          chapter INTEGER NOT NULL CHECK (chapter BETWEEN 1 AND 28),
          prompt_version TEXT NOT NULL REFERENCES style_prompts(prompt_version),
          source_set_id TEXT NOT NULL CHECK (source_set_id = 'CHAPTER_BUNDLE'),
          model TEXT NOT NULL,
          source_snapshot_hash TEXT NOT NULL REFERENCES source_snapshots(snapshot_hash),
          summary_text TEXT NOT NULL,
          generated_at TEXT NOT NULL,
          run_id TEXT REFERENCES generation_runs(run_id),
          candidate_ids_consulted TEXT,
          hidden_bool INTEGER NOT NULL DEFAULT 0 CHECK (hidden_bool IN (0, 1))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_chapter_summaries_chapter "
        "ON chapter_summaries (chapter, generated_at DESC)"
    )

    # ``generation_run_items`` adjustments: relax sentence_id NOT NULL and
    # add a ``chapter`` column so chapter-summary items can be tracked
    # without injecting pseudo-rows into ``sentences``. SQLite cannot
    # remove a NOT NULL constraint via ALTER TABLE, so we rebuild the
    # table. The CHECK on (status, candidate_id, error_code) is
    # preserved verbatim from 0001_init.
    op.execute(
        """
        CREATE TABLE generation_run_items_new (
          run_id TEXT NOT NULL,
          ordinal INT NOT NULL CHECK (ordinal >= 1),
          sentence_id TEXT,
          chapter INT,
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
            -- Per-sentence completion: candidate_id must be set.
            (status = 'completed' AND sentence_id IS NOT NULL
             AND candidate_id IS NOT NULL AND error_code IS NULL)
            OR
            -- Chapter-summary completion: chapter is set, candidate_id
            -- is NULL (the chapter_summaries.summary_id is the artefact,
            -- not a claude_candidates row).
            (status = 'completed' AND chapter IS NOT NULL
             AND candidate_id IS NULL AND error_code IS NULL)
            OR
            (status = 'failed' AND candidate_id IS NULL AND error_code IS NOT NULL)
            OR
            (status IN ('pending', 'running', 'cancelled', 'interrupted')
             AND candidate_id IS NULL AND error_code IS NULL)
          ),
          CHECK (
            (sentence_id IS NOT NULL AND chapter IS NULL)
            OR
            (sentence_id IS NULL AND chapter IS NOT NULL AND chapter BETWEEN 1 AND 28)
          )
        )
        """
    )
    op.execute(
        """
        INSERT INTO generation_run_items_new (
          run_id, ordinal, sentence_id, chapter, status, candidate_id,
          error_code, error_message, started_at, completed_at
        )
        SELECT run_id, ordinal, sentence_id, NULL, status, candidate_id,
               error_code, error_message, started_at, completed_at
        FROM generation_run_items
        """
    )
    op.execute("DROP TABLE generation_run_items")
    op.execute("ALTER TABLE generation_run_items_new RENAME TO generation_run_items")
    op.execute("CREATE INDEX idx_gri_sentence ON generation_run_items (sentence_id)")
    op.execute("CREATE INDEX idx_gri_status ON generation_run_items (run_id, status)")
    op.execute(
        "CREATE INDEX idx_gri_chapter ON generation_run_items (chapter) WHERE chapter IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chapter_summaries_chapter")
    op.execute("DROP TABLE IF EXISTS chapter_summaries")
    # Rebuild generation_run_items back to the 0001 shape (NOT NULL on
    # sentence_id, no chapter column). Rows whose sentence_id is NULL
    # are dropped (they're chapter-summary items that have no equivalent
    # in the older schema).
    op.execute(
        """
        CREATE TABLE generation_run_items_old (
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
    op.execute(
        """
        INSERT INTO generation_run_items_old (
          run_id, ordinal, sentence_id, status, candidate_id,
          error_code, error_message, started_at, completed_at
        )
        SELECT run_id, ordinal, sentence_id, status, candidate_id,
               error_code, error_message, started_at, completed_at
        FROM generation_run_items
        WHERE sentence_id IS NOT NULL
        """
    )
    op.execute("DROP TABLE generation_run_items")
    op.execute("ALTER TABLE generation_run_items_old RENAME TO generation_run_items")
    op.execute("CREATE INDEX idx_gri_sentence ON generation_run_items (sentence_id)")
    op.execute("CREATE INDEX idx_gri_status ON generation_run_items (run_id, status)")
