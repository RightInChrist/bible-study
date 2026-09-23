"""Style-prompt loader (Architect §Prompt artifact format).

Format on disk:

    ---
    name: literal
    version: literal-v1
    description: ...
    requires_greek: true
    compatible_source_sets:
      - SBLGNT_ONLY
      - BOTH_GREEK
    ---

    <markdown body — the prompt template>

The front-matter is YAML; we hand-parse it to avoid pulling PyYAML into
the dependency set for one feature. Front-matter is small, structured,
and entirely under our control.

**Defence-in-depth (Security §Supply chain):** the importer pins a
SHA-256 of every prompt file in ``fixtures/manifest.json`` (see
``api.importer.manifest.ManifestPrompt``); the importer rebuilds the
``style_prompts`` table from those hashes. ``load_prompt`` here parses
the body content for runtime use (the Claude client reads it), but the
importer is the path that establishes that the on-disk bytes match the
manifest.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from api.claude.schemas import RunScopeKind, SourceSetId, StylePrompt


_VALID_RUN_SCOPE_KINDS: frozenset[str] = frozenset(
    {
        "one_sentence",
        "verse_range",
        "whole_chapter",
        "all_unranked_red_letter",
        "chapter_summary",
    }
)


_DEFAULT_PER_SENTENCE_SCOPES: tuple[RunScopeKind, ...] = (
    "one_sentence",
    "verse_range",
    "whole_chapter",
    "all_unranked_red_letter",
)


_FRONT_MATTER_FENCE = "---"


def _split_front_matter(text: str) -> tuple[str, str]:
    """Return ``(front_matter, body)`` from a markdown file with YAML front-matter.

    Raises ``ValueError`` if the file does not begin with ``---\\n`` or the
    closing fence is missing. We refuse to silently treat a malformed
    prompt file as having an empty front-matter — Security §Supply chain
    pinned that prompt edits go through review and the loader's strictness
    is part of that review surface.
    """
    if not text.startswith(_FRONT_MATTER_FENCE + "\n"):
        raise ValueError("style prompt must start with '---\\n' front-matter fence")
    rest = text[len(_FRONT_MATTER_FENCE) + 1 :]
    closing = rest.find("\n" + _FRONT_MATTER_FENCE + "\n")
    if closing == -1:
        # Permit ``---\n`` at the very end with no trailing newline.
        if rest.endswith("\n" + _FRONT_MATTER_FENCE):
            front = rest[: -(len(_FRONT_MATTER_FENCE) + 1)]
            return front, ""
        raise ValueError("style prompt missing closing '---' front-matter fence")
    front = rest[:closing]
    body = rest[closing + len(_FRONT_MATTER_FENCE) + 2 :]
    return front, body


def _parse_front_matter(front: str) -> dict[str, object]:
    """Very small YAML subset: ``key: value`` per line plus ``- value`` lists.

    Sufficient for the prompt-fixture shape (a few scalars + one list).
    Refuses to parse anything more complex; raises ``ValueError`` on
    indentation that doesn't match the expected list-under-key shape.
    """
    out: dict[str, object] = {}
    current_list_key: str | None = None
    current_list: list[str] = []
    for raw_line in front.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # List item: ``  - value``
        if line.lstrip().startswith("- "):
            if current_list_key is None:
                raise ValueError("list item without preceding key in front-matter")
            current_list.append(line.lstrip()[2:].strip())
            continue
        # Close any in-progress list.
        if current_list_key is not None:
            out[current_list_key] = list(current_list)
            current_list_key = None
            current_list = []
        if ":" not in line:
            raise ValueError(f"unparseable front-matter line: {line!r}")
        key_raw, _, value_raw = line.partition(":")
        key = key_raw.strip()
        value = value_raw.strip()
        if value == "":
            current_list_key = key
            current_list = []
            continue
        out[key] = _coerce_scalar(value)
    if current_list_key is not None:
        out[current_list_key] = list(current_list)
    return out


def _coerce_scalar(value: str) -> object:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _validate_source_sets(values: list[str]) -> list[SourceSetId]:
    valid: set[str] = {
        "SBLGNT_ONLY",
        "BYZ_ONLY",
        "BOTH_GREEK",
        "GREEK_PLUS_BIB",
        "GREEK_PLUS_BLB",
        "GREEK_PLUS_BSB",
        "ENGLISH_ONLY_BSB",
    }
    for v in values:
        if v not in valid:
            raise ValueError(f"unknown source_set_id in compatible_source_sets: {v!r}")
    return list(values)  # type: ignore[return-value]


def _validate_run_scopes(values: list[str]) -> list[RunScopeKind]:
    for v in values:
        if v not in _VALID_RUN_SCOPE_KINDS:
            raise ValueError(f"unknown run scope kind in compatible_run_scopes: {v!r}")
    return list(values)  # type: ignore[return-value]


def load_prompt(path: Path) -> StylePrompt:
    """Read a prompt file from disk and return the parsed ``StylePrompt``.

    Raises ``ValueError`` on malformed front-matter or unknown
    ``compatible_source_sets`` entries.

    The optional ``output_format`` front-matter scalar drives whether the
    worktree runner JSON-parses the agent's stdout (``json``) or stores
    it as plain text (``text``). Default is ``text`` if omitted — the
    three pre-existing simple-style prompts all return plain English.
    """
    text = path.read_text(encoding="utf-8")
    front, body = _split_front_matter(text)
    parsed = _parse_front_matter(front)
    name = parsed.get("name")
    version = parsed.get("version")
    description = parsed.get("description")
    requires_greek = parsed.get("requires_greek")
    compatible = parsed.get("compatible_source_sets") or []
    output_format_raw = parsed.get("output_format", "text")
    wants_context_window = parsed.get("wants_context_window", False)
    wants_chapter_input = parsed.get("wants_chapter_input", False)
    compatible_scopes_raw = parsed.get("compatible_run_scopes")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ValueError("style prompt front-matter requires 'name' and 'version' (strings)")
    if not isinstance(description, str):
        raise ValueError("style prompt front-matter requires 'description' (string)")
    if not isinstance(requires_greek, bool):
        raise ValueError("style prompt front-matter requires 'requires_greek' (bool)")
    if not isinstance(compatible, list) or not all(isinstance(v, str) for v in compatible):
        raise ValueError(
            "style prompt front-matter requires 'compatible_source_sets' (list of strings)"
        )
    if output_format_raw not in ("text", "json"):
        raise ValueError(
            f"style prompt 'output_format' must be 'text' or 'json' (got {output_format_raw!r})"
        )
    if not isinstance(wants_context_window, bool):
        raise ValueError(
            "style prompt 'wants_context_window' must be a bool (got "
            f"{wants_context_window!r})"
        )
    if not isinstance(wants_chapter_input, bool):
        raise ValueError(
            "style prompt 'wants_chapter_input' must be a bool (got "
            f"{wants_chapter_input!r})"
        )
    if wants_chapter_input and wants_context_window:
        raise ValueError(
            "style prompt cannot set both 'wants_chapter_input' and "
            "'wants_context_window' — chapter input replaces the per-sentence bundle"
        )
    source_sets = _validate_source_sets(compatible)
    if compatible_scopes_raw is None:
        # Default: chapter prompts get [chapter_summary]; per-sentence
        # prompts get the four per-sentence scope kinds. Encoded as the
        # default factory on the model, but we want the parsed prompt's
        # compatible_run_scopes to reflect the on-disk fixture's choice
        # explicitly when present.
        compatible_scopes: list[RunScopeKind] = (
            ["chapter_summary"]
            if wants_chapter_input
            else list(_DEFAULT_PER_SENTENCE_SCOPES)
        )
    else:
        if not isinstance(compatible_scopes_raw, list) or not all(
            isinstance(v, str) for v in compatible_scopes_raw
        ):
            raise ValueError(
                "style prompt 'compatible_run_scopes' must be a list of strings"
            )
        compatible_scopes = _validate_run_scopes(compatible_scopes_raw)
    if wants_chapter_input and source_sets:
        raise ValueError(
            "style prompt with 'wants_chapter_input: true' must not "
            "declare 'compatible_source_sets' — its source set is always "
            "the synthetic 'CHAPTER_BUNDLE' literal"
        )
    return StylePrompt(
        name=name,
        version=version,
        description=description,
        requires_greek=requires_greek,
        compatible_source_sets=source_sets,
        body=body,
        output_format=output_format_raw,  # type: ignore[arg-type]
        wants_context_window=wants_context_window,
        wants_chapter_input=wants_chapter_input,
        compatible_run_scopes=compatible_scopes,
    )


def body_sha256(body: str) -> str:
    """SHA-256 of the prompt body (post-front-matter), hex-encoded.

    Used by the importer to populate ``style_prompts.body_sha256``. We hash
    the body, not the whole file, so a comment-only edit to the front-matter
    description doesn't invalidate downstream snapshots that happened to
    reference the same body.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
