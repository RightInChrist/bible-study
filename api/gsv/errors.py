"""Domain errors for the GSV feature."""
from __future__ import annotations

from api.errors import DomainError


class UnresolvedTiesError(DomainError):
    """Raised when plain-text export is requested for a chapter that has
    one or more sentences with unresolved ties.

    Architect §Contracts: the plain-text export refuses to write while
    unresolved ties exist (per Designer Flow 6 step 4 + Edge cases).
    """

    status_code = 409
    code = "unresolved_ties"

    def __init__(self, chapter: int, offending_sentences: list[str]) -> None:
        super().__init__(
            message=(
                f"Plain-text GSV for chapter {chapter} cannot render — "
                f"{len(offending_sentences)} sentence(s) have unresolved ties. "
                "Resolve ties at /sentence/{id}/rank or pick a different format."
            ),
            details={
                "chapter": chapter,
                "offending_sentences": offending_sentences,
            },
        )


class GsvChapterOutOfRangeError(DomainError):
    """Raised when ``chapter`` is outside Matthew's 1..28 range."""

    status_code = 404
    code = "gsv_chapter_out_of_range"

    def __init__(self, chapter: int) -> None:
        super().__init__(
            message=f"chapter {chapter} is outside Matthew's range (1..28)",
            details={"chapter": chapter},
        )
