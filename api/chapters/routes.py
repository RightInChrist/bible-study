"""HTTP routes for chapter summaries (Slice 8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from api.admin.schemas import ErrorResponse
from api.chapters.schemas import (
    ChapterSummaryListResponse,
    ChapterSummaryResponse,
)
from api.chapters.service import (
    ChapterSummaryNotFoundError,
    get_summary,
    list_summaries_for_chapter,
)
from api.deps import db_connection


router = APIRouter(prefix="/api/v1", tags=["chapter-summaries"])


@router.get(
    "/chapters/{chapter}/summaries",
    response_model=ChapterSummaryListResponse,
    response_model_exclude_none=True,
    responses={400: {"model": ErrorResponse}},
)
def list_chapter_summaries_route(
    chapter: int = Path(..., ge=1, le=28),
    conn=Depends(db_connection),
) -> ChapterSummaryListResponse:
    return list_summaries_for_chapter(conn, chapter)


@router.get(
    "/chapters/{chapter}/summaries/{summary_id}",
    response_model=ChapterSummaryResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
)
def get_chapter_summary_route(
    chapter: int = Path(..., ge=1, le=28),
    summary_id: int = Path(..., ge=1),
    conn=Depends(db_connection),
) -> ChapterSummaryResponse:
    summary = get_summary(conn, summary_id)
    if summary.chapter != chapter:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "chapter_summary_not_found",
                "message": (
                    f"chapter summary {summary_id} is not for chapter {chapter}"
                ),
                "details": {"summary_id": summary_id, "chapter": chapter},
            },
        )
    return summary
