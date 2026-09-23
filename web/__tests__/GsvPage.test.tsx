/**
 * GsvPage smoke tests (Slice 5).
 *
 * Mocks the chapter + coverage endpoints and asserts the coverage
 * indicator, format toggle, and body all show up.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type {
  GsvChapterResponse,
  GsvCoverageResponse,
} from "../src/api/types";
import { GsvPage } from "../src/routes/GsvPage";
import { renderWithProviders } from "./test-utils";

function fakeChapterJson(): GsvChapterResponse {
  return {
    chapter: 5,
    coverage: {
      chapter: 5,
      total_sentences: 71,
      total_red_letter_sentences: 68,
      ranked_red_letter_sentences: 1,
      unresolved_ties: 0,
    },
    sentences: [
      {
        sentence_id: "mat-5-1",
        chapter: 5,
        ordinal_in_chapter: 1,
        verse_range: "5:1",
        is_red_letter: false,
        text: "When He saw the crowds, He went up on the mountain.",
        provenance: { kind: "translation", name: "BSB", verse_range: "5:1" },
      },
      {
        sentence_id: "mat-5-4",
        chapter: 5,
        ordinal_in_chapter: 4,
        verse_range: "5:3",
        is_red_letter: true,
        text: "Blessed are the poor in spirit, for theirs is the kingdom of heaven.",
        provenance: {
          kind: "claude",
          candidate_id: 99,
          style_prompt_version: "first-century-jewish-v1",
          source_set_id: "BOTH_GREEK",
          model: "claude-opus-4-7+xhigh",
          generated_at: "2026-05-05T12:57:32Z",
        },
      },
    ],
  };
}

function fakeCoverage(): GsvCoverageResponse {
  return {
    ranked_sentences: 1,
    total_red_letter_sentences: 164,
    ch5_ranked: 1,
    ch5_total: 68,
    per_chapter: [
      {
        chapter: 5,
        total_sentences: 71,
        total_red_letter_sentences: 68,
        ranked_red_letter_sentences: 1,
        unresolved_ties: 0,
      },
    ],
  };
}

interface MockOpts {
  chapterError?: { status: number; body: object };
  textBody?: string;
  markdownBody?: string;
}

function mountFetch(opts: MockOpts = {}): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/gsv/coverage")) {
      return new Response(JSON.stringify(fakeCoverage()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/api/v1/gsv/5") && url.includes("format=json")) {
      return new Response(JSON.stringify(fakeChapterJson()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/api/v1/gsv/5") && url.includes("format=text")) {
      if (opts.chapterError) {
        return new Response(JSON.stringify(opts.chapterError.body), {
          status: opts.chapterError.status,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(opts.textBody ?? "Matthew 5 — GSV plain text", {
        status: 200,
        headers: { "content-type": "text/plain" },
      });
    }
    if (url.includes("/api/v1/gsv/5") && url.includes("format=markdown")) {
      return new Response(
        opts.markdownBody ?? "# Matthew 5\n\n**5:1** test paragraph[^mat-5-1]\n\n[^mat-5-1]: BSB / 5:1\n",
        {
          status: 200,
          headers: { "content-type": "text/markdown" },
        },
      );
    }
    if (url.includes("/admin/fixture-status")) {
      return new Response(
        JSON.stringify({
          disk_manifest_hash: "h",
          db_fixture_version: "h",
          stale: false,
          last_imported_at: null,
        }),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GsvPage", () => {
  it("renders coverage block, format toggle, and body for chapter 5", async () => {
    mountFetch();
    renderWithProviders(
      <Routes>
        <Route path="/gsv/:chapter" element={<GsvPage />} />
      </Routes>,
      { initialEntries: ["/gsv/5"] },
    );
    expect(await screen.findByTestId("gsv-page")).toBeInTheDocument();
    expect(await screen.findByTestId("gsv-coverage")).toHaveTextContent(/Matthew 5/);
    expect(await screen.findByTestId("gsv-coverage")).toHaveTextContent(/1 \/ 68/);
    expect(await screen.findByTestId("gsv-format-toggle")).toBeInTheDocument();
    // Default format is markdown
    expect(await screen.findByTestId("gsv-body-markdown")).toBeInTheDocument();
  });

  it("renders the unresolved-ties error block on a 409", async () => {
    mountFetch({
      chapterError: {
        status: 409,
        body: {
          code: "unresolved_ties",
          message: "ties block plain-text export",
          details: {
            chapter: 5,
            offending_sentences: ["mat-5-4"],
          },
        },
      },
    });
    renderWithProviders(
      <Routes>
        <Route path="/gsv/:chapter" element={<GsvPage />} />
      </Routes>,
      { initialEntries: ["/gsv/5"] },
    );
    // Click plain text format to trigger the error
    const textBtn = await screen.findByTestId("gsv-format-toggle__text");
    await userEvent.click(textBtn);
    await waitFor(() => {
      expect(screen.getByTestId("gsv-ties-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gsv-ties-error")).toHaveTextContent(/mat-5-4/);
  });

  it("download button links to the format-specific download URL", async () => {
    mountFetch();
    renderWithProviders(
      <Routes>
        <Route path="/gsv/:chapter" element={<GsvPage />} />
      </Routes>,
      { initialEntries: ["/gsv/5"] },
    );
    const link = await screen.findByTestId("gsv-download");
    expect(link).toHaveAttribute(
      "href",
      "/api/v1/gsv/5?format=markdown&download=true",
    );
  });
});
