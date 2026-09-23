/**
 * ChapterSummaryPage rendering smoke (Slice 8).
 *
 * Asserts the page renders the latest summary list and the structured
 * fields surface as labelled sections. Empty-state path also covered.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Routes, Route } from "react-router-dom";

import { ChapterSummaryPage } from "../src/routes/ChapterSummaryPage";
import { renderWithProviders } from "./test-utils";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const STRUCTURED = {
  summary: "The Sermon on the Mount opens with honor pronounced over the marginal.",
  narrative_arc: "Jesus ascends a mountain — Sinai-shaped — and pronounces blessings.",
  audience_dynamics: "Crowds gathered (5:1), disciples come close (5:2).",
  cultural_throughline:
    "Honor/shame, contested halakha, kingdom-of-heaven inversion.",
  rhetorical_strategy: "Programmatic teaching delivered authoritatively.",
  pragmatic_arc: "Blessings → antitheses → warnings.",
  key_intertexts: [
    {
      reference: "Exod 19–20",
      type: "recapitulation",
      note: "Sinai-shaped delivery from a mountain.",
    },
  ],
  key_sentences: [
    {
      sentence_id: "mat-5-4",
      verse_range: "5:3",
      why_pivotal: "Inaugurates the beatitudes.",
    },
  ],
  open_questions: "Audience — disciples-only or crowd-inclusive?",
  candidate_ids_consulted: [42, 43],
};

const SUMMARY_RESPONSE = {
  summary_id: 7,
  chapter: 5,
  prompt_version: "chapter-summary-v1",
  source_set_id: "CHAPTER_BUNDLE",
  model: "claude-opus-4-7+xhigh",
  source_snapshot_hash: "deadbeef",
  summary: STRUCTURED,
  raw_summary_text: JSON.stringify(STRUCTURED),
  generated_at: "2026-05-08T13:00:00Z",
  run_id: "run-abc",
  candidate_ids_consulted: [42, 43],
  hidden_bool: false,
};

describe("ChapterSummaryPage", () => {
  it("renders empty state when no summaries exist", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ chapter: 5, summaries: [] }), { status: 200 }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/chapter/:chapter/summary" element={<ChapterSummaryPage />} />
      </Routes>,
      { initialEntries: ["/chapter/5/summary"] },
    );

    expect(
      await screen.findByTestId("chapter-summary-page__empty"),
    ).toHaveTextContent(/No chapter summary yet/);
  });

  it("renders the latest summary with all structured sections", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ chapter: 5, summaries: [SUMMARY_RESPONSE] }),
        { status: 200 },
      ),
    );

    renderWithProviders(
      <Routes>
        <Route path="/chapter/:chapter/summary" element={<ChapterSummaryPage />} />
      </Routes>,
      { initialEntries: ["/chapter/5/summary"] },
    );

    await waitFor(() => {
      expect(screen.getByTestId("chapter-summary")).toBeInTheDocument();
    });
    expect(screen.getByTestId("chapter-summary__overview")).toHaveTextContent(
      /Sermon on the Mount/,
    );
    expect(screen.getByTestId("chapter-summary__narrative-arc")).toHaveTextContent(
      /Sinai-shaped/,
    );
    expect(
      screen.getByTestId("chapter-summary__audience-dynamics"),
    ).toHaveTextContent(/disciples come close/);
    expect(
      screen.getByTestId("chapter-summary__cultural-throughline"),
    ).toHaveTextContent(/Honor\/shame/);
    expect(
      screen.getByTestId("chapter-summary__rhetorical-strategy"),
    ).toHaveTextContent(/Programmatic teaching/);
    expect(screen.getByTestId("chapter-summary__pragmatic-arc")).toHaveTextContent(
      /Blessings → antitheses/,
    );
    expect(screen.getByTestId("chapter-summary__intertexts")).toHaveTextContent(
      /Exod 19–20/,
    );
    expect(
      screen.getByTestId("chapter-summary__key-sentences"),
    ).toHaveTextContent(/Inaugurates the beatitudes/);
    expect(
      screen.getByTestId("chapter-summary__open-questions"),
    ).toHaveTextContent(/disciples-only or crowd-inclusive/);
    expect(
      screen.getByTestId("chapter-summary__provenance"),
    ).toHaveTextContent(/claude-opus-4-7\+xhigh/);
  });
});
