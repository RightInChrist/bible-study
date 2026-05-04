import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { TranslationCell } from "../src/components/TranslationCell";
import type { FocalSentence } from "../src/lib/rendering";

const startsMidVerseFocal: FocalSentence = {
  start_chapter: 5,
  start_verse: 1,
  end_chapter: 5,
  end_verse: 1,
  starts_at_verse_boundary: false,
  ends_at_verse_boundary: true,
};

const cleanFocal: FocalSentence = {
  start_chapter: 5,
  start_verse: 3,
  end_chapter: 5,
  end_verse: 3,
  starts_at_verse_boundary: true,
  ends_at_verse_boundary: true,
};

describe("TranslationCell — R1 partial-verse highlighting", () => {
  it("renders a muted segment when starts_at_verse_boundary is false", () => {
    render(
      <TranslationCell
        label="BSB"
        focal={startsMidVerseFocal}
        verses={[{ chapter: 5, verse: 1, text: "When Jesus saw the crowds…" }]}
        caveat="R3-english"
      />,
    );
    const segments = screen.getAllByText(/When Jesus/);
    const muted = segments.find((el) => el.closest('[data-emphasis="muted"]'));
    expect(muted).toBeDefined();
  });

  it("shows the projection caveat ≈ glyph in partial-verse cases", () => {
    render(
      <TranslationCell
        label="Byzantine"
        focal={startsMidVerseFocal}
        verses={[{ chapter: 5, verse: 1, text: "verse text" }]}
        caveat="R2-byzantine"
      />,
    );
    expect(screen.getByText("≈")).toBeInTheDocument();
  });

  it("R4 clean case — every segment highlighted, no ≈ glyph", () => {
    render(
      <TranslationCell
        label="BSB"
        focal={cleanFocal}
        verses={[{ chapter: 5, verse: 3, text: "Blessed are the poor in spirit…" }]}
        caveat="R3-english"
      />,
    );
    expect(screen.queryByText("≈")).toBeNull();
    const segment = screen.getByText(/Blessed are the poor/);
    const wrapper = segment.closest('.segment');
    expect(wrapper?.getAttribute("data-emphasis")).toBe("highlight");
  });
});
