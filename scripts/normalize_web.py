"""Normalize the upstream WEB Matthew USFM file for the importer.

Upstream: ``https://ebible.org/Scriptures/eng-web_usfm.zip`` (extract
``70-MATeng-web.usfm``). USFM tags relevant for our purposes:

  - ``\\c <n>`` — chapter marker
  - ``\\v <n> <text>`` — verse marker; text continues until next \\v or \\c
  - ``\\w word|strong="Gxxxx"\\w*`` — Strong's-tagged word; we want the
    surface form (``word``), not the markup.
  - ``\\f + ... \\f*`` — footnote, drop entirely.
  - ``\\p``, ``\\q1``, ``\\q2``, ``\\m``, ``\\b``, ``\\nb`` — paragraph
    structure markers, drop.

This script strips USFM markup and emits one line per verse in the
canonical fixture form: ``<chapter>:<verse> <english text>``.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path


_FOOTNOTE_PATTERN = re.compile(r"\\f\s.*?\\f\*", re.DOTALL)
_CROSSREF_PATTERN = re.compile(r"\\x\s.*?\\x\*", re.DOTALL)
_WORD_PATTERN = re.compile(r"\\w\s+([^|\\]+)\|[^\\]*\\w\*")
_OTHER_TAG_PATTERN = re.compile(r"\\[a-zA-Z][a-zA-Z0-9]*\*?")
_VERSE_HEAD_PATTERN = re.compile(r"^\\v\s+(\d+)\s*(.*)$")
_CHAPTER_HEAD_PATTERN = re.compile(r"^\\c\s+(\d+)\s*$")


def _strip_usfm(line: str) -> str:
    line = _FOOTNOTE_PATTERN.sub("", line)
    line = _CROSSREF_PATTERN.sub("", line)
    line = _WORD_PATTERN.sub(r"\1", line)
    line = _OTHER_TAG_PATTERN.sub("", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def normalize(input_path: Path, output_path: Path) -> tuple[int, int]:
    raw = input_path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    verses: list[tuple[int, int, str]] = []
    chapter: int | None = None
    pending_verse: int | None = None
    pending_text: list[str] = []

    def _flush() -> None:
        nonlocal pending_verse, pending_text
        if chapter is None or pending_verse is None:
            pending_verse = None
            pending_text = []
            return
        text = " ".join(pending_text).strip()
        text = _strip_usfm(text)
        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            verses.append((chapter, pending_verse, text))
        pending_verse = None
        pending_text = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        m_chapter = _CHAPTER_HEAD_PATTERN.match(line)
        if m_chapter:
            _flush()
            chapter = int(m_chapter.group(1))
            continue
        m_verse = _VERSE_HEAD_PATTERN.match(line)
        if m_verse:
            _flush()
            pending_verse = int(m_verse.group(1))
            tail = m_verse.group(2)
            if tail:
                pending_text.append(tail)
            continue
        if pending_verse is None:
            continue
        # Continuation lines — paragraph markers like \p stand alone, but
        # \q1, \q2 etc. may precede text on the same logical line.
        pending_text.append(line)

    _flush()

    output_lines = [f"{c}:{v} {t}" for c, v, t in verses]
    output = "\n".join(output_lines) + "\n"
    output_bytes = output.encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output_bytes)
    return len(verses), len(output_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("fixtures/_upstream/web-Matt.usfm"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fixtures/web/matthew.txt"),
    )
    args = parser.parse_args()
    verses, byte_count = normalize(args.input, args.output)
    print(f"web: wrote {verses} verses, {byte_count} bytes -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
