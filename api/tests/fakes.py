"""Test fakes for the Claude generation backend.

A real ``claude`` CLI subprocess is **never** spawned in unit tests —
that would make the suite non-hermetic and depend on the user's Claude
Code subscription state. Tests inject :class:`FakeWorktreeSpawner`;
routes resolve their dependency via ``app.dependency_overrides`` against
the ``get_worktree_spawner`` provider.

The fake honors all the same code paths as the real spawner (timeout,
parse-failure, internal-error simulation) and produces deterministic
output keyed off ``(sentence_id, prompt_version, source_set_id)`` so
eval-seeded runs are reproducible.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Awaitable, Callable

from api.claude.schemas import (
    ChapterBundle,
    ChapterMeta,
    GenerationErrorCode,
    SentenceMeta,
    SourceBundle,
    StylePrompt,
    WorktreeResult,
)
from api.claude.worktree import WorktreeGenerationError


@dataclass(frozen=True)
class FakeSpawnerCall:
    """One captured invocation — exposed so tests can assert on call shapes.

    For per-sentence calls, ``bundle`` is a :class:`SourceBundle` and
    ``sentence_meta`` is set; for chapter-summary calls, ``bundle`` is a
    :class:`ChapterBundle` and ``chapter_meta`` is set.
    """

    run_id: str
    ordinal: int
    prompt: StylePrompt
    bundle: SourceBundle | ChapterBundle
    sentence_meta: SentenceMeta | None
    chapter_meta: ChapterMeta | None
    timeout_seconds: int
    model: str
    effort: str


# Type for a custom per-call responder.
SpawnResponder = Callable[
    [FakeSpawnerCall], "Awaitable[WorktreeResult] | WorktreeResult"
]


@dataclass
class FakeWorktreeSpawner:
    """Deterministic stand-in for :class:`ClaudeCodeWorktreeSpawner`.

    Defaults to producing a fake candidate that round-trips:
      - ``output_format=text`` prompts → ``parsed_candidate={"english": <stub>}``
      - ``output_format=json`` prompts → a structured-fields stub matching
        the first-century-jewish-v1 shape, deterministic per
        ``(sentence_id, prompt_version, source_set_id)``.

    Tests can override:
      - ``responder`` to shape the response per-call (e.g. fail item #2)
      - ``cli_available_returns`` to simulate CLI absence
      - ``model`` to pin the reported model identity
    """

    cli_available_returns: bool = True
    # ``model`` is the override value the fake echoes back as the
    # WorktreeResult.model. When None (default), the fake builds the
    # composite ``"{model}+{effort}"`` from the call args, mirroring the
    # real spawner.
    model: str | None = None
    latency_seconds: float = 0.0
    responder: SpawnResponder | None = None
    calls: list[FakeSpawnerCall] = field(default_factory=list)

    def cli_available(self) -> bool:
        return self.cli_available_returns

    async def generate(
        self,
        *,
        run_id: str,
        ordinal: int,
        prompt: StylePrompt,
        bundle: SourceBundle,
        sentence_meta: SentenceMeta,
        timeout_seconds: int,
        model: str | None = None,
        effort: str | None = None,
    ) -> WorktreeResult:
        call = FakeSpawnerCall(
            run_id=run_id,
            ordinal=ordinal,
            prompt=prompt,
            bundle=bundle,
            sentence_meta=sentence_meta,
            chapter_meta=None,
            timeout_seconds=timeout_seconds,
            model=model or "claude-opus-4-7",
            effort=effort or "xhigh",
        )
        self.calls.append(call)
        if self.responder is not None:
            result = self.responder(call)
            if asyncio.iscoroutine(result):
                return await result
            assert isinstance(result, WorktreeResult)
            return result
        if self.latency_seconds > 0:
            await asyncio.sleep(self.latency_seconds)
        return self._default_result(call)

    async def generate_chapter(
        self,
        *,
        run_id: str,
        ordinal: int,
        prompt: StylePrompt,
        bundle: ChapterBundle,
        chapter_meta: ChapterMeta,
        timeout_seconds: int,
        model: str | None = None,
        effort: str | None = None,
    ) -> WorktreeResult:
        call = FakeSpawnerCall(
            run_id=run_id,
            ordinal=ordinal,
            prompt=prompt,
            bundle=bundle,
            sentence_meta=None,
            chapter_meta=chapter_meta,
            timeout_seconds=timeout_seconds,
            model=model or "claude-opus-4-7",
            effort=effort or "xhigh",
        )
        self.calls.append(call)
        if self.responder is not None:
            result = self.responder(call)
            if asyncio.iscoroutine(result):
                return await result
            assert isinstance(result, WorktreeResult)
            return result
        if self.latency_seconds > 0:
            await asyncio.sleep(self.latency_seconds)
        return self._default_chapter_result(call)

    def _default_result(self, call: FakeSpawnerCall) -> WorktreeResult:
        prompt = call.prompt
        bundle = call.bundle
        assert isinstance(bundle, SourceBundle)
        determinism_seed = hashlib.sha256(
            f"{bundle.sentence_id}|{prompt.version}|{bundle.source_set_id}".encode("utf-8")
        ).hexdigest()[:8]
        if prompt.output_format == "json":
            candidate = {
                "english": (
                    f"[fake] cultural-grounded translation of {bundle.sentence_id} "
                    f"({bundle.verse_range})"
                ),
                "underlying_hypothesis": (
                    f"[fake] no clear Semitic substrate signs in this fake stub "
                    f"(seed={determinism_seed})"
                ),
                "cultural_notes": (
                    f"[fake] cultural notes for {bundle.sentence_id}"
                ),
                "intertexts": [],
                "audience": "[fake] disciples",
                "pragmatic_act": "[fake] pronouncing a blessing",
                "confidence": "[fake] deterministic stub for tests",
            }
            raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        else:
            text = (
                f"[fake] {prompt.name} translation of {bundle.sentence_id} "
                f"({bundle.verse_range}) seed={determinism_seed}"
            )
            candidate = {"english": text}
            raw = text
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        reported_model = self.model if self.model is not None else f"{call.model}+{call.effort}"
        return WorktreeResult(
            raw_output=raw,
            parsed_candidate=candidate,
            model=reported_model,
            cli_version="claude-code-test-fake",
            started_at=now,
            completed_at=now,
            elapsed_seconds=self.latency_seconds,
        )


    def _default_chapter_result(self, call: FakeSpawnerCall) -> WorktreeResult:
        prompt = call.prompt
        bundle = call.bundle
        assert isinstance(bundle, ChapterBundle)
        determinism_seed = hashlib.sha256(
            f"chapter:{bundle.chapter}|{prompt.version}".encode("utf-8")
        ).hexdigest()[:8]
        consulted = [c.candidate_id for s in bundle.red_letter_candidates for c in s.candidates]
        candidate = {
            "summary": (
                f"[fake] Chapter {bundle.chapter} — synthesised summary "
                f"(seed={determinism_seed}). Two-to-three paragraphs would go here."
            ),
            "narrative_arc": "[fake] narrative arc placeholder",
            "audience_dynamics": "[fake] audience shifts placeholder",
            "cultural_throughline": "[fake] cultural threads placeholder",
            "rhetorical_strategy": "[fake] rhetorical move placeholder",
            "key_intertexts": [],
            "pragmatic_arc": "[fake] pragmatic arc placeholder",
            "key_sentences": [],
            "open_questions": (
                f"[fake] thin substrate ({len(consulted)} candidates consulted)"
                if not consulted
                else "[fake] open questions placeholder"
            ),
            "candidate_ids_consulted": consulted,
        }
        raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        reported_model = self.model if self.model is not None else f"{call.model}+{call.effort}"
        return WorktreeResult(
            raw_output=raw,
            parsed_candidate=candidate,
            model=reported_model,
            cli_version="claude-code-test-fake",
            started_at=now,
            completed_at=now,
            elapsed_seconds=self.latency_seconds,
        )


def raising_responder(
    code: GenerationErrorCode, message: str
) -> SpawnResponder:
    """Return a responder that always raises ``WorktreeGenerationError``."""

    async def _r(_call: FakeSpawnerCall) -> WorktreeResult:
        raise WorktreeGenerationError(code, message)

    return _r


def hanging_responder() -> SpawnResponder:
    """Return a responder that sleeps long enough to trip a timeout test."""

    async def _r(_call: FakeSpawnerCall) -> WorktreeResult:
        await asyncio.sleep(99999)
        raise RuntimeError("unreachable")

    return _r


def fixed_text_responder(text: str) -> SpawnResponder:
    """Return a responder that produces deterministic plain-text output."""

    async def _r(call: FakeSpawnerCall) -> WorktreeResult:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return WorktreeResult(
            raw_output=text,
            parsed_candidate={"english": text},
            model=f"{call.model}+{call.effort}",
            cli_version="claude-code-test-fake",
            started_at=now,
            completed_at=now,
            elapsed_seconds=0.0,
        )

    return _r
