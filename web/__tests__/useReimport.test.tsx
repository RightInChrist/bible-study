/**
 * Hook test for `useReimport`.
 *
 * Asserts the mutation:
 *   - POSTs to `/api/v1/admin/reimport` with the right body.
 *   - Sends the `X-Requested-By: bible-study-ui` header (CSRF gate).
 *   - Surfaces the typed `ReimportResponse` on success.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useReimport } from "../src/api/hooks";
import type { ReimportResponse } from "../src/api/types";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const SAMPLE_RESPONSE: ReimportResponse = {
  fixture_version: "fakehashfortesting" + "0".repeat(40),
  files_imported: 7,
  sentences_built: 1682,
  words_built: 18329,
  byzantine_verses: 1071,
  english_verses: 3207,
  bib_interlinear_words: 18373,
  red_letter_source_ranges: 2,
  style_prompts: 4,
  elapsed_ms: 901,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useReimport", () => {
  it("POSTs the right body and headers, returns ReimportResponse", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      expect(url).toBe("/api/v1/admin/reimport");
      expect((init?.method ?? "GET").toUpperCase()).toBe("POST");
      const headers = new Headers(init?.headers);
      expect(headers.get("X-Requested-By")).toBe("bible-study-ui");
      const body = JSON.parse(String(init?.body ?? "{}"));
      expect(body.force).toBe(false);
      expect(body.dispositions).toBeUndefined();
      return new Response(JSON.stringify(SAMPLE_RESPONSE), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useReimport(), { wrapper });
    result.current.mutate({ force: false });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.sentences_built).toBe(1682);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("forwards dispositions when force=true", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      expect(body.force).toBe(true);
      expect(body.dispositions).toEqual({ "candidate:42": "delete" });
      return new Response(JSON.stringify(SAMPLE_RESPONSE), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useReimport(), { wrapper });
    result.current.mutate({
      force: true,
      dispositions: { "candidate:42": "delete" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
