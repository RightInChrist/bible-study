import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  useChapterOverlays,
  useMarkRedLetter,
  useUnmarkRedLetter,
} from "../src/api/hooks";
import type {
  ChapterOverlaysResponse,
  MarkRedLetterResponse,
  Overlay,
  UnmarkRedLetterResponse,
} from "../src/api/types";

function head(overrides: Partial<Overlay> = {}): Overlay {
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
    version: 1,
    ...overrides,
  };
}

const overlaysResponse: ChapterOverlaysResponse = {
  chapter: 8,
  chains: [{ head: head(), history: [] }],
  effective_sentence_ids: ["mat-8-26-1"],
};

const markResponse: MarkRedLetterResponse = {
  overlay: head({ overlay_id: 9, version: 1 }),
  affected_sentence_ids: ["mat-8-26-1"],
};

const unmarkResponse: UnmarkRedLetterResponse = {
  overlay: head({
    overlay_id: 11,
    parent_overlay_id: 7,
    operation: "reject",
    rejected: true,
    start_sentence_id: null,
    start_word_offset: null,
    end_sentence_id: null,
    end_word_offset: null,
    version: 2,
  }),
  affected_sentence_ids: [],
};

let posted: { url: string; body: unknown; headers: Headers }[] = [];

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  posted = [];
  vi.restoreAllMocks();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/red-letter/chapter/8/overlays") && method === "GET") {
      return new Response(JSON.stringify(overlaysResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/red-letter/mark") && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      posted.push({ url, body, headers: new Headers(init?.headers) });
      return new Response(JSON.stringify(markResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/red-letter/unmark") && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      posted.push({ url, body, headers: new Headers(init?.headers) });
      return new Response(JSON.stringify(unmarkResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

describe("red-letter hooks", () => {
  it("useChapterOverlays fetches the chapter's chains", async () => {
    const { result } = renderHook(() => useChapterOverlays(8), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.chapter).toBe(8);
    expect(result.current.data?.chains).toHaveLength(1);
  });

  it("useMarkRedLetter posts the request body and X-Requested-By", async () => {
    const { result } = renderHook(() => useMarkRedLetter(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        chapter: 8,
        start_verse: 26,
        end_verse: 26,
      });
    });
    const call = posted.find((p) => p.url.includes("/mark"));
    expect(call?.body).toMatchObject({
      chapter: 8,
      start_verse: 26,
      end_verse: 26,
    });
    expect(call?.headers.get("X-Requested-By")).toBe("bible-study-ui");
  });

  it("useUnmarkRedLetter sets the If-Match header to the head version", async () => {
    const { result } = renderHook(() => useUnmarkRedLetter(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        body: { scope: "manual_overlay", target_id: 7 },
        version: 5,
        chapter: 8,
      });
    });
    const call = posted.find((p) => p.url.includes("/unmark"));
    expect(call?.headers.get("If-Match")).toBe("5");
    expect(call?.body).toMatchObject({
      scope: "manual_overlay",
      target_id: 7,
    });
  });
});
