"""Domain errors for the ranking feature.

All raise to the canonical ``ErrorResponse {code, message, details}``
envelope via the global ``DomainError`` handler in ``api.main``.
"""
from __future__ import annotations

from typing import Any

from api.errors import DomainError


class StaleRankingVersionError(DomainError):
    """Raised when ``If-Match`` does not match the current ``version``.

    Body's ``details`` carries ``current_version`` and ``current_entries``
    (and ``current_notes`` for the rankings row) so the conflict modal
    can render the server-side state without a second round-trip.
    """

    status_code = 409
    code = "stale_version"

    def __init__(self, current_version: int, current_state: dict[str, Any]) -> None:
        super().__init__(
            message=(
                "Ranking version is stale. Refresh and reapply your edits, "
                "or accept the server's current state."
            ),
            details={
                "current_version": current_version,
                **current_state,
            },
        )


class IfMatchRequiredError(DomainError):
    """Raised when a write endpoint is called without an ``If-Match`` header."""

    status_code = 428
    code = "if_match_required"

    def __init__(self) -> None:
        super().__init__(
            message=(
                "If-Match header is required for ranking writes. "
                "Use the version returned by GET /ranking."
            )
        )


class HiddenComboNotFoundError(DomainError):
    status_code = 404
    code = "hidden_combo_not_found"

    def __init__(self, combo_id: str) -> None:
        super().__init__(
            message=f"hidden combo {combo_id!r} not found",
            details={"combo_id": combo_id},
        )
