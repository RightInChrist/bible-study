import { describe, it, expect } from "vitest";

import {
  applyReferenceRules,
  shouldShowProjectionCaveat,
  showsAnchorGlyph,
  type FocalSentence,
  type RefVerse,
} from "../src/lib/rendering";

const cleanFocal: FocalSentence = {
  start_chapter: 5,
  start_verse: 3,
  end_chapter: 5,
  end_verse: 3,
  starts_at_verse_boundary: true,
  ends_at_verse_boundary: true,
};

const startsMidVerseFocal: FocalSentence = {
  start_chapter: 5,
  start_verse: 1,
  end_chapter: 5,
  end_verse: 1,
  starts_at_verse_boundary: false,
  ends_at_verse_boundary: true,
};

const endsMidVerseFocal: FocalSentence = {
  start_chapter: 5,
  start_verse: 1,
  end_chapter: 5,
  end_verse: 2,
  starts_at_verse_boundary: true,
  ends_at_verse_boundary: false,
};

const verses: RefVerse[] = [
  { chapter: 5, verse: 1, text: "When Jesus saw the crowds…" },
  { chapter: 5, verse: 2, text: "And opening His mouth He taught them…" },
];

describe("applyReferenceRules", () => {
  it("R4 clean case — every segment is highlighted", () => {
    const segments = applyReferenceRules(cleanFocal, [verses[0]]);
    expect(segments).toHaveLength(1);
    expect(segments[0].emphasis).toBe("highlight");
  });

  it("R1 mid-verse start — first verse muted", () => {
    const segments = applyReferenceRules(startsMidVerseFocal, [verses[0]]);
    expect(segments).toHaveLength(1);
    expect(segments[0].emphasis).toBe("muted");
  });

  it("R1 mid-verse end — last verse muted, interior highlighted", () => {
    const segments = applyReferenceRules(endsMidVerseFocal, verses);
    expect(segments).toHaveLength(2);
    expect(segments[0].emphasis).toBe("highlight");
    expect(segments[1].emphasis).toBe("muted");
  });

  it("empty verses returns empty segments", () => {
    expect(applyReferenceRules(cleanFocal, [])).toEqual([]);
  });

  it("interior verses in a 3+ verse span are always highlighted", () => {
    const big: RefVerse[] = [
      { chapter: 5, verse: 1, text: "v1" },
      { chapter: 5, verse: 2, text: "v2" },
      { chapter: 5, verse: 3, text: "v3" },
      { chapter: 5, verse: 4, text: "v4" },
    ];
    const focal: FocalSentence = {
      ...endsMidVerseFocal,
      start_verse: 1,
      end_verse: 4,
      starts_at_verse_boundary: false,
      ends_at_verse_boundary: false,
    };
    const segments = applyReferenceRules(focal, big);
    expect(segments[0].emphasis).toBe("muted");
    expect(segments[1].emphasis).toBe("highlight");
    expect(segments[2].emphasis).toBe("highlight");
    expect(segments[3].emphasis).toBe("muted");
  });
});

describe("shouldShowProjectionCaveat", () => {
  it("R4 clean case suppresses the ≈ glyph", () => {
    expect(shouldShowProjectionCaveat(cleanFocal)).toBe(false);
  });
  it("partial-verse start triggers the caveat", () => {
    expect(shouldShowProjectionCaveat(startsMidVerseFocal)).toBe(true);
  });
  it("partial-verse end triggers the caveat", () => {
    expect(shouldShowProjectionCaveat(endsMidVerseFocal)).toBe(true);
  });
});

describe("showsAnchorGlyph", () => {
  it("⚓ when starts_at_verse_boundary is true", () => {
    expect(showsAnchorGlyph({ starts_at_verse_boundary: true })).toBe(true);
  });
  it("no ⚓ when starts_at_verse_boundary is false", () => {
    expect(showsAnchorGlyph({ starts_at_verse_boundary: false })).toBe(false);
  });
});
