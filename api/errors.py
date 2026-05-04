"""Domain exception hierarchy.

Architect §HTTP API pins one error envelope: ``ErrorResponse {code, message,
details}``. Domain code raises subclasses of :class:`DomainError`; the
FastAPI handler in ``api.main`` maps the exception to the canonical JSON
shape with the right HTTP status. This is what stops handlers from drifting
into ``HTTPException(detail={...})`` (which produces ``{"detail": {...}}``
on the wire — the wrong shape).
"""
from __future__ import annotations


class DomainError(Exception):
    """Base for any domain-layer error that maps to an HTTP response.

    Subclasses set ``status_code``/``code``; instances optionally carry
    ``details`` (a dict that surfaces under ``ErrorResponse.details``).
    """

    status_code: int = 500
    code: str = "internal"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] | None = details


class SentenceNotFoundError(DomainError):
    status_code = 404
    code = "sentence_not_found"

    def __init__(self, sentence_id: str, fixture_version: str | None) -> None:
        super().__init__(
            message=f"sentence {sentence_id!r} is not in the current fixture set",
            details={"sentence_id": sentence_id, "fixture_version": fixture_version},
        )
        self.sentence_id = sentence_id
        self.fixture_version = fixture_version
