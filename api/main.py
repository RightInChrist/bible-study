"""FastAPI application factory.

Hard rule (Security §Build-time guards): the launcher refuses to start
with BIND_HOST not in {127.0.0.1, localhost, ::1}. The validation happens
at settings-load time via ``api.settings.Settings._validate_bind_host``.

Architect §HTTP API pins one error envelope across every route:
``ErrorResponse {code, message, details}``. We install global handlers
for ``DomainError`` (any domain layer raises) and FastAPI's
``HTTPException``/``RequestValidationError`` so no route can drift the
shape — every non-2xx response goes through the same JSON encoder.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from pathlib import Path

from api.admin.routes import router as admin_router
from api.chapters.routes import router as chapters_router
from api.claude.worktree import sweep_stale_worktrees
from api.errors import DomainError
from api.gsv.routes import router as gsv_router
from api.middleware import CsrfMiddleware, HostAllowlistMiddleware
from api.rankings.routes import router as rankings_router
from api.red_letter.routes import router as red_letter_router
from api.runs.routes import router as runs_router
from api.sentences.routes import router as sentences_router
from api.settings import get_commit_hash, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="bible-study",
        version="0.0.1",
        description="Local Bible study tool — Matthew, sentence-aligned. "
        f"commit={get_commit_hash()} env={settings.env}",
    )

    app.add_middleware(CsrfMiddleware)
    app.add_middleware(HostAllowlistMiddleware)

    app.include_router(admin_router)
    app.include_router(sentences_router)
    app.include_router(runs_router)
    app.include_router(rankings_router)
    app.include_router(red_letter_router)
    app.include_router(gsv_router)
    app.include_router(chapters_router)

    # Sweep any stale worktrees a prior crashed runner left behind. Cheap
    # no-op on a clean tree; on a crashed-mid-run startup it's the only
    # cleanup path (no user action required).
    base_dir = settings.project_root / Path(settings.worktree_base_dir)
    sweep_stale_worktrees(base_dir, settings.project_root)

    @app.exception_handler(DomainError)
    async def _domain_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        # Re-shape FastAPI's default ``{"detail": ...}`` to the canonical
        # ``ErrorResponse`` envelope. If a handler raised
        # ``HTTPException(detail={...})`` with ``code``/``message`` keys we
        # pass them through; otherwise we synthesise a generic shape.
        detail: object = exc.detail
        if isinstance(detail, dict) and "code" in detail and "message" in detail:
            details_value = detail.get("details")
            payload = {
                "code": str(detail["code"]),
                "message": str(detail["message"]),
                "details": details_value if isinstance(details_value, dict) else None,
            }
        else:
            payload = {
                "code": "http_error",
                "message": str(detail) if detail is not None else "",
                "details": None,
            }
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "request validation failed",
                "details": {"errors": exc.errors()},
            },
        )

    return app


app = create_app()
