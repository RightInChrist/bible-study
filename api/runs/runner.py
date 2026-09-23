"""In-process generation runner (Slice 3a-redo).

CLAUDE.md §Generation mechanism + Architect §Generation runner:

- One ``asyncio`` task per sentence within a run, bounded by an
  ``asyncio.Semaphore(MAX_CONCURRENT_WORKTREES)``.
- Per-sentence: a fresh git worktree under ``data/worktrees/{run_id}/{ordinal}/``,
  the ``claude`` CLI is spawned in non-interactive print mode against
  the worktree, stdout is parsed, and the worktree is removed.
- Per-sentence transaction: ``BEGIN IMMEDIATE`` →
  ``INSERT OR IGNORE source_snapshots`` → ``INSERT claude_candidates`` →
  ``UPDATE generation_run_items SET status='completed', candidate_id=?``.
- ``hidden_combos`` skip path: matches a row → status='cancelled',
  ``error_code=NULL`` (the schema CHECK forbids ``error_code`` on a
  non-failed status).
- No auto-retry on subagent errors.
- SSE event broadcast is a per-run ``asyncio.Queue`` register; the SSE
  endpoint subscribes and replays from the DB before attaching live.

Run-level cost surface:
- ``estimated_worktree_count`` replaces ``estimated_cost_usd``. It's the
  number of worktrees the run will spawn (== sentences in scope). The
  legacy ``generation_runs.estimated_cost_usd_x10000`` column is kept
  by the schema; we always write 0 and ignore it on read paths
  (migration ``0002_runs_no_dollar_cost`` repurposes the column to
  ``estimated_worktree_count`` storage).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from api.claude.prompts import load_prompt
from api.claude.schemas import (
    ChapterBundle,
    ChapterMeta,
    GenerationErrorCode,
    SentenceMeta,
    SourceBundle,
    SourceSetId,
    StylePrompt,
)
from api.claude.sources import (
    IncompatibleSourceSetError,
    build_chapter_bundle,
    resolve_source_bundle,
    upsert_chapter_snapshot,
    upsert_snapshot,
    validate_compatibility,
)
from api.claude.worktree import (
    ClaudeCliUnavailableError,
    WorktreeGenerationError,
    WorktreeSpawner,
)
from api.db.connection import open_connection
from api.errors import DomainError
from api.logging import get_logger
from api.runs.schemas import RunScope
from api.settings import get_settings


_logger = get_logger("bible_study.runner")


class RunCreationError(DomainError):
    """Errors that reject a run before it is dispatched.

    ``code`` is one of:
      - ``claude_cli_unavailable``
      - ``unknown_style_prompt``
      - ``incompatible_source_set``
      - ``invalid_scope``
      - ``empty_scope``
      - ``max_sentences_per_run_exceeded`` / ``max_worktrees_per_run_exceeded``
      - ``max_runs_per_day_exceeded``
    """

    status_code = 400
    code = "run_creation_failed"

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message=message, details=details)
        self.code = code


class RunNotFoundError(DomainError):
    status_code = 404
    code = "run_not_found"


@dataclass
class _RunBroadcaster:
    """Per-run in-process pub-sub for SSE clients."""

    run_id: str
    subscribers: set[asyncio.Queue[dict[str, object]]] = field(default_factory=set)
    terminated: bool = False

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        q: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, object]]) -> None:
        self.subscribers.discard(q)

    def emit(self, payload: dict[str, object]) -> None:
        for q in self.subscribers:
            q.put_nowait(payload)


@dataclass
class _RunnerState:
    """Process-wide registry of in-flight runs.

    One :class:`_RunBroadcaster` per active run. The runner owns the
    map; the SSE endpoint reads it. Both share the same event loop.
    """

    broadcasters: dict[str, _RunBroadcaster] = field(default_factory=dict)


_state = _RunnerState()


def get_broadcaster(run_id: str) -> _RunBroadcaster | None:
    return _state.broadcasters.get(run_id)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _truncate_error_message(message: str) -> str:
    settings = get_settings()
    cap = settings.max_error_message_bytes
    encoded = message.encode("utf-8")
    if len(encoded) <= cap:
        return message
    return encoded[:cap].decode("utf-8", errors="ignore") + "…[truncated]"


def _load_style_prompt(project_root: Path, prompt_version: str) -> StylePrompt:
    """Read the prompt fixture from disk by version."""
    path = project_root / "fixtures" / "prompts" / f"{prompt_version}.md"
    if not path.exists():
        raise RunCreationError(
            code="unknown_style_prompt",
            message=f"prompt {prompt_version!r} not found at {path.relative_to(project_root)}",
            details={"prompt_version": prompt_version},
        )
    return load_prompt(path)


def _expand_scope(conn: sqlite3.Connection, scope: RunScope) -> list[str]:
    """Expand a ``RunScope`` into an ordered list of sentence IDs.

    Chapter-summary scope returns an empty list — its dispatch unit is
    one chapter, not N sentences. The runner detects ``scope.kind ==
    'chapter_summary'`` and dispatches a single chapter item instead.
    """
    kind = scope.kind
    if kind == "chapter_summary":
        return []
    if kind == "one_sentence":
        row = conn.execute(
            "SELECT sentence_id FROM sentences WHERE sentence_id = ?",
            (scope.sentence_id,),  # type: ignore[union-attr]
        ).fetchone()
        if row is None:
            raise RunCreationError(
                code="invalid_scope",
                message=f"sentence {scope.sentence_id!r} not in current fixtures",  # type: ignore[union-attr]
                details={"sentence_id": scope.sentence_id},  # type: ignore[union-attr]
            )
        return [row["sentence_id"]]
    if kind == "verse_range":
        if scope.start_verse > scope.end_verse:  # type: ignore[union-attr]
            raise RunCreationError(
                code="invalid_scope",
                message="start_verse must be <= end_verse",
            )
        rows = conn.execute(
            """
            SELECT sentence_id FROM sentences
            WHERE chapter = ? AND end_verse >= ? AND start_verse <= ?
            ORDER BY ordinal_in_chapter
            """,
            (scope.chapter, scope.start_verse, scope.end_verse),  # type: ignore[union-attr]
        ).fetchall()
        return [r["sentence_id"] for r in rows]
    if kind == "whole_chapter":
        rows = conn.execute(
            """
            SELECT sentence_id FROM sentences
            WHERE chapter = ?
            ORDER BY ordinal_in_chapter
            """,
            (scope.chapter,),  # type: ignore[union-attr]
        ).fetchall()
        return [r["sentence_id"] for r in rows]
    if kind == "all_unranked_red_letter":
        rows = conn.execute(
            """
            SELECT s.sentence_id
            FROM sentences s
            WHERE EXISTS (
              SELECT 1 FROM red_letter_source_ranges rls
              JOIN sentences ss ON ss.sentence_id = rls.start_sentence_id
              JOIN sentences se ON se.sentence_id = rls.end_sentence_id
              WHERE (s.chapter, s.ordinal_in_chapter)
                BETWEEN (ss.chapter, ss.ordinal_in_chapter)
                AND (se.chapter, se.ordinal_in_chapter)
            )
            AND NOT EXISTS (
              SELECT 1 FROM rankings r WHERE r.sentence_id = s.sentence_id
            )
            ORDER BY s.chapter, s.ordinal_in_chapter
            """
        ).fetchall()
        return [r["sentence_id"] for r in rows]
    raise ValueError(f"unknown scope kind: {kind}")


def _scope_with_as_of(scope: RunScope) -> RunScope:
    if scope.kind == "all_unranked_red_letter" and scope.as_of is None:  # type: ignore[union-attr]
        return scope.model_copy(update={"as_of": _now_iso()})
    return scope


def _check_hidden_combo(
    conn: sqlite3.Connection,
    *,
    sentence_id: str,
    style_prompt_version: str,
    source_set_id: SourceSetId,
    model: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM hidden_combos
        WHERE sentence_id = ?
          AND style_prompt_version = ?
          AND source_set_id = ?
          AND model = ?
        """,
        (sentence_id, style_prompt_version, source_set_id, model),
    ).fetchone()
    return row is not None


@dataclass(frozen=True)
class CreateRunResult:
    run_id: str
    sentence_ids: list[str]
    estimated_worktree_count: int
    wants_context_window: bool
    context_window_before: int
    context_window_after: int
    chapter: int | None = None


def create_run(
    *,
    conn: sqlite3.Connection,
    scope: RunScope,
    style_prompt_version: str,
    source_set_id: str,
    model: str,
    effort: str,
    spawner: WorktreeSpawner,
    project_root: Path,
    parent_run_id: str | None = None,
) -> CreateRunResult:
    """Synchronously persist a new run + items; caller schedules dispatch.

    Validation order (server-enforced):
      1. ``claude`` CLI is reachable (``spawner.cli_available()``).
      2. Style prompt exists.
      3. Source-set is in the prompt's ``compatible_source_sets``.
      4. Scope expands to >=1 sentence and <= ``MAX_WORKTREES_PER_RUN``.
      5. 24h rolling run count <= ``MAX_RUNS_PER_DAY``.

    ``model`` is the requested base model id (e.g. ``claude-opus-4-7``);
    ``effort`` is the requested reasoning level (e.g. ``xhigh``). The
    spawner pins both via ``claude --model`` / ``--effort`` and reports
    a composite ``"{model}+{effort}"`` provenance string per candidate.
    The same composite is stored on the ``generation_runs`` row so the
    run-level and candidate-level provenance match.
    """
    settings = get_settings()
    if not spawner.cli_available():
        raise RunCreationError(
            code="claude_cli_unavailable",
            message=(
                "claude CLI is not reachable from this process — install "
                "Claude Code and ensure `claude --version` succeeds."
            ),
            details={"hint": f"set CLAUDE_CLI_PATH (default: 'claude') and re-run"},
        )

    prompt = _load_style_prompt(project_root, style_prompt_version)
    try:
        validate_compatibility(prompt, source_set_id)
    except IncompatibleSourceSetError as exc:
        raise RunCreationError(
            code="incompatible_source_set",
            message=exc.message,
            details=exc.details,
        ) from exc
    if scope.kind not in prompt.compatible_run_scopes:
        raise RunCreationError(
            code="incompatible_source_set",
            message=(
                f"prompt {prompt.version!r} does not declare "
                f"{scope.kind!r} in its compatible_run_scopes "
                f"({prompt.compatible_run_scopes})"
            ),
            details={
                "prompt_version": prompt.version,
                "scope_kind": scope.kind,
                "compatible_run_scopes": list(prompt.compatible_run_scopes),
            },
        )

    captured_scope = _scope_with_as_of(scope)
    is_chapter_scope = captured_scope.kind == "chapter_summary"
    if is_chapter_scope:
        sentence_ids = []
        chapter_value: int | None = int(captured_scope.chapter)  # type: ignore[union-attr]
        if source_set_id != "CHAPTER_BUNDLE":
            raise RunCreationError(
                code="incompatible_source_set",
                message=(
                    f"chapter_summary scope requires source_set_id="
                    f"'CHAPTER_BUNDLE'; got {source_set_id!r}"
                ),
                details={"source_set_id": source_set_id, "scope_kind": "chapter_summary"},
            )
    else:
        sentence_ids = _expand_scope(conn, captured_scope)
        chapter_value = None
        if not sentence_ids:
            raise RunCreationError(
                code="empty_scope",
                message="run scope expanded to zero sentences",
                details={"scope": captured_scope.model_dump()},
            )
        if len(sentence_ids) > settings.max_worktrees_per_run:
            raise RunCreationError(
                code="max_worktrees_per_run_exceeded",
                message=(
                    f"scope expanded to {len(sentence_ids)} sentences; cap is "
                    f"{settings.max_worktrees_per_run} (MAX_WORKTREES_PER_RUN)"
                ),
                details={
                    "items_count": len(sentence_ids),
                    "max_worktrees_per_run": settings.max_worktrees_per_run,
                },
            )

    # 24h rolling: count of generation_runs created in the last 24h.
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM generation_runs WHERE created_at >= ?",
        (_iso_24h_ago(),),
    ).fetchone()
    daily_count = int(row["n"])
    if daily_count >= settings.max_runs_per_day:
        raise RunCreationError(
            code="max_runs_per_day_exceeded",
            message=(
                f"already created {daily_count} runs in the last 24h; cap is "
                f"{settings.max_runs_per_day} (MAX_RUNS_PER_DAY)"
            ),
            details={
                "daily_count": daily_count,
                "max_runs_per_day": settings.max_runs_per_day,
            },
        )

    wants_context_window = prompt.wants_context_window
    context_window_before = settings.context_window_before if wants_context_window else 0
    context_window_after = settings.context_window_after if wants_context_window else 0

    if is_chapter_scope:
        # Validate the chapter bundle resolves at run-creation time —
        # fails fast on an empty chapter rather than mid-dispatch.
        assert chapter_value is not None
        build_chapter_bundle(
            conn,
            chapter=chapter_value,
            prompt_version=style_prompt_version,
        )
    else:
        # Validate the source bundle resolves for the first sentence —
        # fails fast on a fixture-mismatch rather than mid-dispatch.
        resolve_source_bundle(
            conn,
            sentence_id=sentence_ids[0],
            source_set_id=source_set_id,  # type: ignore[arg-type]
            prompt_version=style_prompt_version,
            wants_context_window=wants_context_window,
            context_window_before=context_window_before,
            context_window_after=context_window_after,
        )

    run_id = str(uuid.uuid4())
    created_at = _now_iso()
    estimated_worktree_count = 1 if is_chapter_scope else len(sentence_ids)
    composite_model = f"{model}+{effort}"

    scope_json = json.dumps(
        captured_scope.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        # ``estimated_cost_usd_x10000`` and ``estimated_input_units`` are
        # legacy columns from slice-3a-pre-redo; we keep the column names
        # for forward compatibility with the migration but store the
        # worktree count as the cost-proxy unit. Migration 0002 drops
        # ``estimated_input_units``.
        conn.execute(
            """
            INSERT INTO generation_runs (
                run_id, status, scope_json, style_prompt_version,
                source_set_id, model, estimated_worktree_count,
                parent_run_id, created_at
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                scope_json,
                style_prompt_version,
                source_set_id,
                composite_model,
                estimated_worktree_count,
                parent_run_id,
                created_at,
            ),
        )
        if is_chapter_scope:
            conn.execute(
                """
                INSERT INTO generation_run_items (
                    run_id, ordinal, sentence_id, chapter, status
                ) VALUES (?, 1, NULL, ?, 'pending')
                """,
                (run_id, chapter_value),
            )
        else:
            conn.executemany(
                """
                INSERT INTO generation_run_items (
                    run_id, ordinal, sentence_id, chapter, status
                ) VALUES (?, ?, ?, NULL, 'pending')
                """,
                [
                    (run_id, ordinal, sentence_id)
                    for ordinal, sentence_id in enumerate(sentence_ids, start=1)
                ],
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    _logger.info(
        "run.created",
        extra={
            "run_id": run_id,
            "items_count": len(sentence_ids),
            "estimated_worktree_count": estimated_worktree_count,
            "style_prompt_version": style_prompt_version,
            "source_set_id": source_set_id,
            "model": model,
            "effort": effort,
            "composite_model": composite_model,
            "wants_context_window": wants_context_window,
            "context_window_before": context_window_before,
            "context_window_after": context_window_after,
        },
    )

    return CreateRunResult(
        run_id=run_id,
        sentence_ids=list(sentence_ids),
        estimated_worktree_count=estimated_worktree_count,
        wants_context_window=wants_context_window,
        context_window_before=context_window_before,
        context_window_after=context_window_after,
        chapter=chapter_value,
    )


def _iso_24h_ago() -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Dispatch loop
# ---------------------------------------------------------------------------


@dataclass
class _RunContext:
    run_id: str
    sentence_ids: list[str]
    style_prompt_version: str
    source_set_id: str
    model: str
    effort: str
    project_root: Path
    db_path: Path | None
    spawner: WorktreeSpawner
    wants_context_window: bool = False
    context_window_before: int = 0
    context_window_after: int = 0
    chapter: int | None = None

    @property
    def composite_model(self) -> str:
        """Run-level provenance string — matches what create_run wrote."""
        return f"{self.model}+{self.effort}"

    @property
    def is_chapter_scope(self) -> bool:
        return self.chapter is not None


async def dispatch_run(ctx: _RunContext) -> None:
    """Top-level dispatcher: claim 'running', fan out per-sentence tasks,
    update terminal status, emit terminal SSE event.
    """
    settings = get_settings()
    broadcaster = _state.broadcasters.setdefault(ctx.run_id, _RunBroadcaster(ctx.run_id))

    started_at = _now_iso()
    with _connect(ctx.db_path) as conn:
        conn.execute(
            """
            UPDATE generation_runs SET status='running', started_at=?
            WHERE run_id=? AND status='pending'
            """,
            (started_at, ctx.run_id),
        )

    _logger.info(
        "run.dispatch.started",
        extra={"run_id": ctx.run_id, "items_count": len(ctx.sentence_ids)},
    )

    sem = asyncio.Semaphore(int(settings.max_concurrent_worktrees))
    prompt = load_prompt(
        ctx.project_root / "fixtures" / "prompts" / f"{ctx.style_prompt_version}.md"
    )

    tasks: list[asyncio.Task[None]] = []
    if ctx.is_chapter_scope:
        assert ctx.chapter is not None
        tasks.append(
            asyncio.create_task(
                _run_one_chapter_item(
                    ctx=ctx,
                    ordinal=1,
                    chapter=ctx.chapter,
                    prompt=prompt,
                    sem=sem,
                    broadcaster=broadcaster,
                )
            )
        )
    else:
        for ordinal, sentence_id in enumerate(ctx.sentence_ids, start=1):
            tasks.append(
                asyncio.create_task(
                    _run_one_item(
                        ctx=ctx,
                        ordinal=ordinal,
                        sentence_id=sentence_id,
                        prompt=prompt,
                        sem=sem,
                        broadcaster=broadcaster,
                    )
                )
            )

    try:
        await asyncio.gather(*tasks, return_exceptions=False)
    except Exception:  # noqa: BLE001
        _set_run_status(ctx, status="failed")
        broadcaster.emit({"event": "run_failed", "data": {"run_id": ctx.run_id}})
        broadcaster.terminated = True
        _logger.error("run.failed", extra={"run_id": ctx.run_id}, exc_info=True)
        return

    final = _finalise_run_status(ctx)
    payload: dict[str, object] = {"run_id": ctx.run_id}
    if final == "completed":
        broadcaster.emit({"event": "run_finished", "data": {**payload, "status": "completed"}})
    elif final == "failed":
        broadcaster.emit({"event": "run_failed", "data": payload})
    elif final == "cancelled":
        broadcaster.emit({"event": "run_cancelled", "data": payload})
    elif final == "interrupted":
        broadcaster.emit({"event": "run_interrupted", "data": payload})
    else:
        broadcaster.emit({"event": "run_finished", "data": {**payload, "status": final}})
    broadcaster.terminated = True

    _logger.info("run.finished", extra={"run_id": ctx.run_id, "status": final})


async def _run_one_item(
    *,
    ctx: _RunContext,
    ordinal: int,
    sentence_id: str,
    prompt: StylePrompt,
    sem: asyncio.Semaphore,
    broadcaster: _RunBroadcaster,
) -> None:
    settings = get_settings()
    async with sem:
        with _connect(ctx.db_path) as conn:
            run_row = conn.execute(
                "SELECT status FROM generation_runs WHERE run_id=?",
                (ctx.run_id,),
            ).fetchone()
            if run_row is None:
                return
            if run_row["status"] not in ("running", "pending"):
                return
            cur = conn.execute(
                """
                UPDATE generation_run_items SET status='running', started_at=?
                WHERE run_id=? AND ordinal=? AND status='pending'
                """,
                (_now_iso(), ctx.run_id, ordinal),
            )
            if cur.rowcount == 0:
                return

            if _check_hidden_combo(
                conn,
                sentence_id=sentence_id,
                style_prompt_version=ctx.style_prompt_version,
                source_set_id=ctx.source_set_id,
                model=ctx.composite_model,
            ):
                conn.execute(
                    """
                    UPDATE generation_run_items
                    SET status='cancelled', completed_at=?
                    WHERE run_id=? AND ordinal=? AND status='running'
                    """,
                    (_now_iso(), ctx.run_id, ordinal),
                )
                broadcaster.emit(
                    {
                        "event": "item_cancelled",
                        "data": {
                            "run_id": ctx.run_id,
                            "ordinal": ordinal,
                            "sentence_id": sentence_id,
                            "reason": "hidden_combo_skipped",
                        },
                    }
                )
                _logger.info(
                    "run.item.skipped_hidden",
                    extra={
                        "run_id": ctx.run_id,
                        "ordinal": ordinal,
                        "sentence_id": sentence_id,
                    },
                )
                return

            try:
                bundle = resolve_source_bundle(
                    conn,
                    sentence_id=sentence_id,
                    source_set_id=ctx.source_set_id,
                    prompt_version=ctx.style_prompt_version,
                    wants_context_window=ctx.wants_context_window,
                    context_window_before=ctx.context_window_before,
                    context_window_after=ctx.context_window_after,
                )
                sentence_row = conn.execute(
                    """
                    SELECT chapter, start_verse, end_verse FROM sentences
                    WHERE sentence_id = ?
                    """,
                    (sentence_id,),
                ).fetchone()
            except DomainError as exc:
                _mark_item_failed(
                    conn,
                    run_id=ctx.run_id,
                    ordinal=ordinal,
                    error_code="internal",
                    error_message=exc.message,
                )
                _emit_failed(broadcaster, ctx.run_id, ordinal, sentence_id, "internal", exc.message)
                return

        sentence_meta = SentenceMeta(
            sentence_id=sentence_id,
            chapter=int(sentence_row["chapter"]),
            start_verse=int(sentence_row["start_verse"]),
            end_verse=int(sentence_row["end_verse"]),
            verse_range=bundle.verse_range,
        )

        _logger.info(
            "run.item.started",
            extra={
                "run_id": ctx.run_id,
                "ordinal": ordinal,
                "sentence_id": sentence_id,
            },
        )

        timeout_seconds = int(settings.max_wall_clock_seconds_per_sentence)
        try:
            result = await ctx.spawner.generate(
                run_id=ctx.run_id,
                ordinal=ordinal,
                prompt=prompt,
                bundle=bundle,
                sentence_meta=sentence_meta,
                timeout_seconds=timeout_seconds,
                model=ctx.model,
                effort=ctx.effort,
            )
        except WorktreeGenerationError as exc:
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code=exc.code,
                error_message=exc.message,
            )
            _emit_failed(broadcaster, ctx.run_id, ordinal, sentence_id, exc.code, exc.message)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code="internal",
                error_message=str(exc),
            )
            _emit_failed(broadcaster, ctx.run_id, ordinal, sentence_id, "internal", str(exc))
            return

        # Persist: store the parsed_candidate as canonical JSON in
        # ``candidate_text`` (the existing TEXT column). For text-format
        # prompts that's ``{"english": "..."}``-equivalent — we instead
        # store the bare English string so legacy candidates round-trip
        # cleanly. JSON-format prompts get the full dict.
        if prompt.output_format == "json":
            candidate_text = json.dumps(
                result.parsed_candidate, ensure_ascii=False, sort_keys=True
            )
        else:
            english = result.parsed_candidate.get("english", "")
            candidate_text = str(english).strip()

        latency_ms = int(result.elapsed_seconds * 1000)
        try:
            candidate_id = _commit_item_success(
                ctx,
                ordinal=ordinal,
                sentence_id=sentence_id,
                bundle=bundle,
                model=result.model,
                candidate_text=candidate_text,
                generated_at=result.completed_at,
                latency_ms=latency_ms,
            )
        except sqlite3.OperationalError as exc:
            error_code: GenerationErrorCode = (
                "disk_full" if "disk is full" in str(exc).lower() else "internal"
            )
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code=error_code,
                error_message=str(exc),
            )
            _emit_failed(broadcaster, ctx.run_id, ordinal, sentence_id, error_code, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code="internal",
                error_message=str(exc),
            )
            _emit_failed(broadcaster, ctx.run_id, ordinal, sentence_id, "internal", str(exc))
            return

        broadcaster.emit(
            {
                "event": "item_completed",
                "data": {
                    "run_id": ctx.run_id,
                    "ordinal": ordinal,
                    "sentence_id": sentence_id,
                    "candidate_id": candidate_id,
                    "latency_ms": latency_ms,
                    "generated_at": result.completed_at,
                },
            }
        )
        _logger.info(
            "run.item.completed",
            extra={
                "run_id": ctx.run_id,
                "ordinal": ordinal,
                "sentence_id": sentence_id,
                "candidate_id": candidate_id,
                "duration_ms": latency_ms,
            },
        )


async def _run_one_chapter_item(
    *,
    ctx: _RunContext,
    ordinal: int,
    chapter: int,
    prompt: StylePrompt,
    sem: asyncio.Semaphore,
    broadcaster: _RunBroadcaster,
) -> None:
    """Dispatch the single chapter-summary item for a chapter scope (Slice 8).

    Mirrors :func:`_run_one_item` but with chapter-bundle resolution and
    the ``chapter_summaries`` write path.
    """
    settings = get_settings()
    async with sem:
        with _connect(ctx.db_path) as conn:
            run_row = conn.execute(
                "SELECT status FROM generation_runs WHERE run_id=?",
                (ctx.run_id,),
            ).fetchone()
            if run_row is None:
                return
            if run_row["status"] not in ("running", "pending"):
                return
            cur = conn.execute(
                """
                UPDATE generation_run_items SET status='running', started_at=?
                WHERE run_id=? AND ordinal=? AND status='pending'
                """,
                (_now_iso(), ctx.run_id, ordinal),
            )
            if cur.rowcount == 0:
                return

            try:
                bundle = build_chapter_bundle(
                    conn,
                    chapter=chapter,
                    prompt_version=ctx.style_prompt_version,
                )
            except DomainError as exc:
                _mark_item_failed(
                    conn,
                    run_id=ctx.run_id,
                    ordinal=ordinal,
                    error_code="internal",
                    error_message=exc.message,
                )
                _emit_failed_chapter(
                    broadcaster, ctx.run_id, ordinal, chapter, "internal", exc.message
                )
                return

        chapter_meta = ChapterMeta(chapter=chapter)

        _logger.info(
            "run.item.started",
            extra={
                "run_id": ctx.run_id,
                "ordinal": ordinal,
                "chapter": chapter,
                "scope": "chapter_summary",
            },
        )

        timeout_seconds = int(settings.max_wall_clock_seconds_per_sentence)
        try:
            result = await ctx.spawner.generate_chapter(
                run_id=ctx.run_id,
                ordinal=ordinal,
                prompt=prompt,
                bundle=bundle,
                chapter_meta=chapter_meta,
                timeout_seconds=timeout_seconds,
                model=ctx.model,
                effort=ctx.effort,
            )
        except WorktreeGenerationError as exc:
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code=exc.code,
                error_message=exc.message,
            )
            _emit_failed_chapter(
                broadcaster, ctx.run_id, ordinal, chapter, exc.code, exc.message
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code="internal",
                error_message=str(exc),
            )
            _emit_failed_chapter(
                broadcaster, ctx.run_id, ordinal, chapter, "internal", str(exc)
            )
            return

        # Persist the chapter summary. ``parsed_candidate`` is the JSON
        # dict the agent emitted (or {"english": <text>} for text format,
        # which is degenerate for chapter summaries — chapter prompts
        # MUST be output_format=json, but we defend against a
        # mis-configured prompt).
        if prompt.output_format == "json":
            summary_text = json.dumps(
                result.parsed_candidate, ensure_ascii=False, sort_keys=True
            )
            consulted_raw = result.parsed_candidate.get("candidate_ids_consulted", [])
        else:
            english = result.parsed_candidate.get("english", "")
            summary_text = json.dumps(
                {"summary": str(english).strip()},
                ensure_ascii=False,
                sort_keys=True,
            )
            consulted_raw = []

        consulted_ids: list[int] = []
        if isinstance(consulted_raw, list):
            for item in consulted_raw:
                if isinstance(item, int):
                    consulted_ids.append(item)
                elif isinstance(item, str) and item.isdigit():
                    consulted_ids.append(int(item))

        try:
            summary_id = _commit_chapter_item_success(
                ctx,
                ordinal=ordinal,
                chapter=chapter,
                bundle=bundle,
                model=result.model,
                summary_text=summary_text,
                generated_at=result.completed_at,
                consulted_ids=consulted_ids,
            )
        except sqlite3.OperationalError as exc:
            error_code: GenerationErrorCode = (
                "disk_full" if "disk is full" in str(exc).lower() else "internal"
            )
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code=error_code,
                error_message=str(exc),
            )
            _emit_failed_chapter(
                broadcaster, ctx.run_id, ordinal, chapter, error_code, str(exc)
            )
            return
        except Exception as exc:  # noqa: BLE001
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code="internal",
                error_message=str(exc),
            )
            _emit_failed_chapter(
                broadcaster, ctx.run_id, ordinal, chapter, "internal", str(exc)
            )
            return

        broadcaster.emit(
            {
                "event": "item_completed",
                "data": {
                    "run_id": ctx.run_id,
                    "ordinal": ordinal,
                    "chapter": chapter,
                    "summary_id": summary_id,
                    "generated_at": result.completed_at,
                },
            }
        )
        _logger.info(
            "run.item.completed",
            extra={
                "run_id": ctx.run_id,
                "ordinal": ordinal,
                "chapter": chapter,
                "summary_id": summary_id,
            },
        )


def _emit_failed_chapter(
    broadcaster: _RunBroadcaster,
    run_id: str,
    ordinal: int,
    chapter: int,
    error_code: GenerationErrorCode,
    error_message: str,
) -> None:
    broadcaster.emit(
        {
            "event": "item_failed",
            "data": {
                "run_id": run_id,
                "ordinal": ordinal,
                "chapter": chapter,
                "error_code": error_code,
                "error_message": _truncate_error_message(error_message),
            },
        }
    )


def _emit_failed(
    broadcaster: _RunBroadcaster,
    run_id: str,
    ordinal: int,
    sentence_id: str,
    error_code: GenerationErrorCode,
    error_message: str,
) -> None:
    broadcaster.emit(
        {
            "event": "item_failed",
            "data": {
                "run_id": run_id,
                "ordinal": ordinal,
                "sentence_id": sentence_id,
                "error_code": error_code,
                "error_message": _truncate_error_message(error_message),
            },
        }
    )


def _mark_item_failed(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ordinal: int,
    error_code: GenerationErrorCode,
    error_message: str,
) -> None:
    conn.execute(
        """
        UPDATE generation_run_items
        SET status='failed', error_code=?, error_message=?, completed_at=?
        WHERE run_id=? AND ordinal=? AND status='running'
        """,
        (
            error_code,
            _truncate_error_message(error_message),
            _now_iso(),
            run_id,
            ordinal,
        ),
    )


def _commit_item_failure(
    ctx: _RunContext,
    *,
    ordinal: int,
    error_code: GenerationErrorCode,
    error_message: str,
) -> None:
    with _connect(ctx.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _mark_item_failed(
                conn,
                run_id=ctx.run_id,
                ordinal=ordinal,
                error_code=error_code,
                error_message=error_message,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    _logger.warning(
        "run.item.failed",
        extra={
            "run_id": ctx.run_id,
            "ordinal": ordinal,
            "error_code": error_code,
        },
    )


def _commit_item_success(
    ctx: _RunContext,
    *,
    ordinal: int,
    sentence_id: str,
    bundle: SourceBundle,
    model: str,
    candidate_text: str,
    generated_at: str,
    latency_ms: int,
) -> int:
    """Atomic per-sentence transaction (PLAN §Generation runner durability)."""
    canon_version = "v1"
    with _connect(ctx.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            snapshot_hash = upsert_snapshot(conn, bundle=bundle, canon_version=canon_version)
            cur = conn.execute(
                """
                INSERT INTO claude_candidates (
                    sentence_id, style_prompt_version, source_set_id, model,
                    generated_at, candidate_text, source_snapshot_hash,
                    hidden_bool, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    sentence_id,
                    ctx.style_prompt_version,
                    ctx.source_set_id,
                    model,
                    generated_at,
                    candidate_text,
                    snapshot_hash,
                    latency_ms,
                ),
            )
            candidate_id = int(cur.lastrowid or 0)
            conn.execute(
                """
                UPDATE generation_run_items
                SET status='completed', candidate_id=?, completed_at=?
                WHERE run_id=? AND ordinal=? AND status='running'
                """,
                (candidate_id, _now_iso(), ctx.run_id, ordinal),
            )
            conn.execute("COMMIT")
            return candidate_id
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _commit_chapter_item_success(
    ctx: _RunContext,
    *,
    ordinal: int,
    chapter: int,
    bundle: ChapterBundle,
    model: str,
    summary_text: str,
    generated_at: str,
    consulted_ids: list[int],
) -> int:
    """Atomic chapter-summary commit (Slice 8).

    Inserts the chapter snapshot, the ``chapter_summaries`` row, and
    flips the run item's terminal status in one ``BEGIN IMMEDIATE`` txn.
    The migration 0003 relaxed ``generation_run_items``' CHECK constraint
    so a chapter completion can leave ``candidate_id`` NULL and have
    ``chapter`` set instead — the chapter_summaries.summary_id is the
    completion artefact, not a claude_candidates row.
    """
    canon_version = "v1"
    with _connect(ctx.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            snapshot_hash = upsert_chapter_snapshot(
                conn, bundle=bundle, canon_version=canon_version
            )
            cur = conn.execute(
                """
                INSERT INTO chapter_summaries (
                    chapter, prompt_version, source_set_id, model,
                    source_snapshot_hash, summary_text, generated_at,
                    run_id, candidate_ids_consulted, hidden_bool
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    chapter,
                    ctx.style_prompt_version,
                    "CHAPTER_BUNDLE",
                    model,
                    snapshot_hash,
                    summary_text,
                    generated_at,
                    ctx.run_id,
                    json.dumps(consulted_ids, sort_keys=True),
                ),
            )
            summary_id = int(cur.lastrowid or 0)
            conn.execute(
                """
                UPDATE generation_run_items
                SET status='completed', completed_at=?
                WHERE run_id=? AND ordinal=? AND status='running'
                """,
                (_now_iso(), ctx.run_id, ordinal),
            )
            conn.execute("COMMIT")
            return summary_id
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _set_run_status(ctx: _RunContext, *, status: str) -> None:
    with _connect(ctx.db_path) as conn:
        conn.execute(
            """
            UPDATE generation_runs SET status=?, completed_at=?
            WHERE run_id=? AND status IN ('running', 'pending')
            """,
            (status, _now_iso(), ctx.run_id),
        )


def _finalise_run_status(ctx: _RunContext) -> str:
    with _connect(ctx.db_path) as conn:
        row = conn.execute(
            "SELECT status FROM generation_runs WHERE run_id=?",
            (ctx.run_id,),
        ).fetchone()
        if row is None:
            return "unknown"
        current = row["status"]
        if current in ("cancelled", "failed", "interrupted"):
            return current
        conn.execute(
            """
            UPDATE generation_runs SET status='completed', completed_at=?
            WHERE run_id=? AND status='running'
            """,
            (_now_iso(), ctx.run_id),
        )
        return "completed"


@contextlib.contextmanager
def _connect(db_path: Path | None):
    conn = open_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def schedule_run(
    *,
    run_id: str,
    sentence_ids: list[str],
    style_prompt_version: str,
    source_set_id: str,
    model: str,
    effort: str,
    project_root: Path,
    db_path: Path | None,
    spawner: WorktreeSpawner,
    wants_context_window: bool = False,
    context_window_before: int = 0,
    context_window_after: int = 0,
    chapter: int | None = None,
) -> asyncio.Task[None]:
    _state.broadcasters.setdefault(run_id, _RunBroadcaster(run_id))
    ctx = _RunContext(
        run_id=run_id,
        sentence_ids=list(sentence_ids),
        style_prompt_version=style_prompt_version,
        source_set_id=source_set_id,
        model=model,
        effort=effort,
        project_root=project_root,
        db_path=db_path,
        spawner=spawner,
        wants_context_window=wants_context_window,
        context_window_before=context_window_before,
        context_window_after=context_window_after,
        chapter=chapter,
    )
    return asyncio.create_task(dispatch_run(ctx))


__all__ = [
    "ClaudeCliUnavailableError",
    "CreateRunResult",
    "RunCreationError",
    "RunNotFoundError",
    "create_run",
    "dispatch_run",
    "get_broadcaster",
    "schedule_run",
]
