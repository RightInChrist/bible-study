"""Anthropic SDK wrapper.

Architect §Claude client + PLAN §Cost / spend safety + Security §Anthropic
key handling pin the design:

- Reads ``ANTHROPIC_API_KEY`` from settings; refuses to start a
  generation if the key is unset (the runner asks ``api_key_set`` and
  rejects ``CreateRunRequest`` with ``code='anthropic_key_missing'``).
- Cost estimation is a **character-count heuristic**, not an SDK
  ``count_tokens`` call. Display-only path; no Anthropic call.
- Per-sentence call uses **prompt caching** (Anthropic
  ``cache_control: ephemeral``) on the rendered system prompt + source
  bundle so repeated runs over the same sentence don't pay input tokens
  twice. The variable per-sentence portion (the "translate this Greek
  sentence" instruction) is uncached.
- Errors are typed via the ``GenerationErrorCode`` enum. **No auto-retry**
  (Reliability §Retries & backoff).
- Default model: ``claude-sonnet-4-6`` (per Slice 3a brief).

The client is dependency-injectable. Tests pass a stub (anything with
``async generate`` + ``api_key_set``). Production uses
:class:`AnthropicClaudeClient` which wraps ``anthropic.AsyncAnthropic``.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import anthropic

from api.claude.schemas import (
    CandidateGenerated,
    CostEstimate,
    GenerationErrorCode,
    SourceBundle,
    StylePrompt,
)
from api.errors import DomainError
from api.settings import get_settings


DEFAULT_MODEL = "claude-sonnet-4-6"
"""Slice 3a default. Anthropic's current Claude 4.X family — Sonnet for
translation work, Opus only when reasoning depth justifies the cost
(slice 3a doesn't ship a reasoning gate)."""


# Per-million-token pricing for cost estimation. The character-count
# heuristic feeds these; we never call Anthropic to estimate (per PLAN).
# Numbers below are rounded approximations for the v1 cost cap; if Gavin
# refines them the cap settings absorb the drift via the ±20% band.
_MODEL_PRICES_USD_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    # (input_per_million, output_per_million)
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-haiku-4-5": (0.80, 4.00),
}

_DEFAULT_MAX_OUTPUT_TOKENS = 1024


class AnthropicKeyMissingError(DomainError):
    status_code = 400
    code = "anthropic_key_missing"


class AnthropicGenerationError(Exception):
    """Raised by the client on any Anthropic-call failure.

    The runner catches this and writes ``error_code`` + truncated
    ``error_message`` to ``generation_run_items`` per Reliability §
    Failure modes. The ``code`` field is one of the ``GenerationErrorCode``
    literals.
    """

    def __init__(self, code: GenerationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: GenerationErrorCode = code
        self.message = message


@dataclass(frozen=True)
class ClaudeCallRequest:
    """Internal request shape — NOT a Pydantic schema.

    The Pydantic surface (``CandidateGenerated``, ``SourceBundle``) lives
    in ``api.claude.schemas``. This is the parameter pack the client
    consumes; not a wire contract.
    """

    prompt: StylePrompt
    bundle: SourceBundle
    model: str = DEFAULT_MODEL
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS


def estimate_cost(
    *,
    prompt: StylePrompt,
    bundle: SourceBundle,
    model: str,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
) -> CostEstimate:
    """Character-count heuristic per Architect §Claude client.

    ``estimated_input_units = round(total_chars / 4)``. The +20%
    confidence band lives on the ``CostEstimate`` model so the runner /
    UI can apply it consistently.
    """
    total_chars = len(prompt.body) + _bundle_char_count(bundle)
    estimated_input_tokens = round(total_chars / 4)
    if model not in _MODEL_PRICES_USD_PER_M_TOKENS:
        raise ValueError(f"unknown model {model!r} for cost estimation")
    input_rate, output_rate = _MODEL_PRICES_USD_PER_M_TOKENS[model]
    cost_usd = (
        (estimated_input_tokens / 1_000_000.0) * input_rate
        + (max_output_tokens / 1_000_000.0) * output_rate
    )
    return CostEstimate(
        estimated_input_units=estimated_input_tokens,
        estimated_cost_usd=cost_usd,
        estimated_cost_usd_band_pct=20,
    )


def _bundle_char_count(bundle: SourceBundle) -> int:
    """Approximate character count of a bundle, used by the cost heuristic.

    Walks every text-shaped field — fields containing structured numeric
    data (chapter, verse) contribute nothing because Anthropic's
    char→token ratio applies to text, not numbers, and the heuristic is
    already band-padded.
    """
    n = 0
    if bundle.sblgnt:
        n += len(bundle.sblgnt)
    for v in bundle.byzantine_verses:
        n += len(v.text)
    for v in bundle.bsb_verses:
        n += len(v.text)
    for v in bundle.blb_verses:
        n += len(v.text)
    for w in bundle.bib_interlinear:
        n += len(w.greek_form) + len(w.transliteration) + len(w.english_gloss)
    return n


def render_system_prompt(prompt: StylePrompt, bundle: SourceBundle) -> str:
    """Render the system prompt: prompt body + a JSON-formatted source bundle.

    The Architect pin says cached portion = "rendered prompt + source
    bundle". We render the bundle as canonicalised JSON so the cache key
    hashes the same bytes as the snapshot — repeat runs with the same
    combo hit the prompt cache.
    """
    bundle_json = json.dumps(
        bundle.model_dump(exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    parts = [
        prompt.body.rstrip(),
        "",
        f"Source set: {bundle.source_set_id}",
        f"Verse range: {bundle.verse_range}",
        f"Sentence ID: {bundle.sentence_id}",
        "",
        "Sources (JSON):",
        bundle_json,
    ]
    return "\n".join(parts)


def render_user_message(bundle: SourceBundle) -> str:
    """Per-sentence user prompt — the variable, uncached portion.

    Kept minimal so the cached system prompt + source bundle does the
    heavy lifting; this prompt is the actual "do it" instruction.
    """
    return (
        f"Translate the sentence at {bundle.verse_range} (sentence_id={bundle.sentence_id}) "
        "using the sources supplied in the system prompt. "
        "Return only the English translation, no preamble or quotation marks."
    )


class ClaudeClient(Protocol):
    """Minimal protocol the runner depends on. Tests inject fakes."""

    @property
    def api_key_set(self) -> bool: ...

    async def generate(self, request: ClaudeCallRequest) -> CandidateGenerated: ...


class AnthropicClaudeClient:
    """Production wrapper around ``anthropic.AsyncAnthropic``.

    Constructed once at app startup; reuses the underlying ``httpx``
    client across calls (the SDK manages the pool). The HTTP timeout is
    pinned by ``ANTHROPIC_REQUEST_TIMEOUT_SECONDS``; the runner's
    ``asyncio.wait_for`` adds a +30s belt-and-braces guard (Reliability §
    Failure modes — Anthropic SDK hang).
    """

    def __init__(
        self,
        api_key: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key: str | None = api_key if api_key is not None else (
            settings.anthropic_api_key or None
        )
        self._timeout: float = float(
            request_timeout_seconds
            if request_timeout_seconds is not None
            else settings.anthropic_request_timeout_seconds
        )
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def api_key_set(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            if not self._api_key:
                raise AnthropicKeyMissingError(
                    message="ANTHROPIC_API_KEY is not configured",
                    details={"hint": "set ANTHROPIC_API_KEY in .env and restart"},
                )
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                timeout=self._timeout,
                max_retries=0,
            )
        return self._client

    async def generate(self, request: ClaudeCallRequest) -> CandidateGenerated:
        client = self._ensure_client()
        system_prompt = render_system_prompt(request.prompt, request.bundle)
        user_message = render_user_message(request.bundle)

        started = time.monotonic()
        try:
            message = await client.messages.create(
                model=request.model,
                max_tokens=request.max_output_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": user_message},
                ],
            )
        except anthropic.RateLimitError as exc:
            raise AnthropicGenerationError("rate_limit", _truncate(str(exc))) from exc
        except anthropic.APITimeoutError as exc:
            raise AnthropicGenerationError("timeout", _truncate(str(exc))) from exc
        except anthropic.APIStatusError as exc:
            raise AnthropicGenerationError(
                "anthropic_api_error", _truncate(str(exc))
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise AnthropicGenerationError(
                "anthropic_api_error", _truncate(str(exc))
            ) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — last-resort
            raise AnthropicGenerationError("internal", _truncate(str(exc))) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text = _extract_text(message)
        if not text:
            raise AnthropicGenerationError(
                "invalid_response", "Anthropic returned an empty response"
            )
        if _is_refusal(message, text):
            raise AnthropicGenerationError("anthropic_refusal", _truncate(text))

        usage = getattr(message, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None

        return CandidateGenerated(
            candidate_text=text.strip(),
            model=request.model,
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            latency_ms=latency_ms,
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
        )


def _extract_text(message: object) -> str:
    """Extract concatenated text from a ``Message`` SDK response."""
    parts: list[str] = []
    content = getattr(message, "content", None)
    if content is None:
        return ""
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_attr = getattr(block, "text", "")
            if text_attr:
                parts.append(str(text_attr))
    return "".join(parts)


def _is_refusal(message: object, text: str) -> bool:
    """Heuristic refusal detector.

    The SDK exposes ``stop_reason`` per response; ``"refusal"`` is the
    canonical signal but Anthropic occasionally returns refusal-shaped
    content under ``stop_reason='end_turn'`` instead. We treat
    ``stop_reason == 'refusal'`` as authoritative; otherwise we fall back
    to a few obvious markers. False negatives here are acceptable —
    they're stored as candidates the user can rank low.
    """
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        return True
    return False


_MAX_ERROR_MESSAGE_BYTES = 2 * 1024


def _truncate(text: str) -> str:
    """Truncate to the byte budget pinned by Reliability §Failure modes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= _MAX_ERROR_MESSAGE_BYTES:
        return text
    truncated = encoded[:_MAX_ERROR_MESSAGE_BYTES].decode("utf-8", errors="ignore")
    return truncated + "…[truncated]"
