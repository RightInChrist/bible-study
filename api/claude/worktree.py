"""Claude Code worktree subagent runner (replaces the Anthropic SDK path).

CLAUDE.md §Generation mechanism pins the design:

- One generation = one fresh git worktree under ``data/worktrees/{run_id}/{ordinal}/``,
  cut from the bible-study repo's HEAD onto its own throwaway branch
  ``gen/{run_id}/{ordinal}``. The worktree is scratch — nothing is
  committed.
- The runner writes ``input.md`` into the worktree containing the prompt
  body + canonical source bundle + sentence meta, then spawns the
  ``claude`` CLI in non-interactive print mode
  (``claude -p --model <model> --effort <effort> --output-format text <prompt>``)
  with ``cwd=worktree_path``. Pinning ``--model`` + ``--effort`` makes the
  candidate's model identity deterministic per generation rather than
  inheriting whatever the user's interactive session has selected.
- stdout is captured. For prompts that declared ``output_format: json``
  in their front-matter, the runner attempts to parse stdout as JSON
  (defensively stripping triple-backtick ``json`` fences if present);
  on parse failure it raises ``WorktreeGenerationError("invalid_response", ...)``.
- The canonical model identity stored on the candidate is the requested
  ``"{model}+{effort}"`` string (e.g. ``"claude-opus-4-7+xhigh"``). The
  diagnostic ``claude --version`` reading (``claude-code-{version}``) is
  also captured on :class:`WorktreeResult.cli_version` for triage but is
  not used as the candidate's provenance string.
- Timeouts: the spawn is bounded by ``MAX_WALL_CLOCK_SECONDS_PER_SENTENCE``;
  on timeout the process group is killed and the call raises
  ``WorktreeGenerationError("timeout", ...)``.
- Worktree hygiene: every spawn cleans its worktree in a finally-block —
  ``git worktree remove --force`` then a directory wipe — so a crash
  doesn't leak. ``sweep_stale_worktrees`` runs at startup to clean any
  ``data/worktrees/{run_id}/`` directories left over from a prior crash.

Authentication: Claude Code uses the user's existing subscription. The
``claude`` CLI must already be logged in at the OS level. The runner
sanity-checks this at run-creation time by calling ``claude --version``;
if it fails, the run is rejected with ``code='claude_cli_unavailable'``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from api.claude.schemas import (
    ChapterBundle,
    ChapterMeta,
    GenerationErrorCode,
    SentenceMeta,
    SourceBundle,
    StylePrompt,
    WorktreeResult,
)
from api.errors import DomainError
from api.settings import get_settings


_logger = logging.getLogger("bible_study.worktree")


# How big a stdout dump we'll keep around. Subagent output is bounded
# in practice (one sentence translation), but we cap defensively so a
# misbehaving subagent can't OOM the runner.
_MAX_STDOUT_BYTES = 256 * 1024


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class ClaudeCliUnavailableError(DomainError):
    """Raised at run-creation time when ``claude --version`` fails.

    The runner asks the spawner ``cli_available()`` before accepting a
    new run; if false, the run is rejected with this error. Public
    surface uses ``code='claude_cli_unavailable'``.
    """

    status_code = 400
    code = "claude_cli_unavailable"


class WorktreeGenerationError(Exception):
    """Per-sentence failure in the worktree subagent path.

    Captured by the runner and stored on ``generation_run_items``
    (status='failed', error_code=<code>). ``code`` is one of the
    :data:`GenerationErrorCode` literals — for the worktree mechanism
    we use ``timeout``, ``invalid_response``, ``disk_full``, ``internal``.
    """

    def __init__(self, code: GenerationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: GenerationErrorCode = code
        self.message = message


class WorktreeSpawner(Protocol):
    """Minimal protocol the runner depends on. Tests inject a fake.

    ``model`` and ``effort`` are passed through to the ``claude`` CLI as
    ``--model`` / ``--effort`` flags. They default to the process-wide
    settings (``CLAUDE_MODEL`` / ``CLAUDE_EFFORT``) when omitted; the
    runner forwards the run's requested values explicitly so the call
    site is the authoritative source.
    """

    def cli_available(self) -> bool: ...

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
    ) -> WorktreeResult: ...

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
    ) -> WorktreeResult: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canon_bundle_dict(bundle: SourceBundle) -> dict[str, object]:
    """Return a JSON-friendly dict of the bundle, NFC-normalised."""
    raw = bundle.model_dump(exclude_none=True)
    return raw  # bundle resolver already NFC-normalises


def _build_input_text(
    *,
    prompt: StylePrompt,
    bundle: SourceBundle,
    sentence_meta: SentenceMeta,
) -> str:
    """Render the input.md handed to the Claude Code subagent.

    Layout: prompt body, then a fenced JSON source bundle, then the
    sentence meta block, then a per-output-format directive. The body
    already contains the agent's instructions; the rest is data + a
    final reminder.
    """
    bundle_json = json.dumps(
        _canon_bundle_dict(bundle),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    meta_json = json.dumps(
        sentence_meta.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    if prompt.output_format == "json":
        directive = (
            "Return a single JSON object as specified in the prompt body's "
            "Output format section. No markdown fences, no preamble, no trailing "
            "commentary — the runner parses your raw stdout as JSON."
        )
    else:
        directive = (
            "Return only the English translation as a single line / paragraph. "
            "No commentary, no quotation marks, no preamble."
        )
    parts = [
        prompt.body.rstrip(),
        "",
        "## Sentence meta",
        "",
        "```json",
        meta_json,
        "```",
        "",
        "## Source bundle",
        "",
        "```json",
        bundle_json,
        "```",
        "",
        "## Final directive",
        "",
        directive,
        "",
    ]
    return "\n".join(parts)


def _build_chapter_input_text(
    *,
    prompt: StylePrompt,
    bundle: ChapterBundle,
    chapter_meta: ChapterMeta,
) -> str:
    """Render the input.md for a chapter-summary subagent (Slice 8).

    Same shape as :func:`_build_input_text` but the data block is the
    chapter bundle. The bundle JSON inlines:
      - ``chapter`` (int)
      - ``narrative_text`` (list of sentences with sentence_id, verse_range,
        text_sblgnt, text_bsb, is_red_letter)
      - ``red_letter_candidates`` (list of red-letter sentences with
        per-sentence Claude candidates inlined as parsed structured fields)
    """
    bundle_json = json.dumps(
        bundle.model_dump(exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    meta_json = json.dumps(
        chapter_meta.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    if prompt.output_format == "json":
        directive = (
            "Return a single JSON object as specified in the prompt body's "
            "Output format section. No markdown fences, no preamble, no trailing "
            "commentary — the runner parses your raw stdout as JSON."
        )
    else:
        directive = (
            "Return only the chapter summary as a single line / paragraph. "
            "No commentary, no quotation marks, no preamble."
        )
    parts = [
        prompt.body.rstrip(),
        "",
        "## Chapter meta",
        "",
        "```json",
        meta_json,
        "```",
        "",
        "## Chapter bundle",
        "",
        "```json",
        bundle_json,
        "```",
        "",
        "## Final directive",
        "",
        directive,
        "",
    ]
    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    """Strip a single outer ```json ... ``` fence if present.

    The prompt instructs the subagent to emit raw JSON, but a defensive
    strip protects us from the occasional fence-wrapped output.
    """
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match is not None:
        return match.group(1).strip()
    return stripped


def _parse_candidate(prompt: StylePrompt, raw_stdout: str) -> dict[str, object]:
    """Parse the subagent's stdout into the persisted candidate dict.

    For ``output_format=json`` we JSON-parse and require a dict at top
    level. For ``output_format=text`` we wrap the cleaned stdout in
    ``{"english": <stdout>}`` so downstream consumers (UI, ranking) can
    treat both shapes uniformly.
    """
    if prompt.output_format == "json":
        cleaned = _strip_fences(raw_stdout)
        if not cleaned:
            raise WorktreeGenerationError(
                "invalid_response", "Claude Code subagent produced empty stdout"
            )
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise WorktreeGenerationError(
                "invalid_response",
                f"subagent stdout was not valid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})",
            ) from exc
        if not isinstance(parsed, dict):
            raise WorktreeGenerationError(
                "invalid_response",
                f"subagent stdout JSON must be an object, got {type(parsed).__name__}",
            )
        return parsed
    text = raw_stdout.strip()
    if not text:
        raise WorktreeGenerationError(
            "invalid_response", "Claude Code subagent produced empty stdout"
        )
    return {"english": text}


def _detect_model(claude_cli_path: str) -> str:
    """Best-effort detection of the Claude Code CLI version.

    The CLI exposes ``--version`` which prints e.g. ``2.1.126 (Claude Code)``.
    We extract the numeric prefix and return ``claude-code-{version}``.
    On any failure we return ``claude-code-unknown``.
    """
    try:
        result = subprocess.run(
            [claude_cli_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "claude-code-unknown"
    if result.returncode != 0:
        return "claude-code-unknown"
    head = result.stdout.strip().split()
    if head and re.match(r"^\d+(\.\d+)*$", head[0]):
        return f"claude-code-{head[0]}"
    return "claude-code-unknown"


def sweep_stale_worktrees(base_dir: Path, repo_root: Path) -> int:
    """Remove any leftover worktrees + ``gen/*`` branches from prior crashed runs.

    Called at app startup. For each ``base_dir/<run_id>/`` we attempt
    ``git worktree remove --force`` against every ordinal directory,
    then rmtree the parent. After removing worktrees, we prune git's
    internal worktree registry and delete every branch under
    ``gen/<run_id>/`` (which would otherwise leak between runs since
    ``-D`` only fires inside the per-call cleanup path that didn't run
    on a crash).

    Returns the count of run_id parent directories swept.
    """
    if not base_dir.exists():
        _sweep_stale_branches(repo_root)
        return 0
    swept = 0
    for run_dir in base_dir.iterdir():
        if not run_dir.is_dir():
            continue
        for ordinal_dir in list(run_dir.iterdir()):
            if not ordinal_dir.is_dir():
                continue
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(ordinal_dir)],
                    cwd=str(repo_root),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
            if ordinal_dir.exists():
                shutil.rmtree(ordinal_dir, ignore_errors=True)
        try:
            run_dir.rmdir()
        except OSError:
            shutil.rmtree(run_dir, ignore_errors=True)
        swept += 1
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    _sweep_stale_branches(repo_root)
    if swept:
        _logger.info("worktree.sweep", extra={"swept": swept})
    return swept


def _sweep_stale_branches(repo_root: Path) -> None:
    """Delete any local branches matching ``gen/*`` left from crashed runs."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "gen/*"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        name = line.strip().lstrip("* ").strip()
        if not name.startswith("gen/"):
            continue
        try:
            subprocess.run(
                ["git", "branch", "-D", name],
                cwd=str(repo_root),
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass


class ClaudeCodeWorktreeSpawner:
    """Production spawner: real ``git worktree`` + real ``claude`` CLI.

    Holds no per-call state; one instance is shared process-wide.
    """

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        worktree_base_dir: Path | None = None,
        claude_cli_path: str | None = None,
    ) -> None:
        settings = get_settings()
        self._repo_root: Path = (repo_root or settings.project_root).resolve()
        base = worktree_base_dir or (self._repo_root / settings.worktree_base_dir)
        self._worktree_base_dir: Path = base
        self._claude_cli_path: str = claude_cli_path or settings.claude_cli_path

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def worktree_base_dir(self) -> Path:
        return self._worktree_base_dir

    @property
    def claude_cli_path(self) -> str:
        return self._claude_cli_path

    def cli_available(self) -> bool:
        try:
            result = subprocess.run(
                [self._claude_cli_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

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
        settings = get_settings()
        resolved_model = model if model else settings.claude_model
        resolved_effort = effort if effort else settings.claude_effort
        worktree_path = self._worktree_base_dir / run_id / f"{ordinal:04d}"
        branch = f"gen/{run_id}/{ordinal:04d}"
        # Run the (sync) git/process work in a thread so we don't block
        # the event loop.
        return await asyncio.to_thread(
            self._generate_blocking,
            worktree_path=worktree_path,
            branch=branch,
            prompt=prompt,
            input_text=_build_input_text(
                prompt=prompt, bundle=bundle, sentence_meta=sentence_meta
            ),
            timeout_seconds=timeout_seconds,
            model=resolved_model,
            effort=resolved_effort,
        )

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
        settings = get_settings()
        resolved_model = model if model else settings.claude_model
        resolved_effort = effort if effort else settings.claude_effort
        worktree_path = self._worktree_base_dir / run_id / f"{ordinal:04d}"
        branch = f"gen/{run_id}/{ordinal:04d}"
        return await asyncio.to_thread(
            self._generate_blocking,
            worktree_path=worktree_path,
            branch=branch,
            prompt=prompt,
            input_text=_build_chapter_input_text(
                prompt=prompt, bundle=bundle, chapter_meta=chapter_meta
            ),
            timeout_seconds=timeout_seconds,
            model=resolved_model,
            effort=resolved_effort,
        )

    def _generate_blocking(
        self,
        *,
        worktree_path: Path,
        branch: str,
        prompt: StylePrompt,
        input_text: str,
        timeout_seconds: int,
        model: str,
        effort: str,
    ) -> WorktreeResult:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        # Defensive: prior crash could have left the dir/branch lying around.
        if worktree_path.exists():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
                    cwd=str(self._repo_root),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
        try:
            self._add_worktree(worktree_path=worktree_path, branch=branch)
        except WorktreeGenerationError:
            raise

        started_monotonic = time.monotonic()
        started_iso = _now_iso()
        try:
            input_path = worktree_path / "input.md"
            input_path.write_text(input_text, encoding="utf-8")

            stdout = self._invoke_claude(
                worktree_path=worktree_path,
                input_path=input_path,
                timeout_seconds=timeout_seconds,
                model=model,
                effort=effort,
            )
            stdout = unicodedata.normalize("NFC", stdout)
            parsed = _parse_candidate(prompt, stdout)
            elapsed = time.monotonic() - started_monotonic
            return WorktreeResult(
                raw_output=stdout,
                parsed_candidate=parsed,
                model=f"{model}+{effort}",
                cli_version=_detect_model(self._claude_cli_path),
                started_at=started_iso,
                completed_at=_now_iso(),
                elapsed_seconds=elapsed,
            )
        finally:
            self._remove_worktree(worktree_path=worktree_path, branch=branch)

    def _add_worktree(self, *, worktree_path: Path, branch: str) -> None:
        # `git worktree add -B branch <path> HEAD`: create or reset the
        # branch to HEAD and check it out into the worktree. -B (capital)
        # forces a re-create if the branch already exists, which makes
        # this safe under retry.
        try:
            result = subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-B",
                    branch,
                    str(worktree_path),
                    "HEAD",
                ],
                cwd=str(self._repo_root),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorktreeGenerationError(
                "internal", f"git executable not found: {exc}"
            ) from exc
        except subprocess.SubprocessError as exc:
            raise WorktreeGenerationError(
                "internal", f"git worktree add failed to spawn: {exc}"
            ) from exc
        if result.returncode != 0:
            raise WorktreeGenerationError(
                "internal",
                f"git worktree add exited {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}",
            )

    def _remove_worktree(self, *, worktree_path: Path, branch: str) -> None:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=str(self._repo_root),
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
        try:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(self._repo_root),
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        # Best-effort: also try to clear the run-id parent if it's empty.
        parent = worktree_path.parent
        if parent.exists():
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    def _invoke_claude(
        self,
        *,
        worktree_path: Path,
        input_path: Path,
        timeout_seconds: int,
        model: str,
        effort: str,
    ) -> str:
        # The prompt is passed positionally; --model and --effort pin the
        # underlying model + reasoning budget so the candidate's identity
        # is deterministic per generation. cwd=worktree_path means any
        # tool calls the agent makes are scoped to the worktree.
        prompt_text = input_path.read_text(encoding="utf-8")
        env = dict(os.environ)
        try:
            proc = subprocess.Popen(
                [
                    self._claude_cli_path,
                    "-p",
                    "--model",
                    model,
                    "--effort",
                    effort,
                    "--output-format",
                    "text",
                    prompt_text,
                ],
                cwd=str(worktree_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                preexec_fn=os.setsid if os.name == "posix" else None,
                text=True,
            )
        except FileNotFoundError as exc:
            raise WorktreeGenerationError(
                "internal", f"claude CLI not found at {self._claude_cli_path!r}: {exc}"
            ) from exc
        except OSError as exc:
            raise WorktreeGenerationError(
                "internal", f"failed to spawn claude CLI: {exc}"
            ) from exc

        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill_process_group(proc)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise WorktreeGenerationError(
                "timeout",
                f"claude CLI exceeded {timeout_seconds}s timeout",
            ) from None
        if proc.returncode != 0:
            err = (stderr or "").strip()
            if not err:
                err = (stdout or "").strip()[:512]
            raise WorktreeGenerationError(
                "internal",
                f"claude CLI exited {proc.returncode}: {err}",
            )
        if stdout is None:
            stdout = ""
        if len(stdout.encode("utf-8")) > _MAX_STDOUT_BYTES:
            raise WorktreeGenerationError(
                "invalid_response",
                f"claude CLI stdout exceeded {_MAX_STDOUT_BYTES} bytes",
            )
        return stdout

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen) -> None:
        if os.name != "posix":
            proc.kill()
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass


__all__ = [
    "ClaudeCliUnavailableError",
    "ClaudeCodeWorktreeSpawner",
    "WorktreeGenerationError",
    "WorktreeSpawner",
    "sweep_stale_worktrees",
]
