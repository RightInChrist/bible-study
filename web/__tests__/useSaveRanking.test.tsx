/**
 * Direct tests for the useSaveRanking hook.
 *
 * - Sends If-Match: <version> on PUT
 * - Throws ApiError on 409 so the caller can branch on stale_version
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useSaveRanking } from "../src/api/hooks";
import { ApiError } from "../src/api/client";
import type { RankingResponse } from "../src/api/types";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const SAVED: RankingResponse = {
  sentence_id: "mat-5-3",
  version: 1,
  notes: null,
  entries: [],
  available_candidates: [],
  hidden_combos: [],
};

describe("useSaveRanking", () => {
  it("sends If-Match header derived from the version arg", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(SAVED), { status: 200 }),
    );
    const { result } = renderHook(() => useSaveRanking(), { wrapper });
    await result.current.mutateAsync({
      sentenceId: "mat-5-3",
      version: 5,
      body: { entries: [], notes: null },
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("If-Match")).toBe("5");
    expect(headers.get("X-Requested-By")).toBe("bible-study-ui");
  });

  it("throws ApiError(409) on stale_version so the caller can branch", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "stale_version",
          message: "stale",
          details: { current_version: 3 },
        }),
        { status: 409 },
      ),
    );
    const { result } = renderHook(() => useSaveRanking(), { wrapper });
    await expect(
      result.current.mutateAsync({
        sentenceId: "mat-5-3",
        version: 0,
        body: { entries: [], notes: null },
      }),
    ).rejects.toBeInstanceOf(ApiError);
    await waitFor(() => expect(result.current.isError).toBe(true));
    const err = result.current.error as ApiError;
    expect(err.status).toBe(409);
    expect(err.body?.code).toBe("stale_version");
  });
});
