"""Concurrent read requests must not fail on SQLite thread affinity.

Regression for the Read page failing with "FAILED TO LOAD REFERENCE TEXT":
the page fires the chapter list, the fixture status, and three requests per
sentence (parallel / candidates / ranking) at once. Each request's
connection is opened by the sync ``db_connection`` dependency on one
threadpool worker, used by the sync route on another, and closed on a
third, which raised ``sqlite3.ProgrammingError: SQLite objects created in a
thread can only be used in that same thread`` → 500 for every overlapping
request (40/40 concurrent vs 0/40 sequential on a copy of the real DB).

The test replays that request pattern through the ASGI app so FastAPI's
real threadpool dispatch is exercised.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from api.settings import get_commit_hash

_BASE_URL = "http://127.0.0.1:8000"


async def _read_page_fanout(app: object, chapter: int) -> list[tuple[str, int]]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        listing = await client.get(f"/api/v1/sentences?chapter={chapter}")
        assert listing.status_code == 200, listing.text
        sentence_ids = [s["sentence_id"] for s in listing.json()["sentences"]]

        paths = [f"/api/v1/sentences?chapter={chapter}", "/api/v1/admin/fixture-status"]
        for sentence_id in sentence_ids:
            paths += [
                f"/api/v1/sentences/{sentence_id}/parallel",
                f"/api/v1/sentences/{sentence_id}/candidates",
                f"/api/v1/sentences/{sentence_id}/ranking",
            ]
        responses = await asyncio.gather(*(client.get(p) for p in paths))
        return [(p, r.status_code) for p, r in zip(paths, responses)]


def test_read_page_fanout_succeeds_concurrently(imported_db: Path) -> None:
    get_commit_hash.cache_clear()
    from api.main import create_app

    app = create_app()
    results = asyncio.run(_read_page_fanout(app, chapter=5))

    failures = [(path, status) for path, status in results if status != 200]
    assert len(results) > 200
    assert failures == [], f"{len(failures)}/{len(results)} failed, e.g. {failures[:3]}"
