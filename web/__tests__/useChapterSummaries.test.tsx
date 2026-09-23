/**
 * useChapterSummaries / useGenerateSummary hook tests (Slice 8).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useChapterSummaries, useGenerateSummary } from "../src/api/hooks";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function wrapper(children: ReactNode): JSX.Element {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useChapterSummaries", () => {
  it("fetches /api/v1/chapters/{N}/summaries", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ chapter: 5, summaries: [] }), {
        status: 200,
      }),
    );
    const { result } = renderHook(() => useChapterSummaries(5), {
      wrapper: ({ children }) => wrapper(children),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chapters/5/summaries",
      expect.any(Object),
    );
    expect(result.current.data?.summaries).toEqual([]);
  });

  it("disables the query when chapter is null", () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    renderHook(() => useChapterSummaries(null), {
      wrapper: ({ children }) => wrapper(children),
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useGenerateSummary", () => {
  it("posts the chapter-summary run body and includes CSRF header", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          run_id: "r-1",
          status: "completed",
          style_prompt_version: "chapter-summary-v1",
          source_set_id: "CHAPTER_BUNDLE",
          model: "claude-opus-4-7+xhigh",
          items_count: 1,
          items_completed: 1,
          items_failed: 0,
          items_pending: 0,
          items_running: 0,
          items_cancelled: 0,
          items_interrupted: 0,
          estimated_worktree_count: 1,
          sentence_ids: [],
          chapter: 5,
          created_at: "2026-05-08T12:00:00Z",
        }),
        { status: 200 },
      ),
    );
    const { result } = renderHook(() => useGenerateSummary(), {
      wrapper: ({ children }) => wrapper(children),
    });
    result.current.mutate({
      scope: { kind: "chapter_summary", chapter: 5 },
      style_prompt_version: "chapter-summary-v1",
      source_set_id: "CHAPTER_BUNDLE",
      model: "claude-opus-4-7",
      effort: "xhigh",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const call = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(call[0]).toBe("/api/v1/runs");
    expect(call[1].method).toBe("POST");
    const headers = new Headers(call[1].headers);
    expect(headers.get("X-Requested-By")).toBe("bible-study-ui");
  });
});
