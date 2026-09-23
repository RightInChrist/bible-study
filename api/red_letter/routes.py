"""HTTP routes for the red-letter overlay editor (Slice 7).

  - ``GET /api/v1/red-letter/chapter/{N}/overlays`` — every chain
    touching the chapter + the canonical effective sentence ID set.
  - ``POST /api/v1/red-letter/mark`` — high-level mark by verse range.
  - ``POST /api/v1/red-letter/unmark`` — append a ``rejected=1`` leaf.
  - ``POST /api/v1/red-letter/restore`` — re-activate a rejected chain.
  - ``GET /api/v1/red-letter/source-range/{id}/chain`` — chain history
    for one Berean source range.
  - ``GET /api/v1/red-letter/overlay/{id}/chain`` — chain history for a
    manual chain reachable through any of its overlay rows.

Writes require ``If-Match: <version>`` (Architect §HTTP API). The service
layer raises :class:`StaleOverlayVersionError` on mismatch; the global
``DomainError`` handler renders the canonical ``ErrorResponse`` envelope.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from api.admin.schemas import ErrorResponse
from api.deps import db_connection
from api.red_letter.errors import IfMatchRequiredError
from api.red_letter.schemas import (
    ChapterOverlaysResponse,
    MarkRedLetterRequest,
    MarkRedLetterResponse,
    OverlayChainResponse,
    RestoreRedLetterRequest,
    UnmarkRedLetterRequest,
    UnmarkRedLetterResponse,
)
from api.red_letter.service import (
    get_chapter_overlays,
    get_overlay_chain,
    get_source_range_chain,
    mark_red_letter,
    restore_red_letter,
    unmark_red_letter,
)


router = APIRouter(prefix="/api/v1/red-letter", tags=["red-letter"])


def _parse_if_match(if_match: str | None) -> int:
    if if_match is None:
        raise IfMatchRequiredError()
    raw = if_match.strip().strip('"')
    try:
        return int(raw)
    except ValueError as exc:
        raise IfMatchRequiredError() from exc


@router.get(
    "/chapter/{chapter}/overlays",
    response_model=ChapterOverlaysResponse,
    response_model_exclude_none=True,
)
def get_chapter_overlays_route(
    chapter: int,
    conn=Depends(db_connection),
) -> ChapterOverlaysResponse:
    return get_chapter_overlays(conn, chapter)


@router.get(
    "/source-range/{source_range_id}/chain",
    response_model=OverlayChainResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
)
def get_source_range_chain_route(
    source_range_id: int,
    conn=Depends(db_connection),
) -> OverlayChainResponse:
    return get_source_range_chain(conn, source_range_id)


@router.get(
    "/overlay/{overlay_id}/chain",
    response_model=OverlayChainResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
)
def get_overlay_chain_route(
    overlay_id: int,
    conn=Depends(db_connection),
) -> OverlayChainResponse:
    return get_overlay_chain(conn, overlay_id)


@router.post(
    "/mark",
    response_model=MarkRedLetterResponse,
    response_model_exclude_none=True,
    responses={
        400: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def mark_red_letter_route(
    body: MarkRedLetterRequest,
    conn=Depends(db_connection),
) -> MarkRedLetterResponse:
    return mark_red_letter(conn, body)


@router.post(
    "/unmark",
    response_model=UnmarkRedLetterResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
    },
)
def unmark_red_letter_route(
    body: UnmarkRedLetterRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    conn=Depends(db_connection),
) -> UnmarkRedLetterResponse:
    version = _parse_if_match(if_match)
    return unmark_red_letter(conn, body, version)


@router.post(
    "/restore",
    response_model=UnmarkRedLetterResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
    },
)
def restore_red_letter_route(
    body: RestoreRedLetterRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    conn=Depends(db_connection),
) -> UnmarkRedLetterResponse:
    version = _parse_if_match(if_match)
    return restore_red_letter(conn, body, version)
