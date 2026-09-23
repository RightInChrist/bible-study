"""Tests for ``POST /api/v1/admin/build-static``.

Covers Slice 6:
  - test_build_static_creates_dist_with_expected_files
  - test_build_static_csrf_required
  - test_build_static_grep_guard_rejects_secret_in_dist_tmp
  - test_build_static_grep_guard_rejects_admin_path_reference_in_bundle
  - test_build_static_atomic_swap_preserves_dist_bak

These tests stub the ``npm run build:static`` shell-out via
monkeypatching ``_run_vite_build`` so they don't require Node.js to be
installed in CI. The full pipeline (real Vite build + grep guard +
atomic swap) is exercised by the manual verification step at the end
of the slice.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from api.admin import service as admin_service
from api.settings import get_commit_hash


@pytest.fixture
def admin_client(imported_db: Path) -> Iterator[TestClient]:
    get_commit_hash.cache_clear()
    from api.main import create_app

    app = create_app()
    test_client = TestClient(app, base_url="http://127.0.0.1:8000")
    yield test_client
    test_client.close()


def _stub_vite_build(
    monkeypatch: pytest.MonkeyPatch,
    extra_files: dict[str, bytes] | None = None,
) -> list[Path]:
    """Replace the npm shell-out with a function that writes a couple of
    skeleton SPA files into ``dist_tmp/``. Returns the list of paths so
    a test can introspect what got written.
    """
    written: list[Path] = []

    def fake_vite_build(project_root: Path, web_dir: Path, dist_tmp: Path) -> int:
        index_html = dist_tmp / "index.html"
        index_html.write_text(
            "<!doctype html><html><head><title>bs</title></head>"
            "<body><div id='root'></div></body></html>",
            encoding="utf-8",
        )
        written.append(index_html)
        assets_js = dist_tmp / "assets" / "main.js"
        assets_js.parent.mkdir(parents=True, exist_ok=True)
        assets_js.write_text(
            "// minimal SPA bundle\nconsole.log('bible-study static');\n",
            encoding="utf-8",
        )
        written.append(assets_js)
        if extra_files:
            for rel, content in extra_files.items():
                target = dist_tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                written.append(target)
        return len(written)

    monkeypatch.setattr(admin_service, "_run_vite_build", fake_vite_build)
    return written


def test_build_static_csrf_required(admin_client: TestClient) -> None:
    no_origin = admin_client.post("/api/v1/admin/build-static", json={})
    assert no_origin.status_code == 403

    no_xrb = admin_client.post(
        "/api/v1/admin/build-static",
        json={},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert no_xrb.status_code == 403


def test_build_static_creates_dist_with_expected_files(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_vite_build(monkeypatch)
    response = admin_client.post(
        "/api/v1/admin/build-static",
        json={"include_unranked_placeholders": True},
        headers=writable_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dist_path"].endswith("/dist") or body["dist_path"].endswith("\\dist")
    assert body["files_written"] > 10, "must emit JSON snapshots + SPA bundle"
    assert "took_ms" in body
    assert body["coverage"]["total"] >= 0  # red-letter total

    dist = project_root / "dist"
    assert (dist / "index.html").exists()
    assert (dist / "api" / "v1" / "health.json").exists()
    assert (dist / "api" / "v1" / "admin" / "fixture-status.json").exists()
    # Per-chapter sentence snapshots — at least chapter-1.json must exist.
    assert (dist / "api" / "v1" / "sentences" / "chapter-1.json").exists()
    assert (dist / "api" / "v1" / "sentences" / "chapter-5.json").exists()
    # First sentence under chapter 5 is a known red-letter
    # (Sermon on the Mount). Pick any sentence that exists and assert its
    # parallel.json is on disk.
    parallel_files = list((dist / "api" / "v1" / "sentences").rglob("parallel.json"))
    assert len(parallel_files) >= 1
    # GSV chapter 5 must always exist in JSON form.
    assert (dist / "api" / "v1" / "gsv" / "5.json").exists()
    assert (dist / "api" / "v1" / "gsv" / "5.md").exists()
    # Either text exists or the not-available marker does.
    assert (
        (dist / "api" / "v1" / "gsv" / "5.txt").exists()
        or (dist / "api" / "v1" / "gsv" / "5.txt-not-available.json").exists()
    )
    assert (dist / "api" / "v1" / "gsv" / "coverage.json").exists()


def test_build_static_grep_guard_rejects_secret_in_dist_tmp(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject ``sk-ant-fakekey-...`` into the SPA bundle and assert the
    build aborts with ``build_guard_failed`` and ``dist.tmp/`` is removed.
    """
    fake_key = b"// telemetry token: sk-ant-fakekey-1234567890123456789\n"
    _stub_vite_build(monkeypatch, extra_files={"assets/leaked.js": fake_key})

    # Pre-existing dist/ — must NOT be touched.
    dist = project_root / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir()
    (dist / "marker.txt").write_text("preserve me", encoding="utf-8")

    response = admin_client.post(
        "/api/v1/admin/build-static",
        json={},
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "build_guard_failed"
    hits = body["details"]["hits"]
    assert "sk-ant-" in hits
    assert any("leaked.js" in p for p in hits["sk-ant-"])

    # dist.tmp/ must be cleaned up; dist/ untouched.
    assert not (project_root / "dist.tmp").exists()
    assert (dist / "marker.txt").exists()


def test_build_static_grep_guard_rejects_admin_path_reference_in_bundle(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a built JS asset accidentally references ``/admin/reimport``,
    the static build refuses to swap the tree.
    """
    leaky = b'fetch("/admin/reimport", { method: "POST" });\n'
    _stub_vite_build(monkeypatch, extra_files={"assets/admin-call.js": leaky})

    response = admin_client.post(
        "/api/v1/admin/build-static",
        json={},
        headers=writable_headers,
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "build_guard_failed"
    hits = body["details"]["hits"]
    assert "/admin/reimport" in hits


def test_build_static_atomic_swap_preserves_dist_bak(
    admin_client: TestClient,
    writable_headers: dict[str, str],
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build twice — after the second build, ``dist.bak/`` should hold
    the *first* build's content (Reliability §Static-site atomic swap).
    """
    dist = project_root / "dist"
    dist_bak = project_root / "dist.bak"
    if dist.exists():
        shutil.rmtree(dist)
    if dist_bak.exists():
        shutil.rmtree(dist_bak)

    _stub_vite_build(monkeypatch)
    r1 = admin_client.post(
        "/api/v1/admin/build-static",
        json={},
        headers=writable_headers,
    )
    assert r1.status_code == 200
    assert dist.exists()
    # Drop a marker into dist/ so we can identify "first build" content
    # before the second build moves it to dist.bak.
    (dist / "first-build-marker.txt").write_text("v1", encoding="utf-8")

    r2 = admin_client.post(
        "/api/v1/admin/build-static",
        json={},
        headers=writable_headers,
    )
    assert r2.status_code == 200
    assert dist_bak.exists()
    assert (dist_bak / "first-build-marker.txt").read_text(encoding="utf-8") == "v1"
    # The new dist/ has the fresh build content; the marker is gone.
    assert not (dist / "first-build-marker.txt").exists()
