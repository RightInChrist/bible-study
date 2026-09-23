/**
 * useGsvChapter hook test (Slice 5).
 *
 * Asserts the right ?format= query param is sent for each format
 * variant — JSON uses apiFetch (parsed JSON), text/markdown go through
 * the raw-body apiFetchText path so the response body comes back as a
 * string.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useGsvChapter } from "../src/api/hooks";

function wrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useGsvChapter", () => {
  it("requests format=json and parses JSON body", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          chapter: 5,
          coverage: {
            chapter: 5,
            total_sentences: 71,
            total_red_letter_sentences: 68,
            ranked_red_letter_sentences: 0,
            unresolved_ties: 0,
          },
          sentences: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useGsvChapter(5, "json"), {
      wrapper: wrapper(),
    });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(fetchMock).toHaveBeenCalled();
    const firstCall = fetchMock.mock.calls[0] as unknown[] | undefined;
    const url = firstCall?.[0];
    expect(String(url)).toContain("format=json");
    expect(result.current.data?.format).toBe("json");
    if (result.current.data?.format === "json") {
      expect(result.current.data.data.chapter).toBe(5);
    }
  });

  it("requests format=markdown and returns the raw body string", async () => {
    const fetchMock = vi.fn(async () =>
      new Response("# Matthew 5\n\nbody", {
        status: 200,
        headers: { "content-type": "text/markdown" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useGsvChapter(5, "markdown"), {
      wrapper: wrapper(),
    });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    const firstCall = fetchMock.mock.calls[0] as unknown[] | undefined;
    const url = firstCall?.[0];
    expect(String(url)).toContain("format=markdown");
    expect(result.current.data?.format).toBe("markdown");
    if (result.current.data?.format === "markdown") {
      expect(result.current.data.data).toBe("# Matthew 5\n\nbody");
    }
  });

  it("requests format=text and returns the raw body string", async () => {
    const fetchMock = vi.fn(async () =>
      new Response("Matthew 5 — plain text", {
        status: 200,
        headers: { "content-type": "text/plain" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useGsvChapter(5, "text"), {
      wrapper: wrapper(),
    });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    const firstCall = fetchMock.mock.calls[0] as unknown[] | undefined;
    const url = firstCall?.[0];
    expect(String(url)).toContain("format=text");
    expect(result.current.data?.format).toBe("text");
    if (result.current.data?.format === "text") {
      expect(result.current.data.data).toBe("Matthew 5 — plain text");
    }
  });
});
