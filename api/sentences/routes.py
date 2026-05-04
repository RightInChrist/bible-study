from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.admin.schemas import ErrorResponse
from api.deps import db_connection
from api.sentences.schemas import SentenceListResponse, SentenceParallelResponse
from api.sentences.service import (
    get_sentence_parallel,
    list_sentences_in_chapter,
)


router = APIRouter(prefix="/api/v1/sentences", tags=["sentences"])


@router.get(
    "",
    response_model=SentenceListResponse,
    response_model_exclude_none=True,
)
def list_sentences(
    chapter: int = Query(..., ge=1, le=28),
    conn=Depends(db_connection),
) -> SentenceListResponse:
    return list_sentences_in_chapter(conn, chapter)


@router.get(
    "/{sentence_id}/parallel",
    response_model=SentenceParallelResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
)
def sentence_parallel(
    sentence_id: str,
    conn=Depends(db_connection),
) -> SentenceParallelResponse:
    # ``SentenceNotFoundError`` is a ``DomainError``; the global exception
    # handler in ``api.main`` re-shapes it to the canonical ErrorResponse.
    return get_sentence_parallel(conn, sentence_id)
