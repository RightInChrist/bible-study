"""HTTP routes for the ranking surface (Slice 4 — rank-core).

  - ``GET /api/v1/sentences/{id}/ranking`` — returns the full rank-page
    payload (entries + notes + available candidates + hidden combos).
  - ``PUT /api/v1/sentences/{id}/ranking`` — atomic save.
  - ``POST /api/v1/sentences/{id}/hidden-combos`` — hide a combo.
  - ``DELETE /api/v1/sentences/{id}/hidden-combos/{combo_id}`` — unhide.
  - ``POST /api/v1/sentences/{id}/tie-break`` — record a tie-break.

All writes require ``If-Match: <version>`` (Architect §HTTP API). The
service layer raises :class:`StaleRankingVersionError` on mismatch; the
global handler renders the ``code='stale_version'`` ErrorResponse.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from api.admin.schemas import ErrorResponse
from api.deps import db_connection
from api.rankings.errors import IfMatchRequiredError
from api.rankings.schemas import (
    HideComboRequest,
    RankingResponse,
    RankingWriteRequest,
    TieBreakRequest,
    TieBreakResponse,
)
from api.rankings.service import (
    get_ranking,
    hide_combo,
    post_tie_break,
    put_ranking,
    unhide_combo,
)


router = APIRouter(prefix="/api/v1/sentences", tags=["rankings"])


def _parse_if_match(if_match: str | None) -> int:
    if if_match is None:
        raise IfMatchRequiredError()
    raw = if_match.strip().strip('"')
    try:
        return int(raw)
    except ValueError as exc:
        raise IfMatchRequiredError() from exc


@router.get(
    "/{sentence_id}/ranking",
    response_model=RankingResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
)
def get_ranking_route(
    sentence_id: str,
    conn=Depends(db_connection),
) -> RankingResponse:
    return get_ranking(conn, sentence_id)


@router.put(
    "/{sentence_id}/ranking",
    response_model=RankingResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
    },
)
def put_ranking_route(
    sentence_id: str,
    body: RankingWriteRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    conn=Depends(db_connection),
) -> RankingResponse:
    version = _parse_if_match(if_match)
    return put_ranking(conn, sentence_id, version, body)


@router.post(
    "/{sentence_id}/hidden-combos",
    response_model=RankingResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
    },
)
def post_hidden_combo_route(
    sentence_id: str,
    body: HideComboRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    conn=Depends(db_connection),
) -> RankingResponse:
    version = _parse_if_match(if_match)
    return hide_combo(
        conn,
        sentence_id,
        version,
        style_prompt_version=body.style_prompt_version,
        source_set_id=body.source_set_id,
        model=body.model,
    )


@router.delete(
    "/{sentence_id}/hidden-combos/{combo_id}",
    response_model=RankingResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
    },
)
def delete_hidden_combo_route(
    sentence_id: str,
    combo_id: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    conn=Depends(db_connection),
) -> RankingResponse:
    version = _parse_if_match(if_match)
    return unhide_combo(conn, sentence_id, version, combo_id)


@router.post(
    "/{sentence_id}/tie-break",
    response_model=TieBreakResponse,
    response_model_exclude_none=True,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
    },
)
def post_tie_break_route(
    sentence_id: str,
    body: TieBreakRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    conn=Depends(db_connection),
) -> TieBreakResponse:
    version = _parse_if_match(if_match)
    return post_tie_break(conn, sentence_id, version, body)
