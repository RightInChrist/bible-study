/**
 * Hook test for the parallel-reader chapter fetch — exercises the
 * TanStack Query integration with a mocked `fetch`.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useSentencesByChapter } from "../src/api/hooks";
import type { SentenceListResponse } from "../src/api/types";

function buildResponse(): SentenceListResponse {
  return {
    chapter: 5,
    sentences: [
      {
        sentence_id: "mat-5-1",
        chapter: 5,
        ordinal_in_chapter: 1,
        start_verse: 1,
        end_verse: 1,
        starts_at_verse_boundary: true,
        ends_at_verse_boundary: false,
        text_preview: "first preview",
        word_count: 8,
        is_red_letter: false,
      },
    ],
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.restoreAllMocks();
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/sentences?chapter=5")) {
      return new Response(JSON.stringify(buildResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

describe("useSentencesByChapter", () => {
  it("loads chapter data via the FastAPI endpoint", async () => {
    const { result } = renderHook(() => useSentencesByChapter(5), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.chapter).toBe(5);
    expect(result.current.data?.sentences).toHaveLength(1);
    expect(result.current.data?.sentences[0]?.sentence_id).toBe("mat-5-1");
  });

  it("does not fetch when the chapter is out of range", async () => {
    const fetchSpy = globalThis.fetch as ReturnType<typeof vi.fn>;
    const { result } = renderHook(() => useSentencesByChapter(99), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
