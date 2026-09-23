/**
 * Generate-button + CandidateList rendering tests (Slice 3a-redo).
 *
 * Covers:
 *  - GenerateButton submits POST /api/v1/runs with the new default body
 *    (first-century-jewish-v1 × BOTH_GREEK × claude-opus-4-7).
 *  - The style-prompt picker can flip the request to one of the
 *    cross-check styles.
 *  - CandidateList renders simple text candidates.
 *  - CandidateList renders structured-JSON candidates with each labelled
 *    section visible.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GenerateButton } from "../src/components/GenerateButton";
import { CandidateList } from "../src/components/CandidateList";
import { renderWithProviders } from "./test-utils";

const RUN_RESPONSE_FIELDS = {
  run_id: "run-1",
  status: "completed",
  source_set_id: "BOTH_GREEK",
  items_count: 1,
  items_completed: 1,
  items_failed: 0,
  items_pending: 0,
  items_running: 0,
  items_cancelled: 0,
  items_interrupted: 0,
  estimated_worktree_count: 1,
  sentence_ids: ["mat-5-4"],
  created_at: "2026-05-03T00:00:00Z",
} as const;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("GenerateButton", () => {
  it("submits POST /api/v1/runs with the documented body and CSRF header (default style: first-century-jewish-v1)", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ...RUN_RESPONSE_FIELDS,
          style_prompt_version: "first-century-jewish-v1",
          model: "claude-opus-4-7",
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
      style_prompt_version: "first-century-jewish-v1",
      source_set_id: "BOTH_GREEK",
      model: "claude-opus-4-7",
      effort: "xhigh",
    });
  });

  it("uses the chosen style prompt from the picker dropdown", async () => {
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ...RUN_RESPONSE_FIELDS,
          style_prompt_version: "literal-v1",
          model: "claude-opus-4-7",
        }),
        { status: 200 },
      ),
    );

    renderWithProviders(<GenerateButton sentenceId="mat-5-4" />);
    const select = (await screen.findByTestId(
      "generate-button__style",
    )) as HTMLSelectElement;
    await userEvent.selectOptions(select, "literal-v1");
    const button = await screen.findByTestId("generate-button__submit");
    await userEvent.click(button);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.style_prompt_version).toBe("literal-v1");
  });
});

describe("CandidateList", () => {
  it("renders a row per candidate with all five identity fields visible (plain-text candidate)", async () => {
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
              model: "claude-opus-4-7",
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
      /claude · literal-v1 · BOTH_GREEK · claude-opus-4-7 · 2026-05-03T12:00:00Z/,
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

  it("renders a structured-JSON candidate with each labelled section", async () => {
    const candidateText = JSON.stringify({
      english: "Blessed are those who are poor at the level of breath.",
      underlying_hypothesis: "Aramaic 'ṭuvayhon le-meskinai b-ruḥa'",
      cultural_notes:
        "Beatitudes are honor-claims pronounced over a marginalised group.",
      intertexts: [
        {
          reference: "Isa 61:1",
          type: "allusion",
          note: "Servant-song echo of preaching to the poor.",
        },
      ],
      audience: "the gathered crowd including disciples",
      pragmatic_act: "pronouncing a blessing on a marginal group",
      confidence:
        "Substrate is well-attested; audience is contested between disciples-only and crowd-inclusive readings.",
    });
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sentence_id: "mat-5-4",
          candidates: [
            {
              candidate_id: 99,
              sentence_id: "mat-5-4",
              style_prompt_version: "first-century-jewish-v1",
              source_set_id: "BOTH_GREEK",
              model: "claude-opus-4-7",
              generated_at: "2026-05-03T12:00:00Z",
              candidate_text: candidateText,
              source_snapshot_hash: "deadbeef",
              hidden_bool: false,
            },
          ],
        }),
        { status: 200 },
      ),
    );

    renderWithProviders(<CandidateList sentenceId="mat-5-4" />);
    expect(
      await screen.findByText(/Blessed are those who are poor/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("candidate-structured")).toBeInTheDocument();
    expect(
      screen.getByTestId("candidate-structured__underlying"),
    ).toHaveTextContent(/ṭuvayhon le-meskinai/);
    expect(
      screen.getByTestId("candidate-structured__cultural"),
    ).toHaveTextContent(/honor-claims/);
    expect(
      screen.getByTestId("candidate-structured__intertexts"),
    ).toHaveTextContent(/Isa 61:1/);
    expect(
      screen.getByTestId("candidate-structured__audience"),
    ).toHaveTextContent(/gathered crowd/);
    expect(
      screen.getByTestId("candidate-structured__pragmatic"),
    ).toHaveTextContent(/pronouncing a blessing/);
    expect(
      screen.getByTestId("candidate-structured__confidence"),
    ).toHaveTextContent(/contested/);
  });
});
