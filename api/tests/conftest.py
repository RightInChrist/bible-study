"""Shared pytest fixtures.

Each test gets a tempfile-backed SQLite DB with the 0001_init migration
applied and (where requested) the fixture import already run. Settings are
driven through env vars so the production code path is exercised end-to-end.

Migration is applied by directly calling the revision's ``upgrade()`` against
a sqlalchemy engine — bypasses ``alembic.command.upgrade``'s ~10s startup
per call. Test ``test_alembic_migration_0001_creates_all_tables`` keeps the
real ``alembic.command.upgrade`` path covered.
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from api.importer.runner import import_fixtures  # noqa: E402
from api.settings import get_commit_hash, reset_settings_cache  # noqa: E402


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Copy the repo's fixtures into an isolated working tree.

    Each test gets its own fixtures directory so byte-mutation tests don't
    contaminate the shared repo files.
    """
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(_PROJECT_ROOT / "fixtures", root / "fixtures")
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "bible_study.db"


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Default: clear cached settings between tests so env-var changes apply."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def configured_settings(
    project_root: Path,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point Settings at the test's fixtures + DB via env vars."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("PROJECT_ROOT_OVERRIDE", str(project_root))
    reset_settings_cache()


def _apply_0001_init(db_path: Path) -> None:
    """Apply the bootstrap migration via raw sqlite3 — alembic's machinery
    adds ~9s of overhead per call, which the dedicated migration test
    (``test_alembic_migration_0001_creates_all_tables``) keeps covered.

    We capture each ``op.execute`` from ``api.migrations.versions.0001_init``'s
    ``upgrade()`` body and replay them inside one BEGIN..COMMIT against
    sqlite3 directly. The captured-SQL list is byte-equivalent to what
    Alembic would emit, so this is a faithful application of the revision.
    """
    import sqlite3

    revision_module = importlib.import_module("api.migrations.versions.0001_init")

    captured: list[str] = []

    class _RecordingOp:
        def execute(self, sql: object, *_args: object, **_kwargs: object) -> None:
            captured.append(str(sql))

    import alembic.op as op_module

    original = op_module.execute
    op_module.execute = _RecordingOp().execute  # type: ignore[assignment]
    try:
        revision_module.upgrade()
    finally:
        op_module.execute = original  # type: ignore[assignment]

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        for sql in captured:
            conn.execute(sql)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES ('0001_init')"
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


@pytest.fixture
def migrated_db(configured_settings: None, db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _apply_0001_init(db_path)
    return db_path


@pytest.fixture
def imported_db(migrated_db: Path, project_root: Path) -> Path:
    import_fixtures(project_root=project_root, db_path=migrated_db)
    return migrated_db


@pytest.fixture
def client(imported_db: Path) -> Iterator[TestClient]:
    """A TestClient bound to a fresh imported DB."""
    get_commit_hash.cache_clear()
    from api.main import create_app

    app = create_app()
    test_client = TestClient(app, base_url="http://127.0.0.1:8000")
    yield test_client
    test_client.close()


@pytest.fixture
def writable_headers() -> dict[str, str]:
    """Headers required for state-changing routes (Security §CSRF)."""
    return {
        "Origin": "http://127.0.0.1:8000",
        "X-Requested-By": "bible-study-ui",
    }


@pytest.fixture
def runs_client(imported_db: Path) -> Iterator[tuple[TestClient, "FakeClaudeClient"]]:
    """A TestClient with the Anthropic client dependency overridden.

    Returns ``(client, fake)`` so tests can pre-configure responses on
    ``fake`` (stubbed candidate text, latency, responder callbacks) and
    inspect ``fake.calls`` after.
    """
    get_commit_hash.cache_clear()
    from api.main import create_app
    from api.runs.routes import get_claude_client
    from api.tests.fakes import FakeClaudeClient

    app = create_app()
    fake = FakeClaudeClient()
    app.dependency_overrides[get_claude_client] = lambda: fake
    test_client = TestClient(app, base_url="http://127.0.0.1:8000")
    yield test_client, fake
    test_client.close()


# Re-export for typing in the fixture above. Imported lazily so test
# collection doesn't bring the whole runtime tree.
from api.tests.fakes import FakeClaudeClient  # noqa: E402
