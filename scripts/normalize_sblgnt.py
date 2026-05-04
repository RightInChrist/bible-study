"""Normalize the upstream SBLGNT Matthew text into the importer's format.

Upstream: ``Faithlife/SBLGNT`` repo, ``data/sblgnt/text/Matt.txt``. The
upstream file uses lines like ``Matt 5:1\tἸδὼν ...`` and embeds editorial
variant markers (``⸀``, ``⸂...⸃``, ``⸉...⸊``, ``⟦...⟧``). The importer's
``segmenter.parse_verse_lines`` expects ``c:v <text>`` per line with no
markup.

This script reads the upstream file and emits the canonical fixture form:
one line per verse, ``<chapter>:<verse><tab><greek text>``, with variant
markers stripped (kept characters only). Punctuation is preserved exactly
because the SBLGNT segmenter relies on it (``.``, ``;``, ``·``, ``,``).
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path


# SBLGNT editorial markers (apparatus indicators, NOT punctuation):
#   U+2E00 ⸀ — single-word variant
#   U+2E01 ⸁ — paired closer for the above
#   U+2E02 ⸂ — multi-word variant opener
#   U+2E03 ⸃ — multi-word variant closer
#   U+2E04..U+2E07 (other apparatus brackets, kept stripped for safety)
#   U+27E6 ⟦ / U+27E7 ⟧ — disputed-text brackets
# Em-dash (U+2014), Greek punctuation (·, ;, ., ,), and apostrophes are kept.
_VARIANT_MARKERS = {
    "⸀",
    "⸁",
    "⸂",
    "⸃",
    "⸄",
    "⸅",
    "⸆",
    "⸇",
    "⟦",
    "⟧",
}

_LINE_PATTERN = re.compile(r"^Matt\s+(\d+):(\d+)\s+(.*)$")


def _strip_variant_markers(text: str) -> str:
    return "".join(ch for ch in text if ch not in _VARIANT_MARKERS)


def _normalize_line(text: str) -> str:
    text = _strip_variant_markers(text)
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize(input_path: Path, output_path: Path) -> tuple[int, int]:
    """Convert the upstream SBLGNT Matthew file to importer-form text.

    Returns ``(verse_count, byte_count_of_output)`` for diagnostic use.
    """
    raw = input_path.read_text(encoding="utf-8")
    lines: list[str] = []
    verse_count = 0
    for raw_line in raw.splitlines():
        m = _LINE_PATTERN.match(raw_line)
        if not m:
            continue
        chapter = int(m.group(1))
        verse = int(m.group(2))
        text = _normalize_line(m.group(3))
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
        default=Path("fixtures/_upstream/sblgnt-Matt.txt"),
        help="Path to the upstream SBLGNT Matt.txt file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fixtures/sblgnt/matthew.txt"),
        help="Path to write the normalized importer-form fixture.",
    )
    args = parser.parse_args()
    verses, byte_count = normalize(args.input, args.output)
    print(f"sblgnt: wrote {verses} verses, {byte_count} bytes -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
