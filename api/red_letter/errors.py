"""Domain errors for the red-letter range editor."""
from __future__ import annotations

from typing import Any

from api.errors import DomainError


class CrossChapterRangeError(DomainError):
    """Designer Flow 3 / Error states: a range may not cross a chapter boundary."""

    status_code = 400
    code = "cross_chapter_range"

    def __init__(self, start_chapter: int, end_chapter: int) -> None:
        super().__init__(
            message=(
                "Cross-chapter ranges are not supported in v1. A range may span "
                "multiple sentences within a chapter, but cannot cross a chapter "
                "boundary."
            ),
            details={"start_chapter": start_chapter, "end_chapter": end_chapter},
        )


class InvalidVerseRangeError(DomainError):
    """Start verse > end verse, or no sentence intersects the requested verses."""

    status_code = 400
    code = "invalid_verse_range"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, details=details)


class StaleOverlayVersionError(DomainError):
    """OCC violation on an overlay-chain write.

    Two flavours: (a) the head's ``version`` no longer matches the
    ``If-Match`` token; (b) the chain-leaf race lost — another writer
    inserted a child off the same parent before us.
    """

    status_code = 409
    code = "stale_version"

    def __init__(self, current_version: int, details: dict[str, Any] | None = None) -> None:
        merged: dict[str, Any] = {"current_version": current_version}
        if details:
            merged.update(details)
        super().__init__(
            message=(
                "Red-letter overlay version is stale. Reload the editor to see "
                "the latest state."
            ),
            details=merged,
        )


class OverlayNotFoundError(DomainError):
    status_code = 404
    code = "overlay_not_found"

    def __init__(self, overlay_id: int) -> None:
        super().__init__(
            message=f"red-letter overlay {overlay_id} not found",
            details={"overlay_id": overlay_id},
        )


class SourceRangeNotFoundError(DomainError):
    status_code = 404
    code = "source_range_not_found"

    def __init__(self, source_range_id: int) -> None:
        super().__init__(
            message=f"red-letter source range {source_range_id} not found",
            details={"source_range_id": source_range_id},
        )


class IfMatchRequiredError(DomainError):
    status_code = 428
    code = "if_match_required"

    def __init__(self) -> None:
        super().__init__(
            message=(
                "If-Match header is required for red-letter overlay writes. "
                "Use the version returned by the chain head."
            )
        )
