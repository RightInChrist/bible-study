"""HTTP routes for the GSV (Slice 5).

  - ``GET /api/v1/gsv/coverage`` — repository-wide coverage stats
    (Designer Flow 6 step 2 + status-pill style indicators).
  - ``GET /api/v1/gsv/{chapter}?format=json|text|markdown`` —
    per-chapter GSV in the chosen format.

DELIBERATE EXCEPTION to the global "every route uses ``response_model``"
rule: the ``text`` and ``markdown`` formats return raw bodies via
``Response(media_type=...)``. Pydantic ``response_model`` only fits the
JSON path; the text/markdown branches deliberately bypass it because
their wire payload is plain prose, not a JSON shape. The JSON branch
keeps ``response_model=GsvChapterResponse`` so ``/docs`` documents the
canonical contract.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from api.admin.schemas import ErrorResponse
from api.deps import db_connection
from api.gsv.errors import GsvChapterOutOfRangeError, UnresolvedTiesError
from api.gsv.schemas import GsvChapterResponse, GsvCoverageResponse
from api.gsv.service import (
    compile_chapter_gsv,
    compute_coverage,
    render_markdown,
    render_plain_text,
)


router = APIRouter(prefix="/api/v1/gsv", tags=["gsv"])


GsvFormat = Literal["json", "text", "markdown"]


@router.get(
    "/coverage",
    response_model=GsvCoverageResponse,
    response_model_exclude_none=True,
)
def gsv_coverage(conn=Depends(db_connection)) -> GsvCoverageResponse:
    return compute_coverage(conn)


@router.get(
    "/{chapter}",
    # NOTE: response_model is omitted because this route returns three
    # different content-types (JSON / text / markdown) depending on
    # ``format``. The JSON branch returns ``GsvChapterResponse`` via
    # ``JSONResponse`` with the model's serialised dict; we still
    # document the JSON contract by referencing the schema in the
    # responses block below.
    responses={
        200: {
            "model": GsvChapterResponse,
            "description": (
                "JSON shape when format=json. text/markdown formats return "
                "raw prose with the matching content-type."
            ),
        },
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def gsv_chapter(
    chapter: int = Path(..., ge=1, le=28),
    format: GsvFormat = Query("json"),
    download: bool = Query(False),
    conn=Depends(db_connection),
) -> Response:
    if not 1 <= chapter <= 28:
        # Path validator rejects out-of-range; defence-in-depth so the
        # service-layer exception still maps cleanly if validation is
        # ever loosened.
        raise GsvChapterOutOfRangeError(chapter)
    response = compile_chapter_gsv(conn, chapter)

    if format == "json":
        body = response.model_dump(exclude_none=True)
        return JSONResponse(content=body, status_code=200)

    if format == "text":
        # Raises UnresolvedTiesError when ties exist — global handler
        # renders 409 with code='unresolved_ties'.
        text_body = render_plain_text(response)
        headers: dict[str, str] = {}
        if download:
            headers["Content-Disposition"] = (
                f'attachment; filename="mat-{chapter}-gsv.txt"'
            )
        return PlainTextResponse(
            content=text_body, media_type="text/plain", headers=headers
        )

    # markdown
    md_body = render_markdown(response)
    headers = {}
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="mat-{chapter}-gsv.md"'
        )
    return Response(
        content=md_body, media_type="text/markdown", headers=headers
    )


# Re-export for easier mounting + handler tests; the global exception
# handler in ``api.main`` already covers DomainError → ErrorResponse so
# we only need to ensure the errors module is loaded with the routes.
__all__ = ["router", "GsvChapterOutOfRangeError", "UnresolvedTiesError"]
