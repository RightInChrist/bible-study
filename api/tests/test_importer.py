"""Importer atomicity, idempotency, and hash-mismatch tests.

Reliability §test_coverage:
  - test_fixture_import_succeeds_on_pinned_fixtures
  - test_fixture_import_is_byte_identical_when_run_twice
  - test_fixture_checksum_mismatch_aborts_with_zero_writes
  - test_partial_import_rolls_back  (atomic-or-nothing invariant)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from api.importer import runner as importer_runner
from api.importer.manifest import (
    load_manifest,
    manifest_disk_hash,
    serialize_manifest_for_hashing,
)
from api.importer.runner import (
    FixtureImportError,
    fixture_status,
    import_fixtures,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migrate(db_path: Path) -> None:
    cfg = Config(str(_PROJECT_ROOT / "api" / "migrations" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "api" / "migrations"))
    command.upgrade(cfg, "head")


def _table_hash(db_path: Path, table: str) -> str:
    """Order-deterministic textual fingerprint of a fixture-derived table.

    Rolls every column up into a sortable comparison string so a re-import
    that produced byte-different content surfaces immediately.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()
    parts = []
    for row in rows:
        parts.append("|".join(f"{k}={row[k]!r}" for k in row.keys()))
    parts.sort()
    return "\n".join(parts)


def test_fixture_import_succeeds_on_pinned_fixtures(
    project_root: Path, migrated_db: Path
) -> None:
    result = import_fixtures(project_root=project_root, db_path=migrated_db)
    assert result.files_imported == 7
    # Full Matthew (28 chapters) — pinned conservatively below the
    # observed value (1682) to absorb minor re-segmentation drift.
    assert result.sentences_built >= 1500
    assert result.words_built > 0
    # Two hand-curated red-letter ranges in v1: Sermon on the Mount
    # (Matt 5:3-7:27) and "Come to Me" (Matt 11:28-30). The rest of
    # Matthew's red-letter coverage is deferred to the range-editor UI
    # slice — see ``TODO.md`` "Known deferrals".
    assert result.red_letter_source_ranges == 2
    assert result.fixture_version  # non-empty SHA-256


def test_fixture_import_is_byte_identical_when_run_twice(
    project_root: Path, migrated_db: Path
) -> None:
    import_fixtures(project_root=project_root, db_path=migrated_db)
    snapshot_first = {
        table: _table_hash(migrated_db, table)
        for table in (
            "sentences",
            "words",
            "byzantine_verses",
            "english_verses",
            "bib_interlinear_words",
            "red_letter_source_ranges",
            "fixture_version",
        )
    }
    import_fixtures(project_root=project_root, db_path=migrated_db)
    snapshot_second = {
        table: _table_hash(migrated_db, table)
        for table in snapshot_first
    }
    for table, first_hash in snapshot_first.items():
        assert snapshot_second[table] == first_hash, f"table {table} drifted on re-import"


def test_fixture_checksum_mismatch_aborts_with_zero_writes(
    project_root: Path, migrated_db: Path
) -> None:
    """Security test #11 / Data §Idempotent import: hash mismatch aborts;
    zero rows are written to source-text tables. The ``import.aborted``
    log line carries ``stage='preflight'`` (PLAN §Observability) so an
    on-call grep can distinguish hash-mismatch from in-transaction rollback.
    """
    import logging

    sblgnt = project_root / "fixtures" / "sblgnt" / "matthew.txt"
    original = sblgnt.read_bytes()
    sblgnt.write_bytes(original + b"\n")  # one extra byte changes the SHA-256

    class _ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.DEBUG)
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _ListHandler()
    target = logging.getLogger("bible_study.importer")
    saved_level = target.level
    saved_propagate = target.propagate
    target.setLevel(logging.DEBUG)
    target.propagate = False
    target.addHandler(handler)
    try:
        with pytest.raises(FixtureImportError) as excinfo:
            import_fixtures(project_root=project_root, db_path=migrated_db)
    finally:
        target.removeHandler(handler)
        target.setLevel(saved_level)
        target.propagate = saved_propagate

    assert excinfo.value.code == "fixture_hash_mismatch"

    conn = sqlite3.connect(migrated_db)
    try:
        for table in (
            "sentences",
            "words",
            "byzantine_verses",
            "english_verses",
            "bib_interlinear_words",
            "red_letter_source_ranges",
        ):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table} should be empty after a failed import, found {count} rows"
    finally:
        conn.close()

    aborted = [r for r in handler.records if r.getMessage() == "import.aborted"]
    assert len(aborted) == 1
    record = aborted[0]
    assert getattr(record, "stage", None) == "preflight"
    assert getattr(record, "error_code", None) == "fixture_hash_mismatch"


def test_fixture_status_returns_db_hash_after_import(
    project_root: Path, migrated_db: Path
) -> None:
    result = import_fixtures(project_root=project_root, db_path=migrated_db)
    disk_hash, db_hash, last_imported_at = fixture_status(
        project_root=project_root, db_path=migrated_db
    )
    assert disk_hash == db_hash
    assert db_hash == result.fixture_version
    assert last_imported_at is not None and last_imported_at.endswith("Z")


def test_partial_import_rolls_back(
    project_root: Path,
    migrated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reliability §test_coverage / Data §Idempotent import:
    inject a parse error mid-import; assert the SQLite source-text tables
    are byte-identical to their pre-import state, AND that exactly one
    ``import.aborted`` event with ``error_code`` set and ``stage='transaction'``
    was emitted (PLAN §Observability stable identifiers).

    We monkeypatch ``_execute_many`` so the THIRD call inside the BEGIN
    IMMEDIATE block raises — by then the DELETE FROM statements have run
    and the first INSERTs (sentences, words) have committed to the WAL but
    not yet been COMMITed. The test asserts the rollback drops every
    fixture-derived row, leaving the DB byte-identical to its pre-import
    snapshot.
    """
    import logging

    # Run a successful import once so we have known-good state to roll back to.
    import_fixtures(project_root=project_root, db_path=migrated_db)

    def _table_snapshot(db_path: Path) -> dict[str, str]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows: dict[str, str] = {}
            for table in (
                "sentences",
                "words",
                "byzantine_verses",
                "english_verses",
                "bib_interlinear_words",
                "red_letter_source_ranges",
                "fixture_version",
            ):
                contents = conn.execute(f"SELECT * FROM {table}").fetchall()
                serialised = []
                for row in contents:
                    serialised.append("|".join(f"{k}={row[k]!r}" for k in row.keys()))
                serialised.sort()
                rows[table] = "\n".join(serialised)
            return rows
        finally:
            conn.close()

    pre_snapshot = _table_snapshot(migrated_db)

    call_count = {"n": 0}
    real_execute_many = importer_runner._execute_many

    def _flaky_execute_many(conn, sql, rows):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        # Third call corresponds to the byzantine_verses INSERT — by this
        # point sentences + words have been written inside the txn but not
        # committed; the DELETE FROM truncations have also run.
        if call_count["n"] == 3:
            raise RuntimeError("simulated mid-import parse error")
        return real_execute_many(conn, sql, rows)

    monkeypatch.setattr(importer_runner, "_execute_many", _flaky_execute_many)

    class _ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.DEBUG)
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _ListHandler()
    target = logging.getLogger("bible_study.importer")
    saved_level = target.level
    saved_propagate = target.propagate
    target.setLevel(logging.DEBUG)
    target.propagate = False
    target.addHandler(handler)
    try:
        with pytest.raises(RuntimeError, match="simulated mid-import parse error"):
            import_fixtures(project_root=project_root, db_path=migrated_db)
    finally:
        target.removeHandler(handler)
        target.setLevel(saved_level)
        target.propagate = saved_propagate

    post_snapshot = _table_snapshot(migrated_db)
    for table, before in pre_snapshot.items():
        assert post_snapshot[table] == before, (
            f"table {table} drifted after a rolled-back import — atomic-or-nothing broken"
        )

    aborted = [r for r in handler.records if r.getMessage() == "import.aborted"]
    assert len(aborted) == 1, (
        f"expected exactly one import.aborted event, got {len(aborted)}: "
        f"{[r.getMessage() for r in handler.records]}"
    )
    record = aborted[0]
    assert getattr(record, "stage", None) == "transaction"
    assert getattr(record, "error_code", None) is not None


def test_full_matthew_imports_28_chapters(
    project_root: Path, migrated_db: Path
) -> None:
    """Slice 2 invariant: importing the full-Matthew fixture set produces
    sentences spanning every chapter 1..28. Pre-slice the fixture covered
    only chapter 5; this test guarantees no regression to that scope.
    """
    import_fixtures(project_root=project_root, db_path=migrated_db)
    conn = sqlite3.connect(migrated_db)
    try:
        rows = conn.execute(
            "SELECT chapter, COUNT(*) FROM sentences GROUP BY chapter ORDER BY chapter"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
    finally:
        conn.close()
    chapters_seen = [chapter for chapter, _ in rows]
    assert chapters_seen == list(range(1, 29)), (
        f"expected every chapter 1..28 populated, got {chapters_seen}"
    )
    for chapter, count in rows:
        assert count > 0, f"chapter {chapter} had zero sentences"
    # Conservatively pinned below the observed total (1682) to leave room
    # for minor segmenter rule changes without forcing a fixture re-pin.
    assert total >= 1500, f"expected at least 1500 sentences across full Matthew, got {total}"


def test_byzantine_verses_cover_all_28_chapters(
    project_root: Path, migrated_db: Path
) -> None:
    """Slice 2 invariant: the verse-keyed Byzantine table holds every
    chapter 1..28 (the v1 deferral keeps Byzantine at verse-keyed only —
    sentence-level alignment is in ``TODO.md`` v2 list — but every chapter
    must still be present so the parallel reader's Byzantine column
    populates for every Matthew sentence).
    """
    import_fixtures(project_root=project_root, db_path=migrated_db)
    conn = sqlite3.connect(migrated_db)
    try:
        rows = conn.execute(
            "SELECT chapter, COUNT(*) FROM byzantine_verses GROUP BY chapter ORDER BY chapter"
        ).fetchall()
    finally:
        conn.close()
    chapters_seen = [chapter for chapter, _ in rows]
    assert chapters_seen == list(range(1, 29)), (
        f"expected Byzantine to cover every chapter 1..28, got {chapters_seen}"
    )
    for chapter, count in rows:
        # Every Matthew chapter has at least 17 verses (chapter 3 is the
        # shortest in the Byzantine fixture). Pin below that to be safe.
        assert count >= 15, f"chapter {chapter} had only {count} Byzantine verses"


def test_manifest_disk_hash_canonicalises(tmp_path: Path) -> None:
    """PLAN §fixture_version pins the rule: manifest hash uses the same
    canonicalisation as ``source_snapshots``. Two manifests with identical
    content but different formatting (indentation, key order) must hash
    to the same value.
    """
    project_root = Path(__file__).resolve().parents[2]
    canonical_path = project_root / "fixtures" / "manifest.json"
    manifest = load_manifest(canonical_path)
    canonical_payload = serialize_manifest_for_hashing(manifest)

    pretty = tmp_path / "manifest_pretty.json"
    pretty.write_text(
        json.dumps(json.loads(canonical_payload), indent=4, sort_keys=False),
        encoding="utf-8",
    )

    compact = tmp_path / "manifest_compact.json"
    compact.write_text(canonical_payload, encoding="utf-8")

    h_pretty = manifest_disk_hash(pretty)
    h_compact = manifest_disk_hash(compact)
    h_canonical = manifest_disk_hash(canonical_path)
    assert h_pretty == h_compact == h_canonical


def test_importer_emits_started_and_committed_events(
    project_root: Path,
    migrated_db: Path,
) -> None:
    """PLAN §Observability: the importer emits structured events at start
    and on successful commit. We attach a list-collecting handler directly
    to the importer logger and force the level so pytest's logging plugin
    can't suppress propagation.
    """
    import logging

    class _ListHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.DEBUG)
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _ListHandler()
    target = logging.getLogger("bible_study.importer")
    saved_level = target.level
    saved_propagate = target.propagate
    target.setLevel(logging.DEBUG)
    target.propagate = False
    target.addHandler(handler)
    try:
        import_fixtures(project_root=project_root, db_path=migrated_db)
    finally:
        target.removeHandler(handler)
        target.setLevel(saved_level)
        target.propagate = saved_propagate

    events = [record.getMessage() for record in handler.records]
    assert "import.started" in events, f"got events: {events}"
    assert "import.committed" in events, f"got events: {events}"
    committed = next(
        r for r in handler.records if r.getMessage() == "import.committed"
    )
    # PLAN §Observability pins the keys: row counts + elapsed_ms.
    assert hasattr(committed, "fixture_version")
    assert hasattr(committed, "elapsed_ms")
    assert hasattr(committed, "sentences")
