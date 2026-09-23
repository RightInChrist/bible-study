"""0002_runs_no_dollar_cost: replace dollar-cost columns with a worktree count.

Revision ID: 0002_runs_no_dollar_cost
Revises: 0001_init
Create Date: 2026-05-03

Slice 3a-redo: generation moved from the Anthropic SDK (per-token billed,
``estimated_cost_usd_x10000`` made sense) to Claude Code worktree
subagents (subscription-bounded, no dollar cost). The relevant cost
proxy is now the worktree count == sentence count in scope.

Forward-only schema change (single-user dev, two existing prod runs):

  - ``generation_runs.estimated_cost_usd_x10000``
      → renamed to ``estimated_worktree_count``
        (the existing column was already an INT; we just repurpose it.
         Old rows' values are meaningless USD-x10000 numbers — left as-is;
         the runner now writes the worktree count and read paths use it
         directly).
  - ``generation_runs.estimated_input_units`` → dropped (had no
    consumer outside the SDK-era cost heuristic).

The ``generation_run_items.error_code`` CHECK enum keeps
``'anthropic_api_error'``, ``'anthropic_refusal'``, and ``'rate_limit'``
even though the worktree runner never emits them — they're harmless
historical values; cleaning the enum is a separate slice once SPEC and
PLAN are reworked (see TODO.md cleanup marker).
"""
from __future__ import annotations

from alembic import op


revision = "0002_runs_no_dollar_cost"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite 3.35 (2021-03-12) added native ALTER TABLE ... DROP COLUMN
    # and RENAME COLUMN; we rely on that rather than the table-rebuild
    # dance Alembic's batch_alter_table normally synthesises.
    op.execute(
        "ALTER TABLE generation_runs "
        "RENAME COLUMN estimated_cost_usd_x10000 TO estimated_worktree_count"
    )
    op.execute("ALTER TABLE generation_runs DROP COLUMN estimated_input_units")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE generation_runs "
        "ADD COLUMN estimated_input_units INT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE generation_runs "
        "RENAME COLUMN estimated_worktree_count TO estimated_cost_usd_x10000"
    )
