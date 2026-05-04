"""Normalize the upstream Berean English text downloads (BSB or BLB) for Matthew.

Upstream URLs:
  - BSB: ``https://bereanbible.com/bsb.txt``
  - BLB: ``https://literalbible.com/blb.txt``

Both share the same line layout::

    <header lines, tab-terminated>
    Verse\tBerean Standard Bible
    Genesis 1:1\tIn the beginning God created ...
    ...
    Matthew 1:1\tThis is the record of the genealogy ...

This script reads one of those files and writes only the Matthew verses
to the canonical fixture form (``<chapter>:<verse> <text>`` per line).
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

_LINE_PATTERN = re.compile(r"^Matthew\s+(\d+):(\d+)\t(.*)$")


def normalize(input_path: Path, output_path: Path) -> tuple[int, int]:
    raw = input_path.read_text(encoding="utf-8")
    lines: list[str] = []
    verse_count = 0
    for raw_line in raw.splitlines():
        m = _LINE_PATTERN.match(raw_line)
        if not m:
            continue
        chapter = int(m.group(1))
        verse = int(m.group(2))
        text = unicodedata.normalize("NFC", m.group(3).strip())
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
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verses, byte_count = normalize(args.input, args.output)
    print(f"berean: wrote {verses} verses, {byte_count} bytes -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
