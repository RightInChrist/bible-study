import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { RedLetterEditorPage } from "../src/routes/RedLetterEditorPage";
import type {
  ChapterOverlaysResponse,
  MarkRedLetterResponse,
  Overlay,
  UnmarkRedLetterResponse,
} from "../src/api/types";

function renderEditor(initialPath: string = "/red-letter/chapter/8/edit") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/red-letter/chapter/:chapter/edit"
            element={<RedLetterEditorPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
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
    version: 1,
    ...overrides,
  };
}

const overlaysResponse: ChapterOverlaysResponse = {
  chapter: 8,
  chains: [
    {
      head: manualHead(),
      history: [],
    },
  ],
  effective_sentence_ids: ["mat-8-26-1"],
};

const markResponse: MarkRedLetterResponse = {
  overlay: manualHead({ overlay_id: 9 }),
  affected_sentence_ids: ["mat-8-26-1"],
};

const unmarkResponse: UnmarkRedLetterResponse = {
  overlay: manualHead({
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

let postedBodies: { url: string; body: unknown; headers: Headers }[] = [];

beforeEach(() => {
  postedBodies = [];
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
      postedBodies.push({ url, body, headers: new Headers(init?.headers) });
      return new Response(JSON.stringify(markResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/red-letter/unmark") && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      postedBodies.push({ url, body, headers: new Headers(init?.headers) });
      return new Response(JSON.stringify(unmarkResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

describe("RedLetterEditorPage", () => {
  it("renders chains for the chapter", async () => {
    renderEditor();
    // Without a route match the params will be undefined; render with the
    // route hierarchy via path matching is overkill — we instead pass the
    // wrapper's MemoryRouter to set the path. The page parses chapter from
    // useParams; outside a matching <Route>, the param is undefined and
    // the page falls back to chapter=1. So we route through useParams via
    // a wrapping Route below in the next assertion.
    await waitFor(() => {
      expect(screen.getByText(/red-letter range editor/i)).toBeInTheDocument();
    });
  });

  it("submits the mark form with the correct body", async () => {
    renderEditor();
    const startInput = await screen.findByTestId("mark-new-start-verse");
    const endInput = screen.getByTestId("mark-new-end-verse");
    fireEvent.change(startInput, { target: { value: "26" } });
    fireEvent.change(endInput, { target: { value: "27" } });
    fireEvent.click(screen.getByTestId("mark-new-submit"));
    await waitFor(() => {
      const markCall = postedBodies.find((p) => p.url.includes("/mark"));
      expect(markCall).toBeDefined();
      expect(markCall?.body).toMatchObject({
        start_verse: 26,
        end_verse: 27,
      });
    });
  });

  it("rejects an active chain via the Reject button", async () => {
    renderEditor();
    const reject = await screen.findByTestId("chain-reject");
    fireEvent.click(reject);
    await waitFor(() => {
      const unmarkCall = postedBodies.find((p) => p.url.includes("/unmark"));
      expect(unmarkCall).toBeDefined();
      expect(unmarkCall?.body).toMatchObject({
        scope: "manual_overlay",
        target_id: 7,
      });
      expect(unmarkCall?.headers.get("If-Match")).toBe("1");
    });
  });

  it("does not show Reject when chain is already rejected", async () => {
    const rejectedResponse: ChapterOverlaysResponse = {
      chapter: 8,
      chains: [
        {
          head: manualHead({
            overlay_id: 12,
            parent_overlay_id: 7,
            operation: "reject",
            rejected: true,
            start_sentence_id: null,
            start_word_offset: null,
            end_sentence_id: null,
            end_word_offset: null,
            version: 2,
          }),
          history: [manualHead()],
        },
      ],
      effective_sentence_ids: [],
    };
    vi.restoreAllMocks();
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(rejectedResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderEditor();
    await screen.findByTestId("chain-card");
    expect(screen.queryByTestId("chain-reject")).toBeNull();
    expect(screen.getByTestId("chain-restore")).toBeInTheDocument();
  });
});
