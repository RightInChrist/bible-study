"""Test fakes for the Claude generation backend.

A real Anthropic SDK call is **never** made in unit tests (PLAN
§Anthropic key handling, Reliability §Retries & backoff: tests stub
the client). Tests inject :class:`FakeClaudeClient`; routes resolve
their dependency via ``app.dependency_overrides``.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Awaitable, Callable

from api.claude.client import (
    AnthropicGenerationError,
    ClaudeCallRequest,
    ClaudeClient,
)
from api.claude.schemas import CandidateGenerated, GenerationErrorCode


class FakeClaudeClient:
    """Minimal :class:`ClaudeClient` fake.

    Configured with either:
      - a fixed ``candidate_text`` string (returned for every call), or
      - a ``responder`` callback ``request -> CandidateGenerated`` for
        per-call shaping (e.g. fail the second sentence, succeed others).
    """

    def __init__(
        self,
        *,
        candidate_text: str = "fake-translation",
        api_key_set: bool = True,
        latency_ms: int = 5,
        responder: Callable[[ClaudeCallRequest], Awaitable[CandidateGenerated]] | None = None,
    ) -> None:
        self._candidate_text = candidate_text
        self._api_key_set = api_key_set
        self._latency_ms = latency_ms
        self._responder = responder
        self.calls: list[ClaudeCallRequest] = []

    @property
    def api_key_set(self) -> bool:
        return self._api_key_set

    async def generate(self, request: ClaudeCallRequest) -> CandidateGenerated:
        self.calls.append(request)
        if self._responder is not None:
            return await self._responder(request)
        return CandidateGenerated(
            candidate_text=self._candidate_text,
            model=request.model,
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            latency_ms=self._latency_ms,
        )


def raising_responder(
    code: GenerationErrorCode, message: str
) -> Callable[[ClaudeCallRequest], Awaitable[CandidateGenerated]]:
    """Return a responder that always raises ``AnthropicGenerationError``."""

    async def _r(_request: ClaudeCallRequest) -> CandidateGenerated:
        raise AnthropicGenerationError(code, message)

    return _r


def hanging_responder() -> Callable[[ClaudeCallRequest], Awaitable[CandidateGenerated]]:
    """Return a responder that sleeps forever (used for timeout tests)."""

    async def _r(_request: ClaudeCallRequest) -> CandidateGenerated:
        await asyncio.sleep(99999)
        raise RuntimeError("unreachable")

    return _r
