"""Admin service layer.

Owns the orphan-detection algorithm (PLAN §OrphanSummary computation),
the reimport orchestration with concurrency lock + dispositions, and the
static-site builder.

Routes are thin adapters: they parse Pydantic, call functions here,
serialize Pydantic. The static-site builder also calls functions in
``api/<feature>/service.py`` directly (Architect Hard decision #6 — never
the route handlers).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from api.admin.schemas import (
    BuildStaticCoverage,
    BuildStaticResponse,
    OrphanCandidate,
    OrphanDisposition,
    OrphanOverlay,
    OrphanRanking,
    OrphanReason,
    OrphanSummary,
    ReimportResponse,
)
from api.db.connection import open_connection
from api.errors import DomainError
from api.gsv.service import (
    compile_chapter_gsv,
    compute_coverage,
    render_markdown,
    render_plain_text,
)
from api.gsv.errors import UnresolvedTiesError
from api.importer.runner import (
    FixtureImportError,
    _build_sentences,
    _read_text,
    import_fixtures,
)
from api.importer.manifest import (
    find_file,
    load_manifest,
    verify_manifest_files,
)
from api.chapters.service import list_summaries_for_chapter
from api.logging import get_logger
from api.rankings.service import get_ranking
from api.runs.service import list_sentence_candidates
from api.sentences.service import get_sentence_parallel, list_sentences_in_chapter
from api.settings import get_commit_hash, get_settings


_logger = get_logger("bible_study.admin")


# Process-wide locks (PLAN §Idempotency).
_REIMPORT_LOCK: asyncio.Lock = asyncio.Lock()
_BUILD_LOCK: asyncio.Lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class ImportInProgressError(DomainError):
    status_code = 409
    code = "import_in_progress"

    def __init__(self) -> None:
        super().__init__("An import is already running.")


class BuildInProgressError(DomainError):
    status_code = 409
    code = "build_in_progress"

    def __init__(self) -> None:
        super().__init__("A static-site build is already running.")


class FixtureHashMismatchError(DomainError):
    status_code = 400
    code = "fixture_hash_mismatch"

    def __init__(self, errors: list[str]) -> None:
        super().__init__(
            "One or more fixture files do not match their pinned SHA-256.",
            details={"errors": errors},
        )


class OrphansDetectedError(DomainError):
    status_code = 409
    code = "orphans_detected"

    def __init__(self, summary: OrphanSummary) -> None:
        super().__init__(
            f"{summary.total} orphan(s) reference sentences that the new "
            "fixture set would remove or change. Resolve dispositions and "
            "retry with force=true.",
            details={"orphan_summary": summary.model_dump()},
        )


class BuildGuardFailedError(DomainError):
    status_code = 400
    code = "build_guard_failed"

    def __init__(self, hits: dict[str, list[str]]) -> None:
        super().__init__(
            "Static-site build aborted: forbidden content detected in "
            "the staging tree. Inspect details.hits.",
            details={"hits": hits},
        )


class BuildFailedError(DomainError):
    status_code = 500
    code = "build_failed"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message, details=details)


# ---------------------------------------------------------------------------
# Orphan detection (PLAN §OrphanSummary computation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StagedSentences:
    """Result of staging the new fixtures into an in-memory new_db."""

    by_id: dict[str, dict[str, object]]
    by_text_and_verse: dict[tuple[str, int, int], str]


def _stage_new_sentences(project_root: Path) -> _StagedSentences:
    """Build the new sentence set from fixtures without touching the live DB.

    The orphan pre-flight needs the *future* sentence_id → text mapping
    to classify orphan reasons; this avoids running the full importer
    against a temp DB just for the sentence rows.
    """
    manifest_path = project_root / "fixtures" / "manifest.json"
    manifest = load_manifest(manifest_path)
    sblgnt_path = project_root / find_file(manifest, "sblgnt-matthew").path
    sblgnt_text = _read_text(sblgnt_path)
    _sentences, sentence_rows, _word_rows, _index = _build_sentences(sblgnt_text)
    by_id: dict[str, dict[str, object]] = {}
    by_text_and_verse: dict[tuple[str, int, int], str] = {}
    for row in sentence_rows:
        sentence_id = str(row[0])
        text_sblgnt = str(row[3])
        start_chapter = int(row[4])  # type: ignore[arg-type]
        start_verse = int(row[5])  # type: ignore[arg-type]
        by_id[sentence_id] = {
            "sentence_id": sentence_id,
            "chapter": int(row[1]),  # type: ignore[arg-type]
            "ordinal_in_chapter": int(row[2]),  # type: ignore[arg-type]
            "text_sblgnt": text_sblgnt,
            "start_chapter": start_chapter,
            "start_verse": start_verse,
        }
        by_text_and_verse[(text_sblgnt, start_chapter, start_verse)] = sentence_id
    return _StagedSentences(by_id=by_id, by_text_and_verse=by_text_and_verse)


def _excerpt(text: str | None, max_chars: int = 200) -> str:
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _classify_sentence_reason(
    old_sentence_id: str,
    old_text: str | None,
    old_start_chapter: int | None,
    old_start_verse: int | None,
    staged: _StagedSentences,
) -> tuple[OrphanReason, str | None]:
    """Return (reason, suggested_remap_sentence_id)."""
    if old_sentence_id in staged.by_id:
        new_text = str(staged.by_id[old_sentence_id]["text_sblgnt"])
        if old_text is not None and old_text != new_text:
            return ("sentence_text_changed", None)
        # Sentence ID still exists with same text — not actually an orphan.
        # The caller filters this case out before classifying.
        return ("sentence_text_changed", None)
    if (
        old_text is not None
        and old_start_chapter is not None
        and old_start_verse is not None
    ):
        suggested = staged.by_text_and_verse.get(
            (old_text, old_start_chapter, old_start_verse)
        )
        if suggested is not None and suggested != old_sentence_id:
            return ("sentence_id_remapped", suggested)
    return ("sentence_removed", None)


def _is_orphaned_sentence(sentence_id: str, old_text: str | None, staged: _StagedSentences) -> bool:
    if sentence_id not in staged.by_id:
        return True
    if old_text is None:
        return False
    new_text = str(staged.by_id[sentence_id]["text_sblgnt"])
    return new_text != old_text


def compute_orphan_summary(
    conn: sqlite3.Connection, project_root: Path
) -> OrphanSummary:
    """Algorithm pinned in PLAN §OrphanSummary computation.

    Reads the live DB read-only and stages the new sentences in memory.
    Returns a structured summary listing every orphaned candidate,
    ranking, and overlay leaf row, classified by reason.
    """
    staged = _stage_new_sentences(project_root)

    sentence_text_by_id: dict[str, dict[str, object]] = {}
    rows = conn.execute(
        "SELECT sentence_id, text_sblgnt, start_chapter, start_verse FROM sentences"
    ).fetchall()
    for r in rows:
        sentence_text_by_id[r["sentence_id"]] = {
            "text_sblgnt": r["text_sblgnt"],
            "start_chapter": int(r["start_chapter"]),
            "start_verse": int(r["start_verse"]),
        }

    affected_candidates: list[OrphanCandidate] = []
    candidate_rows = conn.execute(
        """
        SELECT candidate_id, sentence_id, style_prompt_version, source_set_id,
               model, generated_at, candidate_text
        FROM claude_candidates
        ORDER BY candidate_id
        """
    ).fetchall()
    for cr in candidate_rows:
        sid = str(cr["sentence_id"])
        old_meta = sentence_text_by_id.get(sid)
        old_text = str(old_meta["text_sblgnt"]) if old_meta else None
        old_sc = int(old_meta["start_chapter"]) if old_meta else None
        old_sv = int(old_meta["start_verse"]) if old_meta else None
        if not _is_orphaned_sentence(sid, old_text, staged):
            continue
        reason, remap = _classify_sentence_reason(sid, old_text, old_sc, old_sv, staged)
        new_excerpt = None
        if remap is not None and remap in staged.by_id:
            new_excerpt = _excerpt(str(staged.by_id[remap]["text_sblgnt"]))
        affected_candidates.append(
            OrphanCandidate(
                candidate_id=int(cr["candidate_id"]),
                sentence_id_old=sid,
                sentence_id_new=remap,
                reason=reason,
                style_prompt_version=cr["style_prompt_version"],
                source_set_id=cr["source_set_id"],
                model=cr["model"],
                generated_at=cr["generated_at"],
                candidate_text_excerpt=_excerpt(cr["candidate_text"]),
                old_sentence_text_excerpt=_excerpt(old_text or ""),
                new_sentence_text_excerpt=new_excerpt,
                suggested_remap_sentence_id=remap,
            )
        )

    affected_rankings: list[OrphanRanking] = []
    ranking_rows = conn.execute(
        """
        SELECT sentence_id, notes, version
        FROM rankings
        ORDER BY sentence_id
        """
    ).fetchall()
    for rr in ranking_rows:
        sid = str(rr["sentence_id"])
        old_meta = sentence_text_by_id.get(sid)
        old_text = str(old_meta["text_sblgnt"]) if old_meta else None
        old_sc = int(old_meta["start_chapter"]) if old_meta else None
        old_sv = int(old_meta["start_verse"]) if old_meta else None
        if not _is_orphaned_sentence(sid, old_text, staged):
            continue
        reason, remap = _classify_sentence_reason(sid, old_text, old_sc, old_sv, staged)
        ranked_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ranking_entries WHERE sentence_id = ?",
                (sid,),
            ).fetchone()[0]
        )
        has_tie_break = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM tie_break_decisions WHERE sentence_id = ?",
                    (sid,),
                ).fetchone()[0]
            )
            > 0
        )
        affected_rankings.append(
            OrphanRanking(
                sentence_id_old=sid,
                sentence_id_new=remap,
                reason=reason,
                ranked_candidate_count=ranked_count,
                has_notes=bool(rr["notes"]),
                has_tie_break=has_tie_break,
                version=int(rr["version"]),
                suggested_remap_sentence_id=remap,
            )
        )

    affected_overlays: list[OrphanOverlay] = []
    overlay_rows = conn.execute(
        """
        SELECT overlay_id, start_sentence_id, end_sentence_id, origin, rejected
        FROM red_letter_overlays
        WHERE rejected = 0
        ORDER BY overlay_id
        """
    ).fetchall()
    for or_ in overlay_rows:
        ssid = or_["start_sentence_id"]
        esid = or_["end_sentence_id"]
        if ssid is None or esid is None:
            continue
        ssid = str(ssid)
        esid = str(esid)
        s_meta = sentence_text_by_id.get(ssid)
        e_meta = sentence_text_by_id.get(esid)
        s_text = str(s_meta["text_sblgnt"]) if s_meta else None
        e_text = str(e_meta["text_sblgnt"]) if e_meta else None
        s_orphaned = _is_orphaned_sentence(ssid, s_text, staged)
        e_orphaned = _is_orphaned_sentence(esid, e_text, staged)
        if not (s_orphaned or e_orphaned):
            continue
        s_sc = int(s_meta["start_chapter"]) if s_meta else None
        s_sv = int(s_meta["start_verse"]) if s_meta else None
        e_sc = int(e_meta["start_chapter"]) if e_meta else None
        e_sv = int(e_meta["start_verse"]) if e_meta else None
        s_reason, s_remap = (
            _classify_sentence_reason(ssid, s_text, s_sc, s_sv, staged)
            if s_orphaned
            else ("sentence_text_changed", None)
        )
        _, e_remap = (
            _classify_sentence_reason(esid, e_text, e_sc, e_sv, staged)
            if e_orphaned
            else ("sentence_text_changed", None)
        )
        affected_overlays.append(
            OrphanOverlay(
                overlay_id=int(or_["overlay_id"]),
                range_start_sentence_id_old=ssid,
                range_end_sentence_id_old=esid,
                reason=s_reason,
                origin=or_["origin"],
                rejected=bool(or_["rejected"]),
                suggested_remap_start_sentence_id=s_remap,
                suggested_remap_end_sentence_id=e_remap,
            )
        )

    total = len(affected_candidates) + len(affected_rankings) + len(affected_overlays)
    return OrphanSummary(
        affected_candidates=affected_candidates,
        affected_rankings=affected_rankings,
        affected_overlays=affected_overlays,
        total=total,
    )


# ---------------------------------------------------------------------------
# Reimport (with orphan pre-flight + dispositions)
# ---------------------------------------------------------------------------


def _apply_dispositions(
    conn: sqlite3.Connection,
    summary: OrphanSummary,
    dispositions: dict[str, OrphanDisposition] | None,
) -> None:
    """Apply per-orphan dispositions inside a BEGIN IMMEDIATE transaction.

    v1 supports ``delete`` and ``keep`` only — ``remap`` semantics for
    candidates and overlays land in a follow-up. ``keep`` is a no-op
    here; callers paired ``keep`` with `force=True` to acknowledge the
    orphan even though no disposition was applied. The follow-up import
    will succeed because the FK pre-flight is skipped under force.
    """
    if dispositions is None:
        return
    for cand in summary.affected_candidates:
        key = f"candidate:{cand.candidate_id}"
        action = dispositions.get(key)
        if action == "delete":
            conn.execute(
                "DELETE FROM ranking_entries WHERE claude_candidate_id = ?",
                (cand.candidate_id,),
            )
            conn.execute(
                "DELETE FROM claude_candidates WHERE candidate_id = ?",
                (cand.candidate_id,),
            )
    for ranking in summary.affected_rankings:
        key = f"ranking:{ranking.sentence_id_old}"
        action = dispositions.get(key)
        if action == "delete":
            conn.execute(
                "DELETE FROM ranking_entries WHERE sentence_id = ?",
                (ranking.sentence_id_old,),
            )
            conn.execute(
                "DELETE FROM tie_break_decisions WHERE sentence_id = ?",
                (ranking.sentence_id_old,),
            )
            conn.execute(
                "DELETE FROM rankings WHERE sentence_id = ?",
                (ranking.sentence_id_old,),
            )
    for overlay in summary.affected_overlays:
        key = f"overlay:{overlay.overlay_id}"
        action = dispositions.get(key)
        if action == "delete":
            conn.execute(
                "DELETE FROM red_letter_overlays WHERE overlay_id = ?",
                (overlay.overlay_id,),
            )


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


async def run_reimport(
    *,
    project_root: Path,
    db_path: Path,
    force: bool,
    dispositions: dict[str, OrphanDisposition] | None,
) -> ReimportResponse:
    """Orchestrate the full reimport.

    Concurrency lock prevents two simultaneous reimports (PLAN §Idempotency).
    Pre-flight verifies every fixture's SHA-256 (raises 400 on mismatch).
    Pre-flight orphan check runs unless ``force`` is set; if orphans
    exist, raises 409 with the structured ``orphan_summary``. Under
    ``force``, dispositions are applied first, then the importer runs.
    """
    if _REIMPORT_LOCK.locked():
        raise ImportInProgressError()
    async with _REIMPORT_LOCK:
        return await asyncio.to_thread(
            _reimport_sync,
            project_root,
            db_path,
            force,
            dispositions,
        )


def _reimport_sync(
    project_root: Path,
    db_path: Path,
    force: bool,
    dispositions: dict[str, OrphanDisposition] | None,
) -> ReimportResponse:
    started = time.monotonic()

    manifest_path = project_root / "fixtures" / "manifest.json"
    manifest = load_manifest(manifest_path)
    hash_errors = verify_manifest_files(manifest, project_root)
    if hash_errors:
        raise FixtureHashMismatchError(hash_errors)

    if not force:
        conn = open_connection(db_path)
        try:
            summary = compute_orphan_summary(conn, project_root)
        finally:
            conn.close()
        if summary.total > 0:
            _logger.info(
                "import.orphans_detected",
                extra={"total": summary.total, "stage": "preflight"},
            )
            raise OrphansDetectedError(summary)

    if force and dispositions:
        # Apply dispositions inside a separate transaction so the
        # importer's BEGIN IMMEDIATE can run cleanly. Compute the summary
        # again so a stale UI submission doesn't reference IDs that no
        # longer exist (defensive — the orphan resolution screen carries
        # the summary, but a re-render is cheap insurance).
        conn = open_connection(db_path)
        try:
            summary = compute_orphan_summary(conn, project_root)
            conn.execute("BEGIN IMMEDIATE")
            try:
                _apply_dispositions(conn, summary, dispositions)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    try:
        result = import_fixtures(project_root=project_root, db_path=db_path)
    except FixtureImportError as exc:
        if exc.code == "fixture_hash_mismatch":
            raise FixtureHashMismatchError(
                list(exc.details.get("errors", []))  # type: ignore[arg-type]
            ) from exc
        raise BuildFailedError(exc.message, details=exc.details) from exc

    conn = open_connection(db_path)
    try:
        byzantine_count = _count_rows(conn, "byzantine_verses")
        english_count = _count_rows(conn, "english_verses")
        bib_count = _count_rows(conn, "bib_interlinear_words")
        prompt_count = _count_rows(conn, "style_prompts")
    finally:
        conn.close()

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return ReimportResponse(
        fixture_version=result.fixture_version,
        files_imported=result.files_imported,
        sentences_built=result.sentences_built,
        words_built=result.words_built,
        byzantine_verses=byzantine_count,
        english_verses=english_count,
        bib_interlinear_words=bib_count,
        red_letter_source_ranges=result.red_letter_source_ranges,
        style_prompts=prompt_count,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Build static
# ---------------------------------------------------------------------------


# Build-time grep guards (Security §Build-time / publish-time guards).
# Every pattern below MUST NOT appear anywhere under ``dist.tmp/``; if
# any match, the build fails with ``build_guard_failed`` and ``dist.tmp/``
# is removed (``dist/`` is left untouched).
_BUILD_GUARD_LITERALS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "api.anthropic.com",
    "@anthropic-ai/sdk",
    "/admin/reimport",
    "/admin/build-static",
    "/admin/test-only",
    "payload_json",
)


# Admin / write paths are tested as URL substrings — these strings should
# not appear in any built file. Distinct from the literal list above so
# tests can sanity-check the guard set.
_BUILD_GUARD_PATH_LITERALS: tuple[str, ...] = (
    "/admin/reimport",
    "/admin/build-static",
    "/admin/test-only",
    "/runs/",
    "/rankings/",
    "/hidden-combos/",
    "/tie-break/",
    "/red-letter/mark",
    "/red-letter/unmark",
    "/red-letter/restore",
)


# Long, distinctive substrings of each prompt body. Picked at build time
# from disk so a future prompt edit doesn't silently let the old string
# pass through (the new prompt body's content is what we're guarding).
def _prompt_body_substrings(project_root: Path) -> list[tuple[str, str]]:
    """Return ``[(prompt_path, distinctive_substring), ...]`` from the
    on-disk prompt fixtures. The substring is the longest line over a
    width threshold so trivial common phrasing doesn't false-positive.
    """
    prompts_dir = project_root / "fixtures" / "prompts"
    out: list[tuple[str, str]] = []
    if not prompts_dir.exists():
        return out
    for prompt_file in sorted(prompts_dir.glob("*.md")):
        text = prompt_file.read_text(encoding="utf-8")
        # Strip the front-matter so we don't false-positive on the
        # YAML keys that also appear in style_prompts table dumps.
        body = text
        if body.startswith("---\n"):
            close = body.find("\n---\n", 4)
            if close != -1:
                body = body[close + len("\n---\n") :]
        # Pick the longest line over 60 chars as the guard fragment.
        candidate = ""
        for line in body.splitlines():
            stripped = line.strip()
            if len(stripped) > len(candidate) and len(stripped) >= 60:
                candidate = stripped
        if candidate:
            # Trim to 200 chars to keep the grep cheap.
            out.append((str(prompt_file.relative_to(project_root)), candidate[:200]))
    return out


_SECRET_PREFIX_BYTES: bytes = b"sk-ant-"
_ENV_FILE_HEAD = b"DATABASE_PATH="


def _walk_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _grep_guard_dist(dist_tmp: Path, project_root: Path) -> dict[str, list[str]]:
    """Walk ``dist_tmp`` and return ``{pattern: [paths...]}`` for any hit.

    Empty dict = clean; anything non-empty means abort. The grep is
    byte-level so it catches both source and post-bundled artefacts.
    """
    hits: dict[str, list[str]] = {}
    prompt_guards = _prompt_body_substrings(project_root)
    for path in _walk_files(dist_tmp):
        rel = str(path.relative_to(dist_tmp))
        data = _read_bytes(path)
        if not data:
            continue
        if _SECRET_PREFIX_BYTES in data:
            hits.setdefault("sk-ant-", []).append(rel)
        if _ENV_FILE_HEAD in data and rel.endswith(".env"):
            hits.setdefault(".env", []).append(rel)
        for literal in _BUILD_GUARD_LITERALS:
            if literal.encode("utf-8") in data:
                hits.setdefault(literal, []).append(rel)
        for literal in _BUILD_GUARD_PATH_LITERALS:
            if literal.encode("utf-8") in data and literal not in hits:
                hits.setdefault(literal, []).append(rel)
        for prompt_path, fragment in prompt_guards:
            if fragment.encode("utf-8") in data:
                hits.setdefault(f"prompt:{prompt_path}", []).append(rel)
    return hits


# JSON snapshot layout. Documented here for the static SPA's API client.
#
# Path                                            Description
# ---------------------------------------------   ------------------------------
# api/v1/health.json                              static health card
# api/v1/admin/fixture-status.json                snapshot of the import meta
# api/v1/sentences/chapter-{N}.json               SentenceListResponse for
#                                                 chapter N (1..28). The dev
#                                                 API uses `?chapter=N`; the
#                                                 static client resolver maps
#                                                 the query string to this
#                                                 path.
# api/v1/sentences/{sid}/parallel.json            per-sentence parallel reader
# api/v1/sentences/{sid}/candidates.json          per-sentence candidates
# api/v1/sentences/{sid}/ranking.json             per-sentence ranking
# api/v1/gsv/{ch}.json                            per-chapter GSV (json form)
# api/v1/gsv/{ch}.txt                             plain-text variant (or
#                                                 .txt-not-available.json on
#                                                 unresolved ties)
# api/v1/gsv/{ch}.md                              markdown variant
# api/v1/gsv/coverage.json                        repo-wide coverage
def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


def _write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _build_health_card(env_label: str = "static") -> dict[str, object]:
    return {
        "status": "ok",
        "commit_hash": get_commit_hash(),
        "env": env_label,
    }


def _build_fixture_status_snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    """Static snapshot of the fixture-status pill payload at build time."""
    row = conn.execute(
        "SELECT manifest_hash, last_imported_at FROM fixture_version WHERE id = 1"
    ).fetchone()
    db_hash = row["manifest_hash"] if row is not None else None
    last_imported_at = row["last_imported_at"] if row is not None else None
    return {
        "disk_manifest_hash": db_hash,
        "db_fixture_version": db_hash,
        "stale": False,
        "last_imported_at": last_imported_at,
    }


def _emit_snapshots(
    dist_tmp: Path,
    db_path: Path,
    *,
    include_unranked_placeholders: bool,
) -> tuple[int, BuildStaticCoverage]:
    """Emit every JSON snapshot under ``dist_tmp/api/v1/`` via service-layer
    calls. Returns ``(files_written, coverage)``.

    The static-site builder calls service-layer functions directly
    (Architect Hard decision #6) — never the FastAPI route handlers.
    """
    api_root = dist_tmp / "api" / "v1"
    files_written = 0

    # /health.json
    _write_json(api_root / "health.json", _build_health_card())
    files_written += 1

    # /admin/fixture-status.json
    conn = open_connection(db_path)
    try:
        _write_json(
            api_root / "admin" / "fixture-status.json",
            _build_fixture_status_snapshot(conn),
        )
        files_written += 1

        # /sentences/chapter-N.json — one snapshot per chapter. The
        # dev-mode equivalent uses `?chapter=N` query string; the
        # static resolver in `web/src/api/client.ts` rewrites that to
        # `/api/v1/sentences/chapter-N.json`. Each file matches the
        # `SentenceListResponse` shape exactly, so the consuming hook
        # is mode-agnostic.
        chapters = conn.execute(
            "SELECT DISTINCT chapter FROM sentences ORDER BY chapter"
        ).fetchall()
        sentence_ids: list[str] = []
        for ch_row in chapters:
            chapter = int(ch_row["chapter"])
            payload = list_sentences_in_chapter(conn, chapter)
            _write_json(
                api_root / "sentences" / f"chapter-{chapter}.json",
                payload.model_dump(exclude_none=True),
            )
            files_written += 1
            for s in payload.sentences:
                sentence_ids.append(s.sentence_id)

        # /sentences/{id}/parallel.json + candidates.json + ranking.json
        ranking_sentence_ids = {
            row["sentence_id"]
            for row in conn.execute("SELECT sentence_id FROM rankings").fetchall()
        }
        for sid in sentence_ids:
            parallel = get_sentence_parallel(conn, sid)
            _write_json(
                api_root / "sentences" / sid / "parallel.json",
                parallel.model_dump(exclude_none=True),
            )
            files_written += 1
            cand_payload = list_sentence_candidates(conn, sid)
            if cand_payload.candidates:
                _write_json(
                    api_root / "sentences" / sid / "candidates.json",
                    cand_payload.model_dump(exclude_none=True),
                )
                files_written += 1
            if sid in ranking_sentence_ids:
                ranking = get_ranking(conn, sid)
                _write_json(
                    api_root / "sentences" / sid / "ranking.json",
                    ranking.model_dump(exclude_none=True),
                )
                files_written += 1

        # /gsv/{chapter}.json + .md + .txt (or .txt-not-available.json on ties)
        ch_total = 0
        ch_ranked = 0
        ch5_total = 0
        ch5_ranked = 0
        for ch_row in chapters:
            chapter = int(ch_row["chapter"])
            response = compile_chapter_gsv(conn, chapter)
            _write_json(
                api_root / "gsv" / f"{chapter}.json",
                response.model_dump(exclude_none=True),
            )
            files_written += 1
            md_body = render_markdown(response)
            _write_text(api_root / "gsv" / f"{chapter}.md", md_body)
            files_written += 1
            try:
                txt_body = render_plain_text(response)
                _write_text(api_root / "gsv" / f"{chapter}.txt", txt_body)
                files_written += 1
            except UnresolvedTiesError as exc:
                _write_json(
                    api_root / "gsv" / f"{chapter}.txt-not-available.json",
                    {
                        "code": "unresolved_ties",
                        "chapter": chapter,
                        "offending_sentences": exc.details.get("offending_sentences", []),
                    },
                )
                files_written += 1

            ch_total += response.coverage.total_red_letter_sentences
            ch_ranked += response.coverage.ranked_red_letter_sentences
            if chapter == 5:
                ch5_total = response.coverage.total_red_letter_sentences
                ch5_ranked = response.coverage.ranked_red_letter_sentences

        coverage_payload = compute_coverage(conn)
        _write_json(
            api_root / "gsv" / "coverage.json",
            coverage_payload.model_dump(exclude_none=True),
        )
        files_written += 1

        # /chapters/{N}/summaries.json + /chapters/{N}/summaries/{summary_id}.json
        # — Slice 8 chapter-summary read paths.
        for ch_row in chapters:
            chapter = int(ch_row["chapter"])
            summaries_payload = list_summaries_for_chapter(conn, chapter)
            _write_json(
                api_root / "chapters" / str(chapter) / "summaries.json",
                summaries_payload.model_dump(exclude_none=True),
            )
            files_written += 1
            for summary in summaries_payload.summaries:
                _write_json(
                    api_root
                    / "chapters"
                    / str(chapter)
                    / "summaries"
                    / f"{summary.summary_id}.json",
                    summary.model_dump(exclude_none=True),
                )
                files_written += 1
    finally:
        conn.close()

    coverage = BuildStaticCoverage(
        ranked=ch_ranked,
        total=ch_total,
        ch5_ranked=ch5_ranked,
        ch5_total=ch5_total,
    )
    return files_written, coverage


def _run_vite_build(
    project_root: Path,
    web_dir: Path,
    dist_tmp: Path,
) -> int:
    """Shell out to the SPA's static-build script. Returns count of files
    copied from ``web/dist`` to ``dist_tmp``.

    No ``shell=True``; argv list passed directly so the path is never
    interpreted by a shell.
    """
    npm_executable = shutil.which("npm")
    if npm_executable is None:
        raise BuildFailedError(
            "npm not found on PATH; cannot run the SPA static build.",
            details={"web_dir": str(web_dir)},
        )
    completed = subprocess.run(
        [npm_executable, "run", "build:static"],
        cwd=str(web_dir),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BuildFailedError(
            "SPA static build failed.",
            details={
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            },
        )
    web_dist = web_dir / "dist"
    if not web_dist.exists():
        raise BuildFailedError(
            "SPA build produced no dist/ directory.",
            details={"expected_path": str(web_dist)},
        )
    copied = 0
    for src in _walk_files(web_dist):
        rel = src.relative_to(web_dist)
        target = dist_tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    return copied


def _atomic_swap(dist: Path, dist_tmp: Path, dist_bak: Path) -> None:
    """Atomic swap: ``dist -> dist_bak``, ``dist_tmp -> dist``.

    POSIX ``os.replace`` is the atomic primitive. A pre-existing
    ``dist_bak/`` is removed before the swap so we keep at most one
    cycle of history (Reliability §Static-site atomic swap).
    """
    if dist_bak.exists():
        shutil.rmtree(dist_bak)
    if dist.exists():
        dist.rename(dist_bak)
    dist_tmp.rename(dist)


async def run_build_static(
    *,
    project_root: Path,
    db_path: Path,
    include_unranked_placeholders: bool,
) -> BuildStaticResponse:
    """Run the full static-site build pipeline.

    Process-wide async lock prevents two concurrent builds. The build
    runs in a worker thread (the npm subprocess + JSON serialization
    are blocking).
    """
    if _BUILD_LOCK.locked():
        raise BuildInProgressError()
    async with _BUILD_LOCK:
        return await asyncio.to_thread(
            _build_static_sync,
            project_root,
            db_path,
            include_unranked_placeholders,
        )


def _build_static_sync(
    project_root: Path,
    db_path: Path,
    include_unranked_placeholders: bool,
) -> BuildStaticResponse:
    started = time.monotonic()
    dist = project_root / "dist"
    dist_tmp = project_root / "dist.tmp"
    dist_bak = project_root / "dist.bak"
    web_dir = project_root / "web"

    if dist_tmp.exists():
        shutil.rmtree(dist_tmp)
    dist_tmp.mkdir(parents=True, exist_ok=True)

    try:
        spa_files = _run_vite_build(project_root, web_dir, dist_tmp)
        json_files, coverage = _emit_snapshots(
            dist_tmp,
            db_path,
            include_unranked_placeholders=include_unranked_placeholders,
        )
        files_written = spa_files + json_files

        hits = _grep_guard_dist(dist_tmp, project_root)
        if hits:
            shutil.rmtree(dist_tmp, ignore_errors=True)
            raise BuildGuardFailedError(hits)

        _atomic_swap(dist, dist_tmp, dist_bak)
    except DomainError:
        if dist_tmp.exists():
            shutil.rmtree(dist_tmp, ignore_errors=True)
        raise
    except Exception as exc:
        if dist_tmp.exists():
            shutil.rmtree(dist_tmp, ignore_errors=True)
        _logger.error("build.failed", extra={"error": str(exc)})
        raise BuildFailedError(str(exc)) from exc

    took_ms = int((time.monotonic() - started) * 1000)
    _logger.info(
        "build.completed",
        extra={
            "files_written": files_written,
            "took_ms": took_ms,
            "coverage_ranked": coverage.ranked,
            "coverage_total": coverage.total,
        },
    )
    return BuildStaticResponse(
        dist_path=str(dist),
        files_written=files_written,
        coverage=coverage,
        took_ms=took_ms,
    )
