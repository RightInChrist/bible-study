"""HTTP routes for generation runs and Claude candidates.

Architect §Contracts:
  - ``POST /api/v1/runs`` — start a run.
  - ``GET /api/v1/runs/{run_id}`` — current state + counts.
  - ``GET /api/v1/runs`` — paginated recent runs.
  - ``GET /api/v1/runs/{run_id}/stream`` — SSE.
  - ``GET /api/v1/sentences/{sentence_id}/candidates`` — read-side
    candidate list, used by the rank UI in a later slice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.admin.schemas import ErrorResponse
from api.claude.worktree import ClaudeCodeWorktreeSpawner, WorktreeSpawner
from api.deps import db_connection
from api.runs.runner import (
    RunCreationError,
    create_run,
    schedule_run,
)
from api.runs.schemas import (
    CreateRunRequest,
    RunListResponse,
    RunResponse,
    SentenceCandidatesResponse,
)
from api.runs.service import RunNotFoundError, get_run, list_runs, list_sentence_candidates
from api.runs.sse import stream_run
from api.settings import get_settings


router = APIRouter(prefix="/api/v1", tags=["runs"])


_PROCESS_WORKTREE_SPAWNER: WorktreeSpawner | None = None


def get_worktree_spawner() -> WorktreeSpawner:
    """FastAPI dependency — process-wide single instance.

    Tests override via ``app.dependency_overrides[get_worktree_spawner]``
    with :class:`api.tests.fakes.FakeWorktreeSpawner`. Production wires
    the real :class:`ClaudeCodeWorktreeSpawner` which spawns the
    ``claude`` CLI inside a fresh git worktree per generation.

    No env-var fake selection — the runtime spawner is always real;
    tests inject the fake explicitly. (Slice 3a-pre-redo selected the
    fake via ``BIBLE_STUDY_FAKE_CLAUDE``; that env-var path is gone.)
    """
    global _PROCESS_WORKTREE_SPAWNER
    if _PROCESS_WORKTREE_SPAWNER is None:
        _PROCESS_WORKTREE_SPAWNER = ClaudeCodeWorktreeSpawner()
    return _PROCESS_WORKTREE_SPAWNER


def _domain_error_to_http(exc: RunCreationError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
    )


@router.post(
    "/runs",
    response_model=RunResponse,
    response_model_exclude_none=True,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def create_run_route(
    body: CreateRunRequest,
    spawner: WorktreeSpawner = Depends(get_worktree_spawner),
) -> RunResponse:
    """Async on purpose — ``schedule_run`` calls ``asyncio.create_task``
    which needs the running event loop. Sync routes execute on the
    threadpool where no loop is available.
    """
    from api.db.connection import open_connection

    settings = get_settings()
    conn = open_connection()
    try:
        try:
            result = create_run(
                conn=conn,
                scope=body.scope,
                style_prompt_version=body.style_prompt_version,
                source_set_id=body.source_set_id,
                model=body.model,
                effort=body.effort,
                spawner=spawner,
                project_root=settings.project_root,
            )
        except RunCreationError as exc:
            raise _domain_error_to_http(exc)

        schedule_run(
            run_id=result.run_id,
            sentence_ids=result.sentence_ids,
            style_prompt_version=body.style_prompt_version,
            source_set_id=body.source_set_id,
            model=body.model,
            effort=body.effort,
            project_root=settings.project_root,
            db_path=settings.database_path_absolute,
            spawner=spawner,
            wants_context_window=result.wants_context_window,
            context_window_before=result.context_window_before,
            context_window_after=result.context_window_after,
            chapter=result.chapter,
        )

        return get_run(conn, result.run_id)
    finally:
        conn.close()


@router.get(
    "/runs",
    response_model=RunListResponse,
    response_model_exclude_none=True,
)
def list_runs_route(
    limit: int = Query(default=50, ge=1, le=200),
    conn=Depends(db_connection),
) -> RunListResponse:
    return list_runs(conn, limit=limit)


@router.get(
    "/runs/{run_id}",
    response_model=RunResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
)
def get_run_route(
    run_id: str,
    conn=Depends(db_connection),
) -> RunResponse:
    return get_run(conn, run_id)


@router.get("/runs/{run_id}/stream")
async def stream_run_route(run_id: str):
    from api.db.connection import open_connection

    conn = open_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM generation_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(
                message=f"run {run_id!r} not found",
                details={"run_id": run_id},
            )
    finally:
        conn.close()
    return await stream_run(run_id)


@router.get(
    "/sentences/{sentence_id}/candidates",
    response_model=SentenceCandidatesResponse,
    response_model_exclude_none=True,
)
def list_sentence_candidates_route(
    sentence_id: str,
    conn=Depends(db_connection),
) -> SentenceCandidatesResponse:
    return list_sentence_candidates(conn, sentence_id)
