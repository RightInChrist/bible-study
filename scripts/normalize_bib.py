"""Normalize the upstream BSB Translation Tables into the BIB Matthew TSV.

Upstream: ``https://bereanbible.com/bsb_tables.tsv`` — one row per word
of the entire Bible (Hebrew + Greek). Columns relevant for the New
Testament Greek interlinear:

  - col 4  ``Language``       (we filter ``Greek``)
  - col 5  ``WLC/Nestle base`` (clean Greek surface form)
  - col 7  ``Translit``       (transliteration)
  - col 11 ``Str Grk``        (Strong's Greek number, no leading ``G``)
  - col 12 ``VerseId``        (e.g. ``Matthew 5:3``; only set on the
                               first word of a verse — forward-fill)
  - col 18 `` BSB version ``  (the English gloss; whitespace-padded)

The book filter is ``VerseId.startswith("Matthew ")``. Output is the
canonical BIB fixture form expected by ``api/importer/runner.py``:

    verse\tposition\tgreek\tstrong\ttransliteration\tgloss\tinflected

``position`` is reset to 1 at every new verse. ``strong`` is prefixed
with ``G`` (the importer's existing fixture used ``G3107`` etc.).
"""
from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from pathlib import Path


def _verse_key(verse_id: str) -> tuple[int, int] | None:
    if not verse_id.startswith("Matthew "):
        return None
    rest = verse_id[len("Matthew ") :]
    try:
        chapter_str, verse_str = rest.split(":")
        return int(chapter_str), int(verse_str)
    except (ValueError, IndexError):
        return None


def normalize(input_path: Path, output_path: Path) -> tuple[int, int]:
    out_rows: list[tuple[str, int, str, str, str, str, str]] = []
    current_chapter: int | None = None
    current_verse: int | None = None
    position = 0

    with input_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            return 0, 0
        expected = "Heb Sort"
        if header[0] != expected:
            raise ValueError(f"unexpected header row {header[:3]!r}")
        for row in reader:
            if len(row) < 19:
                continue
            language = row[4]
            if language != "Greek":
                continue
            verse_id = row[12].strip()
            if verse_id:
                key = _verse_key(verse_id)
                if key is None:
                    # Non-Matthew Greek row — once we leave Matthew, ignore the rest.
                    if current_chapter is not None and not verse_id.startswith("Matthew "):
                        # Stop reading once we've passed Matthew (saves seconds on a 217k-row scan).
                        break
                    current_chapter = None
                    current_verse = None
                    position = 0
                    continue
                current_chapter, current_verse = key
                position = 0
            if current_chapter is None or current_verse is None:
                # Greek word seen before any Matthew verse marker — ignore.
                continue
            position += 1
            greek = unicodedata.normalize("NFC", row[5].strip())
            transliteration = row[7].strip()
            strong_raw = row[11].strip()
            if not greek or not strong_raw.isdigit():
                # Some rows have empty Greek (continuation pads) — skip them.
                position -= 1
                continue
            gloss = row[18].strip()
            verse_label = f"{current_chapter}:{current_verse}"
            out_rows.append(
                (
                    verse_label,
                    position,
                    greek,
                    f"G{strong_raw}",
                    transliteration,
                    gloss,
                    gloss,
                )
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "verse",
                "position",
                "greek",
                "strong",
                "transliteration",
                "gloss",
                "inflected",
            ]
        )
        for r in out_rows:
            writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])

    output_bytes = output_path.read_bytes()
    return len(out_rows), len(output_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("fixtures/_upstream/bsb_tables.tsv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fixtures/bib/matthew.tsv"),
    )
    args = parser.parse_args()
    rows, byte_count = normalize(args.input, args.output)
    print(f"bib: wrote {rows} word rows, {byte_count} bytes -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
