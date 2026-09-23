"""Schema migration coverage.

Slice scope: test_alembic_migration_0001_creates_all_tables.
Data §Test coverage also calls for asserting PRAGMA foreign_keys is on.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_EXPECTED_TABLES = frozenset(
    {
        "alembic_version",
        "fixture_version",
        "sentences",
        "words",
        "byzantine_verses",
        "english_verses",
        "bib_interlinear_words",
        "red_letter_source_ranges",
        "red_letter_overlays",
        "style_prompts",
        "source_snapshots",
        "claude_candidates",
        "hidden_combos",
        "rankings",
        "ranking_entries",
        "tie_break_decisions",
        "generation_runs",
        "generation_run_items",
        "chapter_summaries",
    }
)


def test_alembic_migration_0001_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_smoke.db"
    cfg = Config(str(_PROJECT_ROOT / "api" / "migrations" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "api" / "migrations"))
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        actual_tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()

    missing = _EXPECTED_TABLES - actual_tables
    assert not missing, f"migration 0001_init did not create: {sorted(missing)}"


def test_disk_full_error_code_in_generation_run_items_check(imported_db: Path) -> None:
    """Reliability §Failure modes: ``error_code='disk_full'`` is a first-class
    typed value in the generation_run_items CHECK enum (not free-text)."""
    conn = sqlite3.connect(imported_db)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='generation_run_items'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert "'disk_full'" in sql, "generation_run_items.error_code CHECK must include 'disk_full'"


def test_foreign_keys_pragma_enabled(imported_db: Path) -> None:
    """Data §SQLite engine settings: foreign_keys must be ON at every connection."""
    from api.db.connection import open_connection

    conn = open_connection(imported_db)
    try:
        result = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert result == 1
