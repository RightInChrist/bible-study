"""In-process generation runner (Slice 3a).

Architect §Generation runner + PLAN §Generation runner durability:

- One ``asyncio`` task per sentence within a run, bounded by an
  ``asyncio.Semaphore(MAX_CONCURRENT_RUN_ITEMS)``.
- Per-sentence transaction: ``BEGIN IMMEDIATE`` →
  ``INSERT OR IGNORE source_snapshots`` → ``INSERT claude_candidates`` →
  ``UPDATE generation_run_items SET status='completed', candidate_id=?``.
  All four statements commit together; rollback on any failure.
- ``hidden_combos`` check before every Anthropic call: if a row exists
  for ``(sentence, prompt, source_set, model)`` the call is skipped and
  the item is marked ``status='completed'`` with ``candidate_id=NULL``
  AND ``error_code=hidden_combo_skipped`` — except the schema's CHECK
  constraint forbids that combination.
  **Resolution: hidden-combo skips are recorded as ``status='cancelled'``
  with ``error_code=NULL``.** ``cancelled`` is the existing terminal
  status for "we deliberately did not run this item"; it satisfies the
  schema constraint, makes Designer's hidden filter trivial (count
  ``cancelled`` items as suppressed), and avoids growing the
  ``error_code`` enum.
- No auto-retry on Anthropic errors (Reliability §Retries & backoff).
- SSE event broadcast is a per-run ``asyncio.Queue`` register; the SSE
  endpoint subscribes and replays from the DB before attaching live.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from api.claude.client import (
    AnthropicGenerationError,
    ClaudeCallRequest,
    ClaudeClient,
    estimate_cost,
)
from api.claude.prompts import load_prompt
from api.claude.schemas import (
    GenerationErrorCode,
    SourceBundle,
    SourceSetId,
    StylePrompt,
)
from api.claude.sources import (
    IncompatibleSourceSetError,
    resolve_source_bundle,
    upsert_snapshot,
    validate_compatibility,
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
      - ``anthropic_key_missing``
      - ``unknown_style_prompt``
      - ``incompatible_source_set``
      - ``invalid_scope``
      - ``empty_scope``
      - ``estimated_cost_exceeds_cap``
      - ``daily_cost_cap_exceeded``
      - ``max_sentences_per_run_exceeded``
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


def _to_x10000(usd: float) -> int:
    """Convert USD to 0.0001-USD integer units (PLAN §generation_runs)."""
    return int(round(usd * 10_000))


def _from_x10000(units: int) -> float:
    return units / 10_000.0


def _truncate_error_message(message: str) -> str:
    settings = get_settings()
    cap = settings.max_error_message_bytes
    encoded = message.encode("utf-8")
    if len(encoded) <= cap:
        return message
    return encoded[:cap].decode("utf-8", errors="ignore") + "…[truncated]"


def _load_style_prompt(project_root: Path, prompt_version: str) -> StylePrompt:
    """Read the prompt fixture from disk by version.

    ``style_prompts`` table records the body path and SHA-256; we read
    from disk for the prompt body so Architect §Prompt artifact format
    ("body content stored on disk, not in DB") holds. The runner does
    not re-verify the SHA — that's the importer's job, asserted in
    ``test_prompt_body_sha256_matches_fixture``.
    """
    # Map version -> path via convention: ``fixtures/prompts/{version}.md``.
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

    Lexicographic-sentence-id ordering would misorder mat-1-2 < mat-1-10
    (PLAN flagged this); we always join through ``sentences`` and order
    by ``(chapter, ordinal_in_chapter)``.
    """
    kind = scope.kind
    if kind == "one_sentence":
        # Validate the sentence exists.
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
        # Effective red-letter set ∩ unranked. v1 has no overlays so the
        # red-letter set is purely ``red_letter_source_ranges``; the
        # unranked predicate is ``NOT EXISTS (SELECT 1 FROM rankings ...)``.
        # Ordering on (chapter, ordinal_in_chapter) — the canonical sort.
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
    """Capture ``as_of`` timestamp on ``all_unranked_red_letter`` scope.

    Architect §RunScope: the timestamp is recorded in
    ``generation_runs.scope_json`` so a Resume from this scope expands
    the same set rather than re-evaluating "unranked" against current
    state.
    """
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
    estimated_cost_usd: float
    estimated_cost_usd_band_pct: int


def create_run(
    *,
    conn: sqlite3.Connection,
    scope: RunScope,
    style_prompt_version: str,
    source_set_id: SourceSetId,
    model: str,
    claude_client: ClaudeClient,
    project_root: Path,
    parent_run_id: str | None = None,
) -> CreateRunResult:
    """Synchronously persist a new run + items; caller schedules dispatch.

    Validation order (server-enforced — UI bypass cannot evade):
      1. Anthropic key configured.
      2. Style prompt exists.
      3. Source-set is in the prompt's ``compatible_source_sets``.
      4. Scope expands to >=1 sentence and <= ``MAX_SENTENCES_PER_RUN``.
      5. Estimated cost × 1.20 ≤ ``MAX_RUN_COST_USD``.
      6. Daily 24h rolling × 1.20 ≤ ``MAX_DAILY_COST_USD``.

    Returns the ``run_id`` + the dense ``sentence_ids`` list (also
    persisted as ``generation_run_items`` rows).
    """
    settings = get_settings()
    if not claude_client.api_key_set:
        raise RunCreationError(
            code="anthropic_key_missing",
            message="ANTHROPIC_API_KEY is not configured",
            details={"hint": "set ANTHROPIC_API_KEY in .env and restart"},
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

    captured_scope = _scope_with_as_of(scope)
    sentence_ids = _expand_scope(conn, captured_scope)
    if not sentence_ids:
        raise RunCreationError(
            code="empty_scope",
            message="run scope expanded to zero sentences",
            details={"scope": captured_scope.model_dump()},
        )
    if len(sentence_ids) > settings.max_sentences_per_run:
        raise RunCreationError(
            code="max_sentences_per_run_exceeded",
            message=(
                f"scope expanded to {len(sentence_ids)} sentences; cap is "
                f"{settings.max_sentences_per_run}"
            ),
            details={
                "items_count": len(sentence_ids),
                "max_sentences_per_run": settings.max_sentences_per_run,
            },
        )

    # Per-run cost estimate: representative bundle on the first sentence,
    # multiplied by N. Architect §Claude client pinned the heuristic;
    # using one bundle for the estimate is acceptable because all
    # sentences in a chapter share roughly the same source-bundle size.
    representative_bundle = resolve_source_bundle(
        conn,
        sentence_id=sentence_ids[0],
        source_set_id=source_set_id,
        prompt_version=style_prompt_version,
    )
    cost_one = estimate_cost(prompt=prompt, bundle=representative_bundle, model=model)
    per_run_cost = cost_one.estimated_cost_usd * len(sentence_ids)
    band = 1.0 + cost_one.estimated_cost_usd_band_pct / 100.0
    upper_bound = per_run_cost * band

    if upper_bound > settings.max_run_cost_usd:
        raise RunCreationError(
            code="estimated_cost_exceeds_cap",
            message=(
                f"estimated run cost ${per_run_cost:.4f} (+{cost_one.estimated_cost_usd_band_pct}%) "
                f"exceeds MAX_RUN_COST_USD=${settings.max_run_cost_usd:.2f}"
            ),
            details={
                "estimated_cost_usd": per_run_cost,
                "band_pct": cost_one.estimated_cost_usd_band_pct,
                "max_run_cost_usd": settings.max_run_cost_usd,
            },
        )

    # Daily cap: SUM(estimated_cost_usd_x10000) over last 24h × 1.20
    daily_units_row = conn.execute(
        """
        SELECT COALESCE(SUM(estimated_cost_usd_x10000), 0) AS total
        FROM generation_runs
        WHERE created_at >= ?
        """,
        (_iso_24h_ago(),),
    ).fetchone()
    daily_total_usd = _from_x10000(int(daily_units_row["total"]))
    projected_daily = (daily_total_usd + per_run_cost) * band
    if projected_daily > settings.max_daily_cost_usd:
        raise RunCreationError(
            code="daily_cost_cap_exceeded",
            message=(
                f"projected 24h spend ${projected_daily:.4f} "
                f"exceeds MAX_DAILY_COST_USD=${settings.max_daily_cost_usd:.2f}"
            ),
            details={
                "projected_daily_usd": projected_daily,
                "max_daily_cost_usd": settings.max_daily_cost_usd,
            },
        )

    run_id = str(uuid.uuid4())
    created_at = _now_iso()
    estimated_input = cost_one.estimated_input_units * len(sentence_ids)
    cost_units = _to_x10000(per_run_cost)

    # Persist run + items in one BEGIN IMMEDIATE.
    scope_json = json.dumps(
        captured_scope.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO generation_runs (
                run_id, status, scope_json, style_prompt_version,
                source_set_id, model, estimated_cost_usd_x10000,
                estimated_input_units, parent_run_id, created_at
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                scope_json,
                style_prompt_version,
                source_set_id,
                model,
                cost_units,
                estimated_input,
                parent_run_id,
                created_at,
            ),
        )
        conn.executemany(
            """
            INSERT INTO generation_run_items (
                run_id, ordinal, sentence_id, status
            ) VALUES (?, ?, ?, 'pending')
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
            "estimated_cost_usd": per_run_cost,
            "style_prompt_version": style_prompt_version,
            "source_set_id": source_set_id,
            "model": model,
        },
    )

    return CreateRunResult(
        run_id=run_id,
        sentence_ids=list(sentence_ids),
        estimated_cost_usd=per_run_cost,
        estimated_cost_usd_band_pct=cost_one.estimated_cost_usd_band_pct,
    )


def _iso_24h_ago() -> str:
    """ISO-8601 UTC timestamp 24h before now (rolling window)."""
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
    source_set_id: SourceSetId
    model: str
    project_root: Path
    db_path: Path | None
    claude_client: ClaudeClient


async def dispatch_run(ctx: _RunContext) -> None:
    """Top-level dispatcher: claim 'running', fan out per-sentence tasks,
    update terminal status, emit terminal SSE event.
    """
    settings = get_settings()
    broadcaster = _state.broadcasters.setdefault(ctx.run_id, _RunBroadcaster(ctx.run_id))

    # Mark run started.
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

    sem = asyncio.Semaphore(int(settings.max_concurrent_anthropic_calls))
    prompt = load_prompt(
        ctx.project_root / "fixtures" / "prompts" / f"{ctx.style_prompt_version}.md"
    )

    tasks: list[asyncio.Task[None]] = []
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
    except Exception:  # noqa: BLE001 — finish run as failed
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
        # Re-check run status before claiming — covers cancel-mid-flight.
        with _connect(ctx.db_path) as conn:
            run_row = conn.execute(
                "SELECT status FROM generation_runs WHERE run_id=?",
                (ctx.run_id,),
            ).fetchone()
            if run_row is None:
                return
            if run_row["status"] not in ("running", "pending"):
                return
            # Claim ``running`` state on the item.
            cur = conn.execute(
                """
                UPDATE generation_run_items SET status='running', started_at=?
                WHERE run_id=? AND ordinal=? AND status='pending'
                """,
                (_now_iso(), ctx.run_id, ordinal),
            )
            if cur.rowcount == 0:
                # Another dispatcher already took it (shouldn't happen in v1).
                return

            # Hidden-combo skip path.
            if _check_hidden_combo(
                conn,
                sentence_id=sentence_id,
                style_prompt_version=ctx.style_prompt_version,
                source_set_id=ctx.source_set_id,
                model=ctx.model,
            ):
                # Architect §hidden_combos: skip the call. Schema CHECK
                # forbids ``error_code`` on a non-failed status, so we
                # mark these as ``cancelled`` (meaning "deliberately not
                # run") to stay schema-clean.
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
                )
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

        _logger.info(
            "run.item.started",
            extra={
                "run_id": ctx.run_id,
                "ordinal": ordinal,
                "sentence_id": sentence_id,
            },
        )

        # Make the Anthropic call OUTSIDE the SQLite connection so we
        # don't hold a write lock during network I/O. The connection is
        # re-opened below to commit success/failure in one transaction.
        request = ClaudeCallRequest(prompt=prompt, bundle=bundle, model=ctx.model)
        timeout_seconds = float(settings.anthropic_request_timeout_seconds + 30)

        try:
            generated = await asyncio.wait_for(
                ctx.claude_client.generate(request), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            _commit_item_failure(
                ctx,
                ordinal=ordinal,
                error_code="timeout",
                error_message="Anthropic call exceeded the runner timeout",
            )
            _emit_failed(broadcaster, ctx.run_id, ordinal, sentence_id, "timeout",
                         "Anthropic call exceeded the runner timeout")
            return
        except AnthropicGenerationError as exc:
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

        # Persist success: snapshot upsert + candidate insert + item
        # update, atomically (PLAN §Generation runner durability).
        try:
            candidate_id = _commit_item_success(
                ctx,
                ordinal=ordinal,
                sentence_id=sentence_id,
                bundle=bundle,
                generated_candidate_text=generated.candidate_text,
                generated_at=generated.generated_at,
                latency_ms=generated.latency_ms,
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
                    "latency_ms": generated.latency_ms,
                    "generated_at": generated.generated_at,
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
                "duration_ms": generated.latency_ms,
            },
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
    """Open a fresh transaction and mark the item failed."""
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
    generated_candidate_text: str,
    generated_at: str,
    latency_ms: int,
) -> int:
    """Atomic per-sentence transaction (PLAN §Generation runner durability)."""
    settings = get_settings()
    canon_version = "v1"  # current source_snapshot_canon_version
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
                    ctx.model,
                    generated_at,
                    generated_candidate_text,
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
    """After all items have settled, set the run's terminal status.

    Per Reliability §Failure modes: per-item failures don't fail the run
    — the run finishes ``completed`` with mixed item statuses. Run-level
    ``failed`` is reserved for runner-level faults. ``cancelled`` /
    ``interrupted`` flow from explicit user/system action and are set
    elsewhere; here we only flip ``running`` → ``completed``.
    """
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
    """Helper wrapping ``open_connection`` with proper cleanup."""
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
    source_set_id: SourceSetId,
    model: str,
    project_root: Path,
    db_path: Path | None,
    claude_client: ClaudeClient,
) -> asyncio.Task[None]:
    """Create the asyncio task that drives a run to completion.

    The caller (route handler) does not await; the task survives across
    request boundaries because it's anchored on the event loop. The
    broadcaster registers eagerly so an SSE client connecting before the
    first dispatch sees ``replay_begin`` over an empty item set.
    """
    _state.broadcasters.setdefault(run_id, _RunBroadcaster(run_id))
    ctx = _RunContext(
        run_id=run_id,
        sentence_ids=list(sentence_ids),
        style_prompt_version=style_prompt_version,
        source_set_id=source_set_id,
        model=model,
        project_root=project_root,
        db_path=db_path,
        claude_client=claude_client,
    )
    return asyncio.create_task(dispatch_run(ctx))


