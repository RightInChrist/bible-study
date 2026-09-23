/**
 * RankPage smoke tests (Slice 4).
 *
 * Loads a rank page with mocked endpoints and asserts the rank surface
 * comes up with the reference strip, candidate list, notes field, and
 * save bar all visible.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type {
  RankingResponse,
  SentenceListResponse,
  SentenceParallelResponse,
} from "../src/api/types";
import { RankPage } from "../src/routes/RankPage";
import { renderWithProviders } from "./test-utils";

function fakeParallel(): SentenceParallelResponse {
  return {
    sentence_id: "mat-5-3",
    chapter: 5,
    ordinal_in_chapter: 3,
    start_chapter: 5,
    start_verse: 3,
    end_chapter: 5,
    end_verse: 3,
    starts_at_verse_boundary: true,
    ends_at_verse_boundary: true,
    word_count: 12,
    text_sblgnt: "Μακάριοι οἱ πτωχοὶ τῷ πνεύματι …",
    byzantine: [
      { chapter: 5, verse: 3, text: "Μακαριοι οι πτωχοι τω πνευματι …" },
    ],
    english: [],
    bib_interlinear: [],
    is_red_letter: true,
  };
}

function fakeRanking(overrides: Partial<RankingResponse> = {}): RankingResponse {
  return {
    sentence_id: "mat-5-3",
    version: 0,
    notes: null,
    entries: [],
    available_candidates: [
      { kind: "translation", name: "SBLGNT", verse_range: "5:3", text: "Greek text" },
      {
        kind: "translation",
        name: "BSB",
        verse_range: "5:3",
        text: "Blessed are the poor in spirit, for theirs is the kingdom of heaven.",
      },
      {
        kind: "translation",
        name: "BLB",
        verse_range: "5:3",
        text: "Blessed are the poor in the spirit …",
      },
      {
        kind: "claude",
        candidate: {
          candidate_id: 42,
          sentence_id: "mat-5-3",
          style_prompt_version: "literal-v1",
          source_set_id: "BOTH_GREEK",
          model: "claude-opus-4-7+xhigh",
          generated_at: "2026-05-05T00:00:00Z",
          candidate_text: "The poor in spirit are happy …",
          source_snapshot_hash: "h",
          hidden_bool: false,
        },
      },
    ],
    hidden_combos: [],
    ...overrides,
  };
}

function fakeChapterList(): SentenceListResponse {
  return {
    chapter: 5,
    sentences: [
      {
        sentence_id: "mat-5-2",
        chapter: 5,
        ordinal_in_chapter: 2,
        start_verse: 2,
        end_verse: 2,
        starts_at_verse_boundary: true,
        ends_at_verse_boundary: true,
        text_preview: "καὶ ἀνοίξας τὸ στόμα …",
        word_count: 5,
        is_red_letter: false,
      },
      {
        sentence_id: "mat-5-3",
        chapter: 5,
        ordinal_in_chapter: 3,
        start_verse: 3,
        end_verse: 3,
        starts_at_verse_boundary: true,
        ends_at_verse_boundary: true,
        text_preview: "Μακάριοι οἱ πτωχοὶ …",
        word_count: 12,
        is_red_letter: true,
      },
      {
        sentence_id: "mat-5-4",
        chapter: 5,
        ordinal_in_chapter: 4,
        start_verse: 4,
        end_verse: 4,
        starts_at_verse_boundary: true,
        ends_at_verse_boundary: true,
        text_preview: "Μακάριοι οἱ πενθοῦντες …",
        word_count: 6,
        is_red_letter: true,
      },
    ],
  };
}

interface MockOpts {
  ranking?: Partial<RankingResponse>;
  putHandler?: (
    body: unknown,
    headers: Headers,
  ) => { status: number; json: object };
}

function mountFetch(opts: MockOpts = {}): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/parallel")) {
        return new Response(JSON.stringify(fakeParallel()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/ranking") && method === "GET") {
        return new Response(JSON.stringify(fakeRanking(opts.ranking ?? {})), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/ranking") && method === "PUT" && opts.putHandler) {
        const headers = new Headers(init?.headers);
        const body = init?.body ? JSON.parse(init.body as string) : null;
        const result = opts.putHandler(body, headers);
        return new Response(JSON.stringify(result.json), {
          status: result.status,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/sentences?chapter=")) {
        return new Response(JSON.stringify(fakeChapterList()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
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
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RankPage", () => {
  it("renders reference strip + rank list + notes + save bar", async () => {
    mountFetch();
    renderWithProviders(
      <Routes>
        <Route path="/sentence/:id/rank" element={<RankPage />} />
      </Routes>,
      { initialEntries: ["/sentence/mat-5-3/rank"] },
    );
    expect(await screen.findByTestId("rank-reference")).toBeInTheDocument();
    expect(await screen.findByTestId("rank-list")).toBeInTheDocument();
    expect(await screen.findByTestId("rank-notes")).toBeInTheDocument();
    expect(await screen.findByTestId("rank-savebar")).toBeInTheDocument();
    // All four available candidates seeded into the draft.
    const cards = await screen.findAllByTestId("rank-card");
    expect(cards.length).toBeGreaterThanOrEqual(4);
  });

  it("sends If-Match header on save", async () => {
    const putHandler = vi.fn((body: unknown, headers: Headers) => {
      const ifMatch = headers.get("If-Match");
      return {
        status: 200,
        json: {
          sentence_id: "mat-5-3",
          version: Number.parseInt(ifMatch ?? "0", 10) + 1,
          notes: (body as { notes: string | null }).notes,
          entries: (body as { entries: unknown[] }).entries,
          available_candidates: fakeRanking().available_candidates,
          hidden_combos: [],
        },
      };
    });
    const fetchMock = mountFetch({ putHandler });
    renderWithProviders(
      <Routes>
        <Route path="/sentence/:id/rank" element={<RankPage />} />
      </Routes>,
      { initialEntries: ["/sentence/mat-5-3/rank"] },
    );
    await screen.findByTestId("rank-list");
    // Toggle a tie so the draft becomes dirty.
    const tieButtons = await screen.findAllByTestId("rank-card__tie");
    await userEvent.click(tieButtons[1]);
    const saveBtn = await screen.findByTestId("rank-savebar__save");
    await userEvent.click(saveBtn);
    await waitFor(() => {
      expect(putHandler).toHaveBeenCalled();
    });
    // Find the PUT call to verify If-Match: 0 (initial version).
    const putCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(putCall).toBeDefined();
    const [, init] = putCall as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("If-Match")).toBe("0");
  });

  it("opens the conflict modal on a 409 response", async () => {
    const putHandler = () => ({
      status: 409,
      json: {
        code: "stale_version",
        message: "stale",
        details: {
          current_version: 7,
          current_notes: "from another tab",
          current_entries: [
            {
              position: 1,
              rank: 1,
              tied_with_above: false,
              candidate_ref: { kind: "translation", name: "WEB", verse_range: "5:3" },
            },
          ],
        },
      },
    });
    mountFetch({ putHandler });
    renderWithProviders(
      <Routes>
        <Route path="/sentence/:id/rank" element={<RankPage />} />
      </Routes>,
      { initialEntries: ["/sentence/mat-5-3/rank"] },
    );
    await screen.findByTestId("rank-list");
    const tieButtons = await screen.findAllByTestId("rank-card__tie");
    await userEvent.click(tieButtons[1]);
    const saveBtn = await screen.findByTestId("rank-savebar__save");
    await userEvent.click(saveBtn);
    expect(await screen.findByTestId("rank-conflict-modal")).toBeInTheDocument();
    expect(screen.getByTestId("rank-conflict-modal__entries")).toHaveTextContent(/WEB/);
  });
});
