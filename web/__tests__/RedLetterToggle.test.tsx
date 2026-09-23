import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { RedLetterToggle } from "../src/components/RedLetterToggle";
import type {
  ChapterOverlaysResponse,
  MarkRedLetterResponse,
  Overlay,
  SentenceListItem,
  SentenceParallelResponse,
  UnmarkRedLetterResponse,
} from "../src/api/types";
import { renderWithProviders } from "./test-utils";

function listItem(overrides: Partial<SentenceListItem> = {}): SentenceListItem {
  return {
    sentence_id: "mat-8-26-1",
    chapter: 8,
    ordinal_in_chapter: 5,
    start_verse: 26,
    end_verse: 26,
    starts_at_verse_boundary: true,
    ends_at_verse_boundary: true,
    text_preview: "ὀλιγόπιστοι …",
    word_count: 6,
    is_red_letter: false,
    ...overrides,
  };
}

function parallelResponse(
  overrides: Partial<SentenceParallelResponse> = {},
): SentenceParallelResponse {
  return {
    sentence_id: "mat-8-26-1",
    chapter: 8,
    ordinal_in_chapter: 5,
    start_chapter: 8,
    start_verse: 26,
    end_chapter: 8,
    end_verse: 26,
    starts_at_verse_boundary: true,
    ends_at_verse_boundary: true,
    word_count: 6,
    text_sblgnt: "Τί δειλοί ἐστε, ὀλιγόπιστοι;",
    byzantine: [],
    english: [],
    bib_interlinear: [],
    is_red_letter: true,
    red_letter_provenance: {
      origin: "manual",
      head_overlay_id: 7,
    },
    ...overrides,
  };
}

function manualHead(overrides: Partial<Overlay> = {}): Overlay {
  return {
    overlay_id: 7,
    operation: "create",
    start_sentence_id: "mat-8-26-1",
    start_word_offset: 1,
    end_sentence_id: "mat-8-26-1",
    end_word_offset: 4,
    rejected: false,
    origin: "manual",
    created_at: "2026-05-08T10:00:00Z",
    version: 3,
    ...overrides,
  };
}

let posted: { url: string; body: unknown; headers: Headers }[] = [];

beforeEach(() => {
  posted = [];
  vi.restoreAllMocks();
});

function installFetchMock(opts: {
  parallel?: SentenceParallelResponse;
  overlays?: ChapterOverlaysResponse;
  markResponse?: MarkRedLetterResponse;
  unmarkResponse?: UnmarkRedLetterResponse;
}): void {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/parallel") && method === "GET") {
      return new Response(JSON.stringify(opts.parallel ?? parallelResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/red-letter/chapter/") && method === "GET") {
      const fallback: ChapterOverlaysResponse = {
        chapter: 8,
        chains: [{ head: manualHead(), history: [] }],
        effective_sentence_ids: ["mat-8-26-1"],
      };
      return new Response(JSON.stringify(opts.overlays ?? fallback), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/red-letter/mark") && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      posted.push({ url, body, headers: new Headers(init?.headers) });
      const fallback: MarkRedLetterResponse = {
        overlay: manualHead({ overlay_id: 9, version: 1 }),
        affected_sentence_ids: ["mat-8-26-1"],
      };
      return new Response(
        JSON.stringify(opts.markResponse ?? fallback),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (url.includes("/red-letter/unmark") && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      posted.push({ url, body, headers: new Headers(init?.headers) });
      const fallback: UnmarkRedLetterResponse = {
        overlay: manualHead({
          overlay_id: 11,
          parent_overlay_id: 7,
          operation: "reject",
          rejected: true,
          start_sentence_id: null,
          start_word_offset: null,
          end_sentence_id: null,
          end_word_offset: null,
          version: 4,
        }),
        affected_sentence_ids: [],
      };
      return new Response(
        JSON.stringify(opts.unmarkResponse ?? fallback),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
}

describe("RedLetterToggle", () => {
  it("shows Mark button when sentence is not red-letter", () => {
    installFetchMock({});
    renderWithProviders(<RedLetterToggle sentence={listItem()} />);
    expect(screen.getByTestId("mark-red-letter-button")).toBeInTheDocument();
    expect(screen.queryByTestId("unmark-red-letter-button")).toBeNull();
  });

  it("shows Unmark button when sentence is red-letter", () => {
    installFetchMock({});
    renderWithProviders(
      <RedLetterToggle sentence={listItem({ is_red_letter: true })} />,
    );
    expect(screen.getByTestId("unmark-red-letter-button")).toBeInTheDocument();
    expect(screen.queryByTestId("mark-red-letter-button")).toBeNull();
  });

  it("submits the mark form with the sentence's verse range", async () => {
    installFetchMock({});
    renderWithProviders(<RedLetterToggle sentence={listItem()} />);
    fireEvent.click(screen.getByTestId("mark-red-letter-button"));
    fireEvent.click(screen.getByTestId("mark-submit"));
    await waitFor(() => {
      const call = posted.find((p) => p.url.includes("/mark"));
      expect(call).toBeDefined();
      expect(call?.body).toMatchObject({
        chapter: 8,
        start_verse: 26,
        end_verse: 26,
      });
    });
  });

  it("sends If-Match header with head version on unmark confirm", async () => {
    installFetchMock({});
    renderWithProviders(
      <RedLetterToggle sentence={listItem({ is_red_letter: true })} />,
    );
    // Wait for parallel + chapter overlays to load before clicking —
    // the Unmark button is disabled while parallel is loading.
    const unmarkBtn = await screen.findByTestId("unmark-red-letter-button");
    await waitFor(() => {
      expect(unmarkBtn).not.toBeDisabled();
    });
    fireEvent.click(unmarkBtn);
    const confirmBtn = await screen.findByTestId("unmark-confirm-button");
    await waitFor(() => {
      expect(confirmBtn).not.toBeDisabled();
    });
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      const call = posted.find((p) => p.url.includes("/unmark"));
      expect(call).toBeDefined();
      expect(call?.body).toMatchObject({
        scope: "manual_overlay",
        target_id: 7,
      });
      expect(call?.headers.get("If-Match")).toBe("3");
    });
  });
});
