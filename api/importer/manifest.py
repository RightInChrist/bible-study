from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    source_url: str
    retrieved_at: str
    release_tag: str
    sha256: str = Field(min_length=64, max_length=64)
    # Optional audit-trail fields, added when ``path`` is a normalized
    # derivative of an upstream file (one normalized fixture per source).
    # ``upstream_sha256`` pins the *upstream* file (so a re-download of a
    # different release surfaces immediately); ``sha256`` above pins the
    # *on-disk* normalized fixture (so the importer's hash check still
    # protects against on-disk tampering).
    upstream_path: str | None = None
    upstream_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    normalizer: str | None = None


class ManifestSegmenter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edition: Literal["sblgnt"]
    rule: str


class ManifestPrompt(BaseModel):
    """Pin one style-prompt fixture file by path + SHA-256.

    The body content is on disk under ``fixtures/prompts/{path}``; the
    front-matter (``name``, ``version``, ``description``,
    ``compatible_source_sets``) is the source of truth read at import time
    by ``api.claude.prompts.load_prompt``. The hash here pins the file
    bytes so a runtime tampering with the prompt body fails the importer's
    SHA-256 check (defence-in-depth — Security §Supply chain).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    path: str
    sha256: str = Field(min_length=64, max_length=64)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    segmenter: ManifestSegmenter
    byzantine_mode: str
    word_tokenizer: str
    source_snapshot_canon_version: str
    files: list[ManifestFile]
    prompts: list[ManifestPrompt] = Field(default_factory=list)


def load_manifest(manifest_path: Path) -> Manifest:
    raw = manifest_path.read_text(encoding="utf-8")
    return Manifest.model_validate_json(raw)


def manifest_disk_hash(manifest_path: Path) -> str:
    """SHA-256 of the **canonicalised** manifest payload, hex-encoded.

    PLAN §fixture_version pins the rule: "SHA-256 of canonicalised JSON
    matching ``source_snapshots`` canonicalisation." Hashing raw on-disk
    bytes would couple the hash to formatting (indentation, key order,
    trailing newline) so a no-op reformat of ``manifest.json`` would
    falsely flip the staleness pill.

    Canonicalisation is delegated to :func:`serialize_manifest_for_hashing`
    so the rule is defined exactly once and the source-snapshot writer can
    reuse it.
    """
    manifest = load_manifest(manifest_path)
    canonical = serialize_manifest_for_hashing(manifest)
    h = hashlib.sha256()
    h.update(canonical.encode("utf-8"))
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest_files(manifest: Manifest, project_root: Path) -> list[str]:
    """Return a list of error strings for any file whose on-disk SHA-256
    does not match the manifest's pinned hash. Empty list = all files match.

    Security §Supply chain: importer aborts ATOMIC-OR-NOTHING on any mismatch.
    Both ``files`` (text fixtures) and ``prompts`` (style-prompt artifacts)
    are checked — a tampered prompt body would otherwise let an attacker
    redirect Claude's behaviour without changing a tracked-by-hash file.
    """
    errors: list[str] = []
    for entry in manifest.files:
        full = project_root / entry.path
        if not full.exists():
            errors.append(f"missing: {entry.path}")
            continue
        actual = file_sha256(full)
        if actual != entry.sha256:
            errors.append(
                f"hash mismatch: {entry.path} expected {entry.sha256} got {actual}"
            )
    for prompt in manifest.prompts:
        full = project_root / prompt.path
        if not full.exists():
            errors.append(f"missing: {prompt.path}")
            continue
        actual = file_sha256(full)
        if actual != prompt.sha256:
            errors.append(
                f"hash mismatch: {prompt.path} expected {prompt.sha256} got {actual}"
            )
    return errors


def find_file(manifest: Manifest, name: str) -> ManifestFile:
    for entry in manifest.files:
        if entry.name == name:
            return entry
    raise KeyError(f"manifest has no file named {name!r}")


def serialize_manifest_for_hashing(manifest: Manifest) -> str:
    """Canonical JSON of the manifest, used for ``fixture_version.manifest_hash``.

    Uses the same canonicalization rule as ``source_snapshots`` (Architect §
    canonicalization rule): UTF-8, ``ensure_ascii=False``, ``sort_keys=True``,
    ``separators=(',', ':')``.
    """
    payload = json.loads(manifest.model_dump_json())
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
