/**
 * StatusPill tests for Slice 6:
 *   - Re-import button is enabled when stale.
 *   - Click invokes the mutation; on 200, renders a toast.
 *   - On 409 `orphans_detected`, navigates to /admin/orphans (we assert
 *     by capturing the navigation via a custom Routes setup).
 *   - On 409 `import_in_progress`, renders the "already running" toast.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { StatusPill } from "../src/components/StatusPill";
import type { FixtureStatusResponse } from "../src/api/types";

function NavSpy({ onChange }: { onChange: (path: string) => void }) {
  const location = useLocation();
  onChange(location.pathname);
  return null;
}

function renderPillAt(path: string, onNav?: (p: string) => void) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        {onNav ? <NavSpy onChange={onNav} /> : null}
        <Routes>
          <Route path="/" element={<StatusPill />} />
          <Route path="/admin/orphans" element={<div data-testid="orphan-route">orphans page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function staleStatus(): FixtureStatusResponse {
  return {
    disk_manifest_hash: "newdiskhash00000000",
    db_fixture_version: "olddbhash00000000000",
    stale: true,
    last_imported_at: "2026-05-01T12:00:00Z",
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("StatusPill re-import button", () => {
  it("is enabled when stale and triggers the mutation on click", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/v1/admin/fixture-status") {
        return new Response(JSON.stringify(staleStatus()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "/api/v1/admin/reimport") {
        calls += 1;
        expect((init?.method ?? "GET").toUpperCase()).toBe("POST");
        return new Response(
          JSON.stringify({
            fixture_version: "newhash",
            files_imported: 7,
            sentences_built: 1682,
            words_built: 1,
            byzantine_verses: 1,
            english_verses: 1,
            bib_interlinear_words: 1,
            red_letter_source_ranges: 2,
            style_prompts: 4,
            elapsed_ms: 100,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPillAt("/");
    const btn = await screen.findByTestId("status-pill-reimport");
    expect(btn).not.toBeDisabled();
    await userEvent.click(btn);
    await waitFor(() => expect(calls).toBe(1));
    expect(await screen.findByTestId("status-pill-toast")).toHaveTextContent(
      /Re-imported/i,
    );
  });

  it("navigates to /admin/orphans on 409 orphans_detected", async () => {
    const visited: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/v1/admin/fixture-status") {
        return new Response(JSON.stringify(staleStatus()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "/api/v1/admin/reimport") {
        return new Response(
          JSON.stringify({
            code: "orphans_detected",
            message: "1 orphan(s)",
            details: {
              orphan_summary: {
                affected_candidates: [],
                affected_rankings: [],
                affected_overlays: [],
                total: 0,
              },
            },
          }),
          { status: 409, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPillAt("/", (p) => visited.push(p));
    const btn = await screen.findByTestId("status-pill-reimport");
    await userEvent.click(btn);
    await waitFor(() =>
      expect(visited).toContain("/admin/orphans"),
    );
  });

  it("shows the in-progress toast on 409 import_in_progress", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/v1/admin/fixture-status") {
        return new Response(JSON.stringify(staleStatus()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "/api/v1/admin/reimport") {
        return new Response(
          JSON.stringify({
            code: "import_in_progress",
            message: "An import is already running.",
            details: null,
          }),
          { status: 409, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPillAt("/");
    const btn = await screen.findByTestId("status-pill-reimport");
    await userEvent.click(btn);
    expect(await screen.findByTestId("status-pill-toast")).toHaveTextContent(
      /already running/i,
    );
  });
});
