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
from api.claude.client import AnthropicClaudeClient, ClaudeClient
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


_PROCESS_CLAUDE_CLIENT: ClaudeClient | None = None


def get_claude_client() -> ClaudeClient:
    """FastAPI dependency — process-wide single instance.

    Tests override via ``app.dependency_overrides[get_claude_client]``.

    Eval-harness path: when ``settings.bible_study_fake_claude`` is true
    AND ``env == 'development'``, return a deterministic fake. The
    env-gate matches Security §Authn / Authz: a public-facing
    deployment must never accept a flag that bypasses the real Anthropic
    integration.
    """
    global _PROCESS_CLAUDE_CLIENT
    if _PROCESS_CLAUDE_CLIENT is None:
        settings = get_settings()
        if settings.bible_study_fake_claude and settings.env == "development":
            _PROCESS_CLAUDE_CLIENT = _build_eval_fake_claude_client()
        else:
            _PROCESS_CLAUDE_CLIENT = AnthropicClaudeClient()
    return _PROCESS_CLAUDE_CLIENT


def _build_eval_fake_claude_client() -> ClaudeClient:
    """Build a deterministic fake-Claude client for the ``bs/run/`` eval.

    The candidate text is derived from ``sentence_id`` so the eval can
    assert on a stable string, and ``api_key_set=True`` so the runner's
    pre-flight check passes without a real key.
    """
    from datetime import UTC, datetime

    from api.claude.client import ClaudeCallRequest
    from api.claude.schemas import CandidateGenerated

    class _Fake:
        @property
        def api_key_set(self) -> bool:
            return True

        async def generate(self, request: ClaudeCallRequest) -> CandidateGenerated:
            return CandidateGenerated(
                candidate_text=(
                    f"[fake-claude] translation of {request.bundle.sentence_id} "
                    f"({request.bundle.verse_range})"
                ),
                model=request.model,
                generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                latency_ms=1,
            )

    return _Fake()


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
    claude_client: ClaudeClient = Depends(get_claude_client),
) -> RunResponse:
    """Async on purpose — ``schedule_run`` calls ``asyncio.create_task``
    which needs the running event loop. Sync routes execute on the
    threadpool where no loop is available.

    Opens the SQLite connection inside the route (not via ``Depends``) so
    it lives on the same thread as the asyncio event loop. SQLite
    objects are thread-bound; the dependency's separate-thread origin
    would surface as ``ProgrammingError`` here.
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
                claude_client=claude_client,
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
            project_root=settings.project_root,
            db_path=settings.database_path_absolute,
            claude_client=claude_client,
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
    """SSE stream for a run.

    Opens the connection inline (not via ``Depends``) to keep SQLite on
    the event loop's thread; see ``create_run_route`` for the same
    rationale. The stream itself opens its own short-lived connections
    in ``api.runs.sse`` for replay queries.
    """
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
