/**
 * SBLGNT vs Byzantine boundary divergence heuristic (SPEC.md §Edge cases).
 *
 * Byzantine ships verse-keyed only in v1; this heuristic detects the
 * cases where the parallel reader should surface the `↔` glyph between
 * two adjacent rows:
 *
 *  - Two adjacent SBLGNT sentences share at least one verse (their verse
 *    ranges overlap on the same verse number).
 *  - An SBLGNT sentence ends mid-verse (`ends_at_verse_boundary=false`)
 *    where the Byzantine verse text continues uninterrupted into the next
 *    SBLGNT sentence.
 *
 * Pure function so it's testable in isolation; the parallel reader maps
 * over adjacent pairs.
 */
import type { SentenceListItem } from "../api/types";

export function showsDivergenceGlyph(
  upper: SentenceListItem,
  lower: SentenceListItem,
): boolean {
  if (upper.chapter !== lower.chapter) {
    // SPEC.md treats cross-chapter cases as their own visual concern (see
    // SPEC §Edge cases — "Sentence spans multiple verses across a chapter
    // boundary"); the `↔` glyph is for in-chapter Byzantine mismatches.
    return false;
  }
  // Verse ranges overlap on the same verse number.
  const sharesVerse = upper.end_verse >= lower.start_verse && upper.start_verse <= lower.end_verse;
  if (sharesVerse) {
    return true;
  }
  // Upper ends mid-verse and lower starts in a verse adjacent or
  // following — Byzantine continues uninterrupted across that boundary.
  if (
    upper.ends_at_verse_boundary === false &&
    lower.start_verse <= upper.end_verse + 1
  ) {
    return true;
  }
  return false;
}
