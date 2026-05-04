from __future__ import annotations

from fastapi import APIRouter, Depends

from api.admin.schemas import FixtureStatusResponse, HealthResponse
from api.deps import db_connection
from api.importer.manifest import manifest_disk_hash
from api.settings import get_commit_hash, get_settings


router = APIRouter(prefix="/api/v1", tags=["admin"])


@router.get(
    "/health",
    response_model=HealthResponse,
    response_model_exclude_none=True,
)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        commit_hash=get_commit_hash(),
        env=settings.env,
    )


@router.get(
    "/admin/fixture-status",
    response_model=FixtureStatusResponse,
    response_model_exclude_none=True,
)
def fixture_status(conn=Depends(db_connection)) -> FixtureStatusResponse:
    settings = get_settings()
    manifest_path = settings.project_root / "fixtures" / "manifest.json"
    disk_hash = manifest_disk_hash(manifest_path)
    row = conn.execute(
        "SELECT manifest_hash, last_imported_at FROM fixture_version WHERE id = 1"
    ).fetchone()
    db_hash = row["manifest_hash"] if row is not None else None
    last_imported_at = row["last_imported_at"] if row is not None else None
    return FixtureStatusResponse(
        disk_manifest_hash=disk_hash,
        db_fixture_version=db_hash,
        stale=(db_hash != disk_hash),
        last_imported_at=last_imported_at,
    )
