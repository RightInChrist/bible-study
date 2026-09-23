"""Claude generation backend (Slice 3a-redo).

Modules:
    - ``prompts`` — load + parse versioned style-prompt fixtures.
    - ``sources`` — source-set resolver + canonical snapshot hashing.
    - ``worktree`` — Claude Code subagent worktree spawner + typed errors.
    - ``schemas`` — Pydantic models for prompts and source bundles.

The runner / SSE / API routes live under ``api.runs``; this package owns
everything that prepares context for Claude Code or hashes source
bundles. There is no Anthropic SDK dependency here — generation is a
subprocess invocation of the ``claude`` CLI per CLAUDE.md §Generation
mechanism.
"""
