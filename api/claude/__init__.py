"""Claude generation backend (Slice 3a).

Modules:
    - ``prompts`` — load + parse versioned style-prompt fixtures.
    - ``sources`` — source-set resolver + canonical snapshot hashing.
    - ``client`` — Anthropic SDK wrapper with cost estimation + typed errors.
    - ``schemas`` — Pydantic models for prompts and source bundles.

The runner / SSE / API routes live under ``api.runs``; this package owns
everything that talks to Anthropic or to the prompt and source-bundle
fixtures.
"""
