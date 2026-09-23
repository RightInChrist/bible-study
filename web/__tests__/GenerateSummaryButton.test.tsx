/**
 * GenerateSummaryButton tests (Slice 8).
 *
 * Asserts the button POSTs the right body shape and polls until the
 * run completes. The button is wrapped in <AuthOnly>, which renders
 * normally in dev mode (the test default).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GenerateSummaryButton } from "../src/components/chapter/GenerateSummaryButton";
import { renderWithProviders } from "./test-utils";

const RUN_RESPONSE_BASE = {
  run_id: "run-chapter-1",
  status: "completed" as const,
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
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("GenerateSummaryButton", () => {
  it("submits POST /api/v1/runs with the chapter_summary scope and CSRF header", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(RUN_RESPONSE_BASE), { status: 200 }),
    );

    renderWithProviders(<GenerateSummaryButton chapter={5} />);
    const button = await screen.findByTestId("generate-summary-button__submit");
    await userEvent.click(button);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/runs");
    expect(init.method).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("X-Requested-By")).toBe("bible-study-ui");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      scope: { kind: "chapter_summary", chapter: 5 },
      style_prompt_version: "chapter-summary-v1",
      source_set_id: "CHAPTER_BUNDLE",
      model: "claude-opus-4-7",
      effort: "xhigh",
    });
  });

  it("reports the success state when the run completes", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(RUN_RESPONSE_BASE), { status: 200 }),
    );

    renderWithProviders(<GenerateSummaryButton chapter={5} />);
    const button = await screen.findByTestId("generate-summary-button__submit");
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/Done/);
    });
  });
});
