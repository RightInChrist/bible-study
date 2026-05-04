import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen } from "@testing-library/react";

import { SentenceRow } from "../src/components/SentenceRow";
import type { SentenceListItem, SentenceParallelResponse } from "../src/api/types";
import { renderWithProviders } from "./test-utils";

function listItem(overrides: Partial<SentenceListItem> = {}): SentenceListItem {
  return {
    sentence_id: "mat-5-3",
    chapter: 5,
    ordinal_in_chapter: 3,
    start_verse: 2,
    end_verse: 2,
    starts_at_verse_boundary: true,
    ends_at_verse_boundary: true,
    text_preview: "Μακάριοι οἱ πτωχοὶ τῷ πνεύματι…",
    word_count: 6,
    is_red_letter: true,
    ...overrides,
  };
}

function parallel(overrides: Partial<SentenceParallelResponse> = {}): SentenceParallelResponse {
  return {
    sentence_id: "mat-5-3",
    chapter: 5,
    ordinal_in_chapter: 3,
    start_chapter: 5,
    start_verse: 2,
    end_chapter: 5,
    end_verse: 2,
    starts_at_verse_boundary: true,
    ends_at_verse_boundary: true,
    word_count: 6,
    text_sblgnt: "Μακάριοι οἱ πτωχοὶ τῷ πνεύματι, ὅτι αὐτῶν ἐστιν ἡ βασιλεία τῶν οὐρανῶν.",
    byzantine: [{ chapter: 5, verse: 3, text: "Μακάριοι οἱ πτωχοὶ τῷ πνεύματι…" }],
    english: [
      { translation: "BSB", verses: [{ chapter: 5, verse: 3, text: "Blessed are the poor in spirit…" }] },
      { translation: "BLB", verses: [{ chapter: 5, verse: 3, text: "Blessed are the poor in the spirit…" }] },
      { translation: "WEB", verses: [{ chapter: 5, verse: 3, text: "Blessed are the poor in spirit…" }] },
    ],
    bib_interlinear: [],
    is_red_letter: true,
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/parallel")) {
      return new Response(JSON.stringify(parallel()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

describe("SentenceRow", () => {
  it("renders a left-edge red rule for red-letter sentences", () => {
    renderWithProviders(
      <SentenceRow sentence={listItem({ is_red_letter: true })} focused={false} onFocus={() => undefined} />,
    );
    const row = screen.getByRole("article");
    expect(row.getAttribute("data-red-letter")).toBe("true");
  });

  it("does not render the red rule for non-red-letter sentences", () => {
    renderWithProviders(
      <SentenceRow
        sentence={listItem({ sentence_id: "mat-5-1", is_red_letter: false })}
        focused={false}
        onFocus={() => undefined}
      />,
    );
    const row = screen.getByRole("article");
    expect(row.getAttribute("data-red-letter")).toBe("false");
  });

  it("shows the ⚓ glyph when starts_at_verse_boundary is true", () => {
    renderWithProviders(
      <SentenceRow
        sentence={listItem({ starts_at_verse_boundary: true })}
        focused={false}
        onFocus={() => undefined}
      />,
    );
    expect(screen.getByLabelText("starts at verse boundary")).toBeInTheDocument();
  });

  it("hides the ⚓ glyph when starts_at_verse_boundary is false", () => {
    renderWithProviders(
      <SentenceRow
        sentence={listItem({
          sentence_id: "mat-5-2",
          starts_at_verse_boundary: false,
          start_verse: 1,
          end_verse: 1,
        })}
        focused={false}
        onFocus={() => undefined}
      />,
    );
    expect(screen.queryByLabelText("starts at verse boundary")).toBeNull();
  });
});
