import { describe, it, expect } from "vitest";

import { showsDivergenceGlyph } from "../src/lib/divergence";
import type { SentenceListItem } from "../src/api/types";

function s(overrides: Partial<SentenceListItem>): SentenceListItem {
  return {
    sentence_id: "mat-5-1",
    chapter: 5,
    ordinal_in_chapter: 1,
    start_verse: 1,
    end_verse: 1,
    starts_at_verse_boundary: true,
    ends_at_verse_boundary: true,
    text_preview: "",
    word_count: 5,
    is_red_letter: false,
    ...overrides,
  };
}

describe("showsDivergenceGlyph", () => {
  it("fires when two adjacent sentences share a verse", () => {
    const upper = s({ start_verse: 1, end_verse: 1, ends_at_verse_boundary: false });
    const lower = s({
      sentence_id: "mat-5-2",
      ordinal_in_chapter: 2,
      start_verse: 1,
      end_verse: 1,
      starts_at_verse_boundary: false,
    });
    expect(showsDivergenceGlyph(upper, lower)).toBe(true);
  });

  it("fires when upper ends mid-verse and lower starts the next verse", () => {
    const upper = s({ start_verse: 1, end_verse: 1, ends_at_verse_boundary: false });
    const lower = s({
      sentence_id: "mat-5-2",
      ordinal_in_chapter: 2,
      start_verse: 2,
      end_verse: 2,
      starts_at_verse_boundary: true,
    });
    expect(showsDivergenceGlyph(upper, lower)).toBe(true);
  });

  it("does not fire on a clean verse-boundary transition", () => {
    const upper = s({ start_verse: 1, end_verse: 1 });
    const lower = s({
      sentence_id: "mat-5-2",
      ordinal_in_chapter: 2,
      start_verse: 2,
      end_verse: 2,
    });
    expect(showsDivergenceGlyph(upper, lower)).toBe(false);
  });

  it("does not fire across chapter boundaries (chapter header takes over)", () => {
    const upper = s({ chapter: 5, start_verse: 48, end_verse: 48 });
    const lower = s({
      sentence_id: "mat-6-1",
      chapter: 6,
      ordinal_in_chapter: 1,
      start_verse: 1,
      end_verse: 1,
    });
    expect(showsDivergenceGlyph(upper, lower)).toBe(false);
  });
});
