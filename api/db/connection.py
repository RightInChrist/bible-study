from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from api.settings import get_settings


_PRAGMA_STATEMENTS: tuple[str, ...] = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA wal_autocheckpoint = 1000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -20000",
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for stmt in _PRAGMA_STATEMENTS:
        conn.execute(stmt)


def open_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with the pinned PRAGMAs applied.

    Data §SQLite engine settings: WAL + foreign_keys=ON + busy_timeout=5000
    are part of the data contract, not tuning knobs.
    """
    if db_path is None:
        db_path = get_settings().database_path_absolute
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


@contextmanager
def connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = open_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()
