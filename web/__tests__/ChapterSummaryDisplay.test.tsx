/**
 * ChapterSummaryDisplay rendering tests (Slice 8).
 *
 * Asserts graceful handling of sparse / missing structured fields —
 * e.g. an unparseable raw text resolves to an empty structured object,
 * and the page should not blow up.
 */
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";

import { ChapterSummaryDisplay } from "../src/components/chapter/ChapterSummaryDisplay";
import type { ChapterSummaryResponse } from "../src/api/types";

const baseSummary: ChapterSummaryResponse = {
  summary_id: 1,
  chapter: 5,
  prompt_version: "chapter-summary-v1",
  source_set_id: "CHAPTER_BUNDLE",
  model: "claude-opus-4-7+xhigh",
  source_snapshot_hash: "deadbeef",
  summary: {},
  raw_summary_text: "",
  generated_at: "2026-05-08T12:00:00Z",
  run_id: "run-1",
  candidate_ids_consulted: [],
  hidden_bool: false,
};

function renderInRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("ChapterSummaryDisplay", () => {
  it("does not crash on empty structured payload", () => {
    renderInRouter(<ChapterSummaryDisplay summary={baseSummary} />);
    // No labelled sections should render.
    expect(screen.queryByTestId("chapter-summary__overview")).toBeNull();
    expect(screen.queryByTestId("chapter-summary__narrative-arc")).toBeNull();
    expect(screen.queryByTestId("chapter-summary__intertexts")).toBeNull();
    // Provenance footer is always present.
    expect(screen.getByTestId("chapter-summary__provenance")).toBeInTheDocument();
  });

  it("renders only present fields and hides empty key_intertexts", () => {
    renderInRouter(
      <ChapterSummaryDisplay
        summary={{
          ...baseSummary,
          summary: {
            summary: "A short overview.",
            narrative_arc: "An arc.",
            key_intertexts: [],
            key_sentences: [],
            open_questions: null,
          },
        }}
      />,
    );
    expect(screen.getByTestId("chapter-summary__overview")).toHaveTextContent(
      /A short overview/,
    );
    expect(screen.getByTestId("chapter-summary__narrative-arc")).toHaveTextContent(
      /An arc/,
    );
    expect(screen.queryByTestId("chapter-summary__intertexts")).toBeNull();
    expect(screen.queryByTestId("chapter-summary__key-sentences")).toBeNull();
    expect(screen.queryByTestId("chapter-summary__open-questions")).toBeNull();
  });

  it("renders consulted candidate ids count", () => {
    renderInRouter(
      <ChapterSummaryDisplay
        summary={{ ...baseSummary, candidate_ids_consulted: [10, 20, 30] }}
      />,
    );
    expect(
      screen.getByTestId("chapter-summary__provenance"),
    ).toHaveTextContent(/candidates consulted \(3\)/);
    expect(
      screen.getByTestId("chapter-summary__provenance"),
    ).toHaveTextContent(/10, 20, 30/);
  });

  it("renders sentence_id key_sentences as Links", () => {
    renderInRouter(
      <ChapterSummaryDisplay
        summary={{
          ...baseSummary,
          summary: {
            key_sentences: [
              {
                sentence_id: "mat-5-4",
                verse_range: "5:3",
                why_pivotal: "Inaugurates the beatitudes.",
              },
            ],
          },
        }}
      />,
    );
    const link = screen.getByRole("link", { name: /5:3/ });
    expect(link).toHaveAttribute("href", "/sentence/mat-5-4");
  });
});
