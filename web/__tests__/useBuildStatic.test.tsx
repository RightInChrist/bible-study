/**
 * Hook test for `useBuildStatic`. Mirrors `useReimport.test.tsx` —
 * asserts the right URL, method, headers, body, and typed response.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useBuildStatic } from "../src/api/hooks";
import type { BuildStaticResponse } from "../src/api/types";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const SAMPLE_RESPONSE: BuildStaticResponse = {
  dist_path: "/abs/path/to/dist",
  files_written: 4321,
  coverage: { ranked: 17, total: 24, ch5_ranked: 17, ch5_total: 24 },
  took_ms: 1842,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useBuildStatic", () => {
  it("POSTs the right body, returns BuildStaticResponse", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      expect(url).toBe("/api/v1/admin/build-static");
      expect((init?.method ?? "GET").toUpperCase()).toBe("POST");
      const headers = new Headers(init?.headers);
      expect(headers.get("X-Requested-By")).toBe("bible-study-ui");
      const body = JSON.parse(String(init?.body ?? "{}"));
      expect(body.include_unranked_placeholders).toBe(true);
      return new Response(JSON.stringify(SAMPLE_RESPONSE), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useBuildStatic(), { wrapper });
    result.current.mutate({ include_unranked_placeholders: true });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.files_written).toBe(4321);
  });
});
