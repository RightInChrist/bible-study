/**
 * Orphan resolution page tests.
 *
 *   - Renders each orphan kind with disposition selectors.
 *   - Disposition selector toggles the value.
 *   - Apply submits the right body to /admin/reimport with force=true
 *     and the dispositions map.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { OrphanResolutionPage } from "../src/routes/OrphanResolutionPage";
import type { OrphanSummary } from "../src/api/types";

function buildSummary(): OrphanSummary {
  return {
    affected_candidates: [
      {
        candidate_id: 42,
        sentence_id_old: "mat-1-1",
        sentence_id_new: null,
        reason: "sentence_text_changed",
        style_prompt_version: "literal-v1",
        source_set_id: "SBLGNT_ONLY",
        model: "claude-opus-4-7+xhigh",
        generated_at: "2026-05-01T00:00:00Z",
        candidate_text_excerpt: "candidate text excerpt here",
        old_sentence_text_excerpt: "old text",
        new_sentence_text_excerpt: null,
        suggested_remap_sentence_id: null,
      },
    ],
    affected_rankings: [
      {
        sentence_id_old: "mat-1-2",
        sentence_id_new: null,
        reason: "sentence_removed",
        ranked_candidate_count: 3,
        has_notes: true,
        has_tie_break: false,
        version: 5,
        suggested_remap_sentence_id: null,
      },
    ],
    affected_overlays: [],
    total: 2,
  };
}

function renderPage(summary: OrphanSummary | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          summary !== null
            ? { pathname: "/admin/orphans", state: { orphanSummary: summary } }
            : "/admin/orphans",
        ]}
      >
        <Routes>
          <Route path="/admin/orphans" element={<OrphanResolutionPage />} />
          <Route path="/chapter/1" element={<div data-testid="chapter-1">chapter</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("OrphanResolutionPage", () => {
  it("renders empty state when no summary is in router state", () => {
    renderPage(null);
    expect(screen.getByTestId("orphan-page-empty")).toBeInTheDocument();
  });

  it("renders one row per orphan with a disposition selector", () => {
    renderPage(buildSummary());
    expect(screen.getByTestId("orphan-page")).toBeInTheDocument();
    expect(screen.getByTestId("orphan-candidates")).toBeInTheDocument();
    expect(screen.getByTestId("orphan-rankings")).toBeInTheDocument();
    expect(screen.getByTestId("disposition-candidate:42")).toHaveValue("keep");
    expect(screen.getByTestId("disposition-ranking:mat-1-2")).toHaveValue("keep");
  });

  it("toggles disposition and submits with force=true on Apply", async () => {
    let captured: { force?: boolean; dispositions?: Record<string, string> } = {};
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      captured = body;
      return new Response(
        JSON.stringify({
          fixture_version: "ok",
          files_imported: 7,
          sentences_built: 1,
          words_built: 1,
          byzantine_verses: 1,
          english_verses: 1,
          bib_interlinear_words: 1,
          red_letter_source_ranges: 2,
          style_prompts: 4,
          elapsed_ms: 1,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage(buildSummary());
    await userEvent.selectOptions(
      screen.getByTestId("disposition-candidate:42"),
      "delete",
    );
    expect(screen.getByTestId("disposition-candidate:42")).toHaveValue("delete");
    await userEvent.click(screen.getByTestId("orphan-apply"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(captured.force).toBe(true);
    expect(captured.dispositions).toEqual({
      "candidate:42": "delete",
      "ranking:mat-1-2": "keep",
    });
  });
});
