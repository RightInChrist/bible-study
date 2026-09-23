/**
 * ConflictModal tests — discard/reapply flows.
 */
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConflictModal } from "../src/components/rank/ConflictModal";
import type { RankingEntry } from "../src/api/types";
import { renderWithProviders } from "./test-utils";

const SERVER_ENTRIES: RankingEntry[] = [
  {
    position: 1,
    rank: 1,
    tied_with_above: false,
    candidate_ref: { kind: "translation", name: "WEB", verse_range: "5:3" },
  },
];

describe("ConflictModal", () => {
  it("renders the server's current entries and notes", () => {
    renderWithProviders(
      <ConflictModal
        payload={{
          current_version: 7,
          current_entries: SERVER_ENTRIES,
          current_notes: "from another tab",
        }}
        onDiscardMyChanges={() => undefined}
        onReapplyOverServer={() => undefined}
        onDismiss={() => undefined}
      />,
    );
    expect(screen.getByTestId("rank-conflict-modal")).toBeInTheDocument();
    expect(screen.getByTestId("rank-conflict-modal__entries")).toHaveTextContent(/WEB/);
    expect(screen.getByText(/from another tab/)).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("calls onDiscardMyChanges when the discard button is clicked", async () => {
    const onDiscard = vi.fn();
    renderWithProviders(
      <ConflictModal
        payload={{
          current_version: 1,
          current_entries: SERVER_ENTRIES,
          current_notes: null,
        }}
        onDiscardMyChanges={onDiscard}
        onReapplyOverServer={() => undefined}
        onDismiss={() => undefined}
      />,
    );
    await userEvent.click(screen.getByTestId("rank-conflict-modal__discard"));
    expect(onDiscard).toHaveBeenCalledOnce();
  });

  it("calls onReapplyOverServer when reapply is clicked", async () => {
    const onReapply = vi.fn();
    renderWithProviders(
      <ConflictModal
        payload={{
          current_version: 1,
          current_entries: SERVER_ENTRIES,
          current_notes: null,
        }}
        onDiscardMyChanges={() => undefined}
        onReapplyOverServer={onReapply}
        onDismiss={() => undefined}
      />,
    );
    await userEvent.click(screen.getByTestId("rank-conflict-modal__reapply"));
    expect(onReapply).toHaveBeenCalledOnce();
  });
});
