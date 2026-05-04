"""Normalize the upstream Byzantine Matthew CSV into the importer's format.

Upstream: ``byztxt/byzantine-majority-text`` repo,
``csv-unicode/strongs/no-parsing/MAT.csv``. The upstream file is one
verse per CSV row: ``chapter,verse,text``. Text is unaccented lowercase
Greek (no polytonic diacritics) — the Architect deferred sentence-level
Byzantine alignment to v2 (``CLAUDE.md`` JTBD #1, ``TODO.md`` v2 list)
so the verse-keyed reference text is sufficient for v1.

Emits one line per verse in the canonical fixture form:
``<chapter>:<verse><tab><greek text>``.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path


def normalize(input_path: Path, output_path: Path) -> tuple[int, int]:
    """Convert the upstream Byzantine Matthew CSV into importer-form text."""
    raw = input_path.read_text(encoding="utf-8")
    lines: list[str] = []
    verse_count = 0
    reader = csv.DictReader(raw.splitlines())
    for row in reader:
        chapter = int(row["chapter"])
        verse = int(row["verse"])
        text = unicodedata.normalize("NFC", row["text"].strip())
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        lines.append(f"{chapter}:{verse} {text}")
        verse_count += 1
    output = "\n".join(lines) + "\n"
    output_bytes = output.encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output_bytes)
    return verse_count, len(output_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("fixtures/_upstream/byzantine-MAT.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fixtures/byzantine/matthew.txt"),
    )
    args = parser.parse_args()
    verses, byte_count = normalize(args.input, args.output)
    print(f"byzantine: wrote {verses} verses, {byte_count} bytes -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
