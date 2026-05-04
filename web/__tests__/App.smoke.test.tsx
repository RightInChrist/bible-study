/**
 * Smoke test: the App renders the status pill + parallel reader without
 * crashing when the API mocks return a chapter (Matthew 5).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { App } from "../src/App";

function chapterPayload() {
  return {
    chapter: 5,
    sentences: [
      {
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
      },
    ],
  };
}

function parallelPayload() {
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
    text_sblgnt: "Μακάριοι οἱ πτωχοὶ τῷ πνεύματι…",
    byzantine: [{ chapter: 5, verse: 3, text: "Μακάριοι…" }],
    english: [
      { translation: "BSB", verses: [{ chapter: 5, verse: 3, text: "Blessed are the poor…" }] },
      { translation: "BLB", verses: [{ chapter: 5, verse: 3, text: "Blessed are the poor…" }] },
      { translation: "WEB", verses: [{ chapter: 5, verse: 3, text: "Blessed are the poor…" }] },
    ],
    bib_interlinear: [],
    is_red_letter: true,
  };
}

function fixtureStatus() {
  return {
    disk_manifest_hash: "abc123def456",
    db_fixture_version: "abc123def456",
    stale: false,
    last_imported_at: "2026-05-03T00:00:00Z",
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const respond = (body: unknown) =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      if (url.includes("/api/v1/admin/fixture-status")) return respond(fixtureStatus());
      if (url.includes("/api/v1/sentences?chapter=5")) return respond(chapterPayload());
      if (url.includes("/parallel")) return respond(parallelPayload());
      return new Response("{}", { status: 404 });
    }),
  );
});

describe("App smoke", () => {
  it("renders the status pill and parallel reader for Matthew 5", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { container } = (await import("@testing-library/react")).render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/chapter/5"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(container.querySelector(".app-shell")).not.toBeNull();
    await waitFor(() =>
      expect(screen.getByText(/Matthew 5/)).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText(/fix abc123def456/)).toBeInTheDocument(),
    );
  });
});
