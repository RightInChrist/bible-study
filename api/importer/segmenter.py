"""SBLGNT sentence segmenter and Greek word tokenizer.

Architect §Sentence segmenter pinned the SBLGNT path: "uses the edition's
punctuation directly to produce canonical sentences." Architect §Word
tokenizer pinned the rule: "a maximal run of Greek letters with diacritics
preserved (Unicode categories L* and M* over the Greek and Coptic / Greek
Extended ranges); punctuation and whitespace are word boundaries; clitics
joined by an apostrophe (U+2019 or U+0027) — e.g. δι᾽ — form a single word."

Sentence-terminating punctuation in SBLGNT: ``.`` (period), ``;`` (Greek
question mark), ``·`` (Greek ano teleia / mid-dot, sentence-ending in this
fixture set), and ``!`` (rare). Periods and question marks always end a
sentence; we treat ``·`` as sentence-ending as well so the Beatitudes split
into one sentence per beatitude — that's the whole point of Matthew 5:3-12
in this slice.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Sentence-ending punctuation. SBLGNT uses ``.`` and ``·`` (U+00B7) routinely;
# ``;`` is the Greek question mark; ``!`` is rare. We split on any of these.
_SENTENCE_TERMINATORS = ".;·!"


_GREEK_LETTER_RANGES = (
    (0x0370, 0x03FF),  # Greek and Coptic
    (0x1F00, 0x1FFF),  # Greek Extended
)
_APOSTROPHES = {"’", "ʼ", "'", "᾽", "᾿"}


def _is_greek_letter(ch: str) -> bool:
    cp = ord(ch)
    in_range = any(lo <= cp <= hi for lo, hi in _GREEK_LETTER_RANGES)
    if not in_range:
        return False
    cat = unicodedata.category(ch)
    # L* (letters) plus M* (combining marks — diacritics like polytonic accents)
    return cat[0] in ("L", "M")


@dataclass(frozen=True)
class TokenizedWord:
    text: str
    byte_start: int
    byte_end: int


def tokenize_greek(text: str) -> list[TokenizedWord]:
    """Tokenize an NFC-normalized Greek string per the pinned tokenizer rule.

    Returns a list of (text, byte_start, byte_end) tuples; offsets are in
    UTF-8 bytes within ``text`` for ``words.byte_start`` / ``words.byte_end``.
    """
    text = unicodedata.normalize("NFC", text)
    words: list[TokenizedWord] = []
    i = 0
    n = len(text)
    while i < n:
        if _is_greek_letter(text[i]):
            start_char = i
            j = i
            while j < n:
                if _is_greek_letter(text[j]):
                    j += 1
                    continue
                # Apostrophe between two letters joins clitics into one word
                if text[j] in _APOSTROPHES and j + 1 < n and _is_greek_letter(text[j + 1]):
                    j += 1
                    continue
                # Apostrophe right after a letter, end-of-word (e.g. δι᾽) — include the apostrophe
                if text[j] in _APOSTROPHES:
                    j += 1
                    break
                break
            word_text = text[start_char:j]
            byte_start = len(text[:start_char].encode("utf-8"))
            byte_end = byte_start + len(word_text.encode("utf-8"))
            words.append(TokenizedWord(text=word_text, byte_start=byte_start, byte_end=byte_end))
            i = j
        else:
            i += 1
    return words


@dataclass(frozen=True)
class SegmentedSentence:
    chapter: int
    ordinal_in_chapter: int
    text: str
    start_chapter: int
    start_verse: int
    end_chapter: int
    end_verse: int
    starts_at_verse_boundary: bool
    ends_at_verse_boundary: bool


_VERSE_LINE = re.compile(r"^(\d+):(\d+)\s+(.*)$")


@dataclass(frozen=True)
class _VerseChunk:
    chapter: int
    verse: int
    text: str  # NFC-normalized, leading/trailing whitespace stripped


def parse_verse_lines(content: str) -> list[_VerseChunk]:
    """Parse a fixture file laid out one verse per line as ``c:v <text>``.

    Used for SBLGNT, Byzantine, BSB, BLB, WEB. Lines that don't match the
    pattern are ignored (allows blank lines / comments in fixtures).
    """
    chunks: list[_VerseChunk] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _VERSE_LINE.match(line)
        if not m:
            continue
        chapter = int(m.group(1))
        verse = int(m.group(2))
        text = unicodedata.normalize("NFC", m.group(3).strip())
        chunks.append(_VerseChunk(chapter=chapter, verse=verse, text=text))
    return chunks


def segment_sblgnt(content: str) -> list[SegmentedSentence]:
    """Segment SBLGNT verse-keyed fixture text into sentences.

    Algorithm:
      1. Parse one ``_VerseChunk`` per ``c:v <text>`` line.
      2. Walk the verse text in order, accumulating characters into a
         pending sentence buffer along with byte-level provenance for the
         starting and ending verse + whether each side begins/ends at the
         verse boundary.
      3. Emit a sentence whenever a sentence terminator is encountered.

    "Begins at verse boundary" means the sentence's first non-whitespace
    character is the first non-whitespace character of its starting verse.
    "Ends at verse boundary" means the sentence's last non-whitespace
    character is the last non-whitespace character of its ending verse.
    """
    chunks = parse_verse_lines(content)
    sentences: list[SegmentedSentence] = []

    # Accumulator state for the in-progress sentence.
    buf: list[str] = []
    start_chapter: int | None = None
    start_verse: int | None = None
    starts_at_boundary = False
    last_verse_chapter = 0
    last_verse_number = 0
    chapter_ordinal_counters: dict[int, int] = {}

    def _flush(current_chapter: int, current_verse: int, ends_at_boundary: bool) -> None:
        nonlocal buf, start_chapter, start_verse, starts_at_boundary
        if not buf:
            return
        text = "".join(buf).strip()
        if not text:
            buf = []
            start_chapter = None
            start_verse = None
            starts_at_boundary = False
            return
        text_nfc = unicodedata.normalize("NFC", text)
        assert start_chapter is not None and start_verse is not None
        ch = start_chapter
        ordinal = chapter_ordinal_counters.get(ch, 0) + 1
        chapter_ordinal_counters[ch] = ordinal
        sentences.append(
            SegmentedSentence(
                chapter=ch,
                ordinal_in_chapter=ordinal,
                text=text_nfc,
                start_chapter=start_chapter,
                start_verse=start_verse,
                end_chapter=current_chapter,
                end_verse=current_verse,
                starts_at_verse_boundary=starts_at_boundary,
                ends_at_verse_boundary=ends_at_boundary,
            )
        )
        buf = []
        start_chapter = None
        start_verse = None
        starts_at_boundary = False

    for chunk in chunks:
        verse_text = chunk.text
        last_verse_chapter = chunk.chapter
        last_verse_number = chunk.verse
        # Walk char by char so we know when we hit a terminator.
        char_index = 0
        # If the in-progress sentence is empty when this verse begins, then
        # whatever sentence starts at the first non-whitespace char of this
        # verse begins at the verse boundary.
        char_index_in_verse = 0
        n = len(verse_text)
        while char_index_in_verse < n:
            ch = verse_text[char_index_in_verse]
            if not buf:
                # Skip leading whitespace so we don't start a sentence with " ".
                if ch.isspace():
                    char_index_in_verse += 1
                    continue
                start_chapter = chunk.chapter
                start_verse = chunk.verse
                # Begins at the verse boundary iff this char is the first
                # non-whitespace char of its verse (char_index_in_verse is
                # the offset within this verse).
                stripped_offset = len(verse_text) - len(verse_text.lstrip())
                starts_at_boundary = char_index_in_verse == stripped_offset
            buf.append(ch)
            if ch in _SENTENCE_TERMINATORS:
                # Look ahead in this verse: is this the last non-whitespace
                # character of the verse? If so, the sentence ends at the
                # verse boundary.
                rest = verse_text[char_index_in_verse + 1 :]
                ends_at_boundary = rest.strip() == ""
                _flush(chunk.chapter, chunk.verse, ends_at_boundary)
            char_index_in_verse += 1
            char_index += 1
        # Append a single space between verses so words don't run together
        # if the previous verse didn't end the sentence.
        if buf:
            buf.append(" ")

    # Flush any trailing sentence (no terminator in fixture — unusual but allowed).
    if buf:
        _flush(last_verse_chapter, last_verse_number, ends_at_boundary=True)

    return sentences
