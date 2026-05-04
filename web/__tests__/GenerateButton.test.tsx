/**
 * Generate-button + CandidateList rendering smoke test.
 *
 * The full create-run round-trip is covered by the API tests; this
 * verifies the React surface renders + posts the documented body shape
 * with CSRF headers, and that the candidate list renders rows with the
 * five identity fields visible.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GenerateButton } from "../src/components/GenerateButton";
import { CandidateList } from "../src/components/CandidateList";
import { renderWithProviders } from "./test-utils";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("GenerateButton", () => {
  it("submits POST /api/v1/runs with the documented body and CSRF header", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          run_id: "run-1",
          status: "completed",
          style_prompt_version: "literal-v1",
          source_set_id: "BOTH_GREEK",
          model: "claude-sonnet-4-6",
          items_count: 1,
          items_completed: 1,
          items_failed: 0,
          items_pending: 0,
          items_running: 0,
          items_cancelled: 0,
          items_interrupted: 0,
          estimated_cost_usd: 0.01,
          estimated_cost_usd_band_pct: 20,
          sentence_ids: ["mat-5-4"],
          created_at: "2026-05-03T00:00:00Z",
        }),
        { status: 200 },
      ),
    );
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          run_id: "run-1",
          status: "completed",
          style_prompt_version: "literal-v1",
          source_set_id: "BOTH_GREEK",
          model: "claude-sonnet-4-6",
          items_count: 1,
          items_completed: 1,
          items_failed: 0,
          items_pending: 0,
          items_running: 0,
          items_cancelled: 0,
          items_interrupted: 0,
          estimated_cost_usd: 0.01,
          estimated_cost_usd_band_pct: 20,
          sentence_ids: ["mat-5-4"],
          created_at: "2026-05-03T00:00:00Z",
        }),
        { status: 200 },
      ),
    );

    renderWithProviders(<GenerateButton sentenceId="mat-5-4" />);
    const button = await screen.findByTestId("generate-button__submit");
    await userEvent.click(button);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/runs");
    expect(init.method).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("X-Requested-By")).toBe("bible-study-ui");
    expect(headers.get("Content-Type")).toBe("application/json");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      scope: { kind: "one_sentence", sentence_id: "mat-5-4" },
      style_prompt_version: "literal-v1",
      source_set_id: "BOTH_GREEK",
      model: "claude-sonnet-4-6",
    });
  });
});

describe("CandidateList", () => {
  it("renders a row per candidate with all five identity fields visible", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sentence_id: "mat-5-4",
          candidates: [
            {
              candidate_id: 42,
              sentence_id: "mat-5-4",
              style_prompt_version: "literal-v1",
              source_set_id: "BOTH_GREEK",
              model: "claude-sonnet-4-6",
              generated_at: "2026-05-03T12:00:00Z",
              candidate_text: "Blessed are the poor in spirit.",
              source_snapshot_hash: "abcd",
              hidden_bool: false,
            },
          ],
        }),
        { status: 200 },
      ),
    );

    renderWithProviders(<CandidateList sentenceId="mat-5-4" />);
    expect(await screen.findByText(/Blessed are the poor/)).toBeInTheDocument();
    const provenance = await screen.findByText(
      /claude · literal-v1 · BOTH_GREEK · claude-sonnet-4-6 · 2026-05-03T12:00:00Z/,
    );
    expect(provenance).toBeInTheDocument();
  });

  it("renders nothing when there are no candidates", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ sentence_id: "mat-5-4", candidates: [] }),
        { status: 200 },
      ),
    );

    const { container } = renderWithProviders(<CandidateList sentenceId="mat-5-4" />);
    await waitFor(() => {
      expect(container.querySelector("[data-testid='candidate-list']")).toBeNull();
    });
  });
});
