"""FastAPI dependencies — connection lifetime, request context."""
from __future__ import annotations

from typing import Iterator

from api.db.connection import open_connection


def db_connection() -> Iterator:
    """Yield a SQLite connection bound to the request lifetime."""
    conn = open_connection()
    try:
        yield conn
    finally:
        conn.close()
