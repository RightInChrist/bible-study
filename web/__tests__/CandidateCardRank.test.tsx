/**
 * Unit tests for the rank-page CandidateCard component.
 *
 * - Renders translation provenance ("BSB · 5:3").
 * - Renders Claude provenance with the five-tuple identity.
 * - Hide button disabled for translation cards (open-source can't be hidden).
 * - Tie toggle calls onToggleTie.
 */
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CandidateCard } from "../src/components/rank/CandidateCard";
import type {
  AvailableCandidate,
  RankingEntry,
} from "../src/api/types";
import { renderWithProviders } from "./test-utils";

function noop(): void {
  return;
}

const TRANSLATION_ENTRY: RankingEntry = {
  position: 1,
  rank: 1,
  tied_with_above: false,
  candidate_ref: { kind: "translation", name: "BSB", verse_range: "5:3" },
};
const TRANSLATION_CARD: AvailableCandidate = {
  kind: "translation",
  name: "BSB",
  verse_range: "5:3",
  text: "Blessed are the poor in spirit, for theirs is the kingdom of heaven.",
};

const CLAUDE_ENTRY: RankingEntry = {
  position: 2,
  rank: 2,
  tied_with_above: false,
  candidate_ref: { kind: "claude", candidate_id: 99 },
};
const CLAUDE_CARD: AvailableCandidate = {
  kind: "claude",
  candidate: {
    candidate_id: 99,
    sentence_id: "mat-5-3",
    style_prompt_version: "first-century-jewish-v1",
    source_set_id: "BOTH_GREEK",
    model: "claude-opus-4-7+xhigh",
    generated_at: "2026-05-05T12:00:00Z",
    candidate_text: "Honored are those poor at the level of breath …",
    source_snapshot_hash: "deadbeef",
    hidden_bool: false,
  },
};

describe("CandidateCard (rank)", () => {
  it("renders translation provenance footer", () => {
    renderWithProviders(
      <CandidateCard
        entry={TRANSLATION_ENTRY}
        card={TRANSLATION_CARD}
        isFocused={false}
        onFocus={noop}
        onToggleTie={noop}
        onHide={noop}
        onDragStart={noop}
        onDragOver={() => undefined}
        onDrop={noop}
        onDragEnd={noop}
      />,
    );
    expect(screen.getByText("BSB · 5:3")).toBeInTheDocument();
  });

  it("renders Claude provenance with the full five-tuple identity", () => {
    renderWithProviders(
      <CandidateCard
        entry={CLAUDE_ENTRY}
        card={CLAUDE_CARD}
        isFocused={false}
        onFocus={noop}
        onToggleTie={noop}
        onHide={noop}
        onDragStart={noop}
        onDragOver={() => undefined}
        onDrop={noop}
        onDragEnd={noop}
      />,
    );
    expect(
      screen.getByText(
        /claude · first-century-jewish-v1 · BOTH_GREEK · claude-opus-4-7\+xhigh · 2026-05-05T12:00:00Z/,
      ),
    ).toBeInTheDocument();
  });

  it("disables the hide button for translation cards", () => {
    renderWithProviders(
      <CandidateCard
        entry={TRANSLATION_ENTRY}
        card={TRANSLATION_CARD}
        isFocused={false}
        onFocus={noop}
        onToggleTie={noop}
        onHide={noop}
        onDragStart={noop}
        onDragOver={() => undefined}
        onDrop={noop}
        onDragEnd={noop}
      />,
    );
    expect(screen.getByTestId("rank-card__hide")).toBeDisabled();
  });

  it("calls onToggleTie when the tie button is clicked", async () => {
    const onToggleTie = vi.fn();
    renderWithProviders(
      <CandidateCard
        entry={CLAUDE_ENTRY}
        card={CLAUDE_CARD}
        isFocused={false}
        onFocus={noop}
        onToggleTie={onToggleTie}
        onHide={noop}
        onDragStart={noop}
        onDragOver={() => undefined}
        onDrop={noop}
        onDragEnd={noop}
      />,
    );
    await userEvent.click(screen.getByTestId("rank-card__tie"));
    expect(onToggleTie).toHaveBeenCalledOnce();
  });

  it("calls onHide for Claude cards (hide button enabled)", async () => {
    const onHide = vi.fn();
    renderWithProviders(
      <CandidateCard
        entry={CLAUDE_ENTRY}
        card={CLAUDE_CARD}
        isFocused={false}
        onFocus={noop}
        onToggleTie={noop}
        onHide={onHide}
        onDragStart={noop}
        onDragOver={() => undefined}
        onDrop={noop}
        onDragEnd={noop}
      />,
    );
    const hideBtn = screen.getByTestId("rank-card__hide");
    expect(hideBtn).not.toBeDisabled();
    await userEvent.click(hideBtn);
    expect(onHide).toHaveBeenCalledOnce();
  });
});
