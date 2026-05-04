/**
 * Reference rendering rules R1–R5 (SPEC.md §Reference rendering rules).
 *
 * These are pure functions: input is a focal SBLGNT sentence's verse-range
 * boundary flags + a list of verses from a reference column (Byzantine or
 * an English translation), output is a list of `Segment`s the React
 * component renders. Rules are centralised so a change here propagates
 * uniformly to the parallel reader, the sentence detail drawer, and (in
 * future slices) the Rank top-strip.
 *
 * v1 keeps this segmentation coarse: we mute the *verse* that we know is
 * partially-leaked, rather than mid-verse word slicing. R1 says "the
 * this-sentence portion HIGHLIGHTED and the rest MUTED"; without
 * word-level alignment for English/Byzantine the closest honest
 * approximation is "highlight the verse(s) the sentence fully owns; mute
 * any verse the sentence only partially owns." Designer's R2/R3 caveats
 * already warn the user that these projections are approximate; the `~`
 * glyph rides on muted verses in those columns.
 *
 * When the alignment matures (BIB word-level for English, deferred
 * Byzantine word-range projection) this module is the one place to swap
 * for true partial-verse highlighting.
 */
export interface FocalSentence {
  start_chapter: number;
  start_verse: number;
  end_chapter: number;
  end_verse: number;
  starts_at_verse_boundary: boolean;
  ends_at_verse_boundary: boolean;
}

export interface RefVerse {
  chapter: number;
  verse: number;
  text: string;
}

export type SegmentEmphasis = "highlight" | "muted";

export interface Segment {
  chapter: number;
  verse: number;
  text: string;
  emphasis: SegmentEmphasis;
}

/**
 * Apply R1 / R3 (and R2 for Byzantine) to a list of reference verses.
 *
 * - R4 clean case (`starts_at_verse_boundary && ends_at_verse_boundary`):
 *   every verse rendered as `highlight`. No muting, no `~` glyph.
 * - R1 partial-verse: the *first* verse is muted iff
 *   `starts_at_verse_boundary === false` (the sentence began mid-way
 *   through it, so an earlier sibling sentence owns the start). The
 *   *last* verse is muted iff `ends_at_verse_boundary === false`.
 *   Interior verses (when the span covers 3+ verses) are always
 *   highlighted — they belong wholly to this sentence.
 *
 * Edge case: span of length 1 with both boundaries false (the sentence
 * is a tiny piece of a single verse). Both flags fire; we mute the verse
 * — we can't say which half is ours. This produces a faithful "see the
 * full verse for context, none of it is wholly ours" rendering. R5
 * exempts SBLGNT itself from this behaviour; SBLGNT is rendered from the
 * sentence's `text_sblgnt` field directly, never via this function.
 */
export function applyReferenceRules(
  focal: FocalSentence,
  verses: readonly RefVerse[],
): Segment[] {
  if (verses.length === 0) {
    return [];
  }
  const segments: Segment[] = [];
  for (let i = 0; i < verses.length; i += 1) {
    const verse = verses[i];
    const isFirst = i === 0;
    const isLast = i === verses.length - 1;
    const muteForStart = isFirst && !focal.starts_at_verse_boundary;
    const muteForEnd = isLast && !focal.ends_at_verse_boundary;
    const emphasis: SegmentEmphasis = muteForStart || muteForEnd ? "muted" : "highlight";
    segments.push({
      chapter: verse.chapter,
      verse: verse.verse,
      text: verse.text,
      emphasis,
    });
  }
  return segments;
}

/**
 * Whether the column should display the `~` projection caveat (R2 for
 * Byzantine, R3 for English). True iff the segmentation produced any
 * `muted` segment OR the focal sentence is mid-verse on either side
 * (R3's tooltip wants to fire even on a single fully-highlighted verse
 * when the sentence is technically a sub-span of it).
 *
 * R4 (both boundaries true) returns false — no caveat surfacing on the
 * clean case.
 */
export function shouldShowProjectionCaveat(focal: FocalSentence): boolean {
  return !focal.starts_at_verse_boundary || !focal.ends_at_verse_boundary;
}

/**
 * Whether the row should expose the `⚓` boundary anchor glyph (Designer
 * Flow 1 step 3 — the clean case is the boring case, but the ⚓ is the
 * easy/clean-case affordance that signals "this sentence starts on a
 * verse number cleanly").
 */
export function showsAnchorGlyph(focal: Pick<FocalSentence, "starts_at_verse_boundary">): boolean {
  return focal.starts_at_verse_boundary === true;
}
