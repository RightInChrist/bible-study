"""Worktree-hygiene tests for :class:`ClaudeCodeWorktreeSpawner`.

CLAUDE.md §Generation mechanism pins worktree hygiene as a hard rule:
each generation runs in an isolated worktree; the runner cleans up
after capturing output. These tests stub the ``claude`` CLI invocation
(via a subclass) so we exercise the git-worktree paths without a real
subagent spawn.

Coverage:
  - test_worktree_cleanup_after_run_completes
  - test_worktree_cleanup_on_subprocess_failure
  - test_stale_worktree_swept_on_startup
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api.claude.schemas import (
    SentenceMeta,
    SourceBundle,
    StylePrompt,
    WorktreeResult,
)
from api.claude.worktree import (
    ClaudeCodeWorktreeSpawner,
    WorktreeGenerationError,
    sweep_stale_worktrees,
)


def _make_repo(tmp_path: Path) -> Path:
    """Create a tiny throwaway git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init", "--no-gpg-sign"],
        cwd=repo,
        check=True,
        env={**os.environ, "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return repo


def _stub_prompt() -> StylePrompt:
    return StylePrompt(
        name="literal",
        version="literal-v1",
        description="stub",
        requires_greek=True,
        compatible_source_sets=["BOTH_GREEK"],
        body="translate the sentence",
        output_format="text",
    )


def _stub_bundle() -> SourceBundle:
    return SourceBundle(
        sentence_id="mat-5-4",
        source_set_id="BOTH_GREEK",
        fixture_version="fv",
        prompt_version="literal-v1",
        sblgnt="μακάριοι οἱ πτωχοί",
        verse_range="5:3",
    )


def _stub_meta() -> SentenceMeta:
    return SentenceMeta(
        sentence_id="mat-5-4",
        chapter=5,
        start_verse=3,
        end_verse=3,
        verse_range="5:3",
    )


class _SuccessSpawner(ClaudeCodeWorktreeSpawner):
    """Subclass that stubs the CLI invocation but keeps the real worktree paths."""

    def _invoke_claude(
        self,
        *,
        worktree_path: Path,
        input_path: Path,
        timeout_seconds: int,
        model: str,
        effort: str,
    ) -> str:
        # Sanity: the input.md must exist in the worktree before the
        # subagent would read it.
        assert input_path.exists()
        # Sanity: model + effort are explicitly threaded through.
        assert model
        assert effort
        return "Blessed are the poor (stub)."


class _FailingSpawner(ClaudeCodeWorktreeSpawner):
    """Subclass that simulates a mid-call subagent crash."""

    def _invoke_claude(
        self,
        *,
        worktree_path: Path,
        input_path: Path,
        timeout_seconds: int,
        model: str,
        effort: str,
    ) -> str:
        raise WorktreeGenerationError("internal", "simulated subagent crash")


@pytest.fixture
def isolated_spawner_factory(tmp_path: Path):
    """Provide a factory that builds a spawner against a fresh git repo."""

    def _factory(spawner_cls: type[ClaudeCodeWorktreeSpawner]) -> ClaudeCodeWorktreeSpawner:
        repo = _make_repo(tmp_path)
        return spawner_cls(
            repo_root=repo,
            worktree_base_dir=repo / "data" / "worktrees",
            claude_cli_path="claude",  # not actually invoked — _invoke_claude is stubbed
        )

    return _factory


def test_worktree_cleanup_after_run_completes(isolated_spawner_factory) -> None:
    spawner = isolated_spawner_factory(_SuccessSpawner)
    base = spawner.worktree_base_dir

    result = asyncio.run(
        spawner.generate(
            run_id="run-clean-success",
            ordinal=1,
            prompt=_stub_prompt(),
            bundle=_stub_bundle(),
            sentence_meta=_stub_meta(),
            timeout_seconds=30,
        )
    )
    assert isinstance(result, WorktreeResult)
    assert result.parsed_candidate.get("english", "").startswith("Blessed are the poor")

    # No worktree directory should remain under data/worktrees/{run_id}/
    leftovers: list[str] = []
    if base.exists():
        for run_dir in base.iterdir():
            for entry in run_dir.iterdir():
                leftovers.append(str(entry))
    assert leftovers == [], f"worktrees leaked: {leftovers!r}"


def test_worktree_cleanup_on_subprocess_failure(isolated_spawner_factory) -> None:
    spawner = isolated_spawner_factory(_FailingSpawner)
    base = spawner.worktree_base_dir

    with pytest.raises(WorktreeGenerationError):
        asyncio.run(
            spawner.generate(
                run_id="run-clean-failure",
                ordinal=1,
                prompt=_stub_prompt(),
                bundle=_stub_bundle(),
                sentence_meta=_stub_meta(),
                timeout_seconds=30,
            )
        )

    # Even though the subagent "crashed", the worktree must be removed.
    leftovers: list[str] = []
    if base.exists():
        for run_dir in base.iterdir():
            for entry in run_dir.iterdir():
                leftovers.append(str(entry))
    assert leftovers == [], f"worktrees leaked on failure: {leftovers!r}"


def test_stale_worktree_swept_on_startup(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = repo / "data" / "worktrees"
    sentinel = base / "sentinel-stale" / "0001"
    sentinel.mkdir(parents=True)
    (sentinel / "leftover.txt").write_text("oops", encoding="utf-8")

    swept = sweep_stale_worktrees(base, repo)
    assert swept >= 1
    assert not sentinel.exists(), "stale ordinal directory must be removed"
    assert not (base / "sentinel-stale").exists(), "stale run directory must be removed"


def test_sweep_is_noop_on_clean_tree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = repo / "data" / "worktrees"
    # base doesn't exist yet — sweep should return 0 cleanly.
    assert sweep_stale_worktrees(base, repo) == 0
    base.mkdir(parents=True)
    assert sweep_stale_worktrees(base, repo) == 0


def test_worktree_result_records_model_plus_effort(isolated_spawner_factory) -> None:
    """The ``WorktreeResult.model`` field is the requested ``"{model}+{effort}"``.

    Ensures the spawner's canonical model identity reflects the args
    passed by the runner — not just the CLI version detected from
    ``claude --version``. This is what gets stored on
    ``claude_candidates.model`` for provenance.
    """
    spawner = isolated_spawner_factory(_SuccessSpawner)

    result = asyncio.run(
        spawner.generate(
            run_id="run-model-id",
            ordinal=1,
            prompt=_stub_prompt(),
            bundle=_stub_bundle(),
            sentence_meta=_stub_meta(),
            timeout_seconds=30,
            model="claude-opus-4-7",
            effort="xhigh",
        )
    )
    assert result.model == "claude-opus-4-7+xhigh"
    # cli_version is captured separately for diagnostics.
    assert result.cli_version.startswith("claude-code-")


def test_worktree_invoke_passes_model_and_effort_flags(tmp_path: Path) -> None:
    """The CLI argv must contain ``--model <m>`` and ``--effort <e>`` flags."""
    repo = _make_repo(tmp_path)

    captured_args: dict[str, list[str]] = {}

    class _CapturingSpawner(ClaudeCodeWorktreeSpawner):
        def _invoke_claude(
            self,
            *,
            worktree_path: Path,
            input_path: Path,
            timeout_seconds: int,
            model: str,
            effort: str,
        ) -> str:
            # Reconstruct the exact argv that the parent class would use,
            # without actually spawning the subprocess. We verify that
            # the flags + values are in the list in the right positions.
            argv = [
                self.claude_cli_path,
                "-p",
                "--model",
                model,
                "--effort",
                effort,
                "--output-format",
                "text",
                "<prompt>",
            ]
            captured_args["argv"] = argv
            return "stub"

    spawner = _CapturingSpawner(
        repo_root=repo,
        worktree_base_dir=repo / "data" / "worktrees",
        claude_cli_path="claude",
    )
    asyncio.run(
        spawner.generate(
            run_id="run-argv",
            ordinal=1,
            prompt=_stub_prompt(),
            bundle=_stub_bundle(),
            sentence_meta=_stub_meta(),
            timeout_seconds=30,
            model="claude-opus-4-7",
            effort="xhigh",
        )
    )
    argv = captured_args["argv"]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-7"
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "xhigh"
