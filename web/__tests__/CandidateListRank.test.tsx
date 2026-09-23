/**
 * Unit tests for the rank-page CandidateList helpers.
 *
 * - normalizeEntries reassigns position + rank, respecting tied_with_above
 * - moveEntry produces a new normalised order
 * - tie toggle on the second card produces shared-rank semantics
 */
import { describe, it, expect } from "vitest";

import {
  candidateRefKey,
  moveEntry,
  normalizeEntries,
} from "../src/components/rank/CandidateList";
import type { RankingEntry } from "../src/api/types";

function trEntry(name: "BSB" | "BLB" | "WEB", position: number): RankingEntry {
  return {
    position,
    rank: position,
    tied_with_above: false,
    candidate_ref: { kind: "translation", name, verse_range: "5:3" },
  };
}

describe("normalizeEntries", () => {
  it("reassigns position 1..N starting from 1", () => {
    const out = normalizeEntries([
      trEntry("BSB", 5),
      trEntry("BLB", 9),
      trEntry("WEB", 12),
    ]);
    expect(out.map((e) => e.position)).toEqual([1, 2, 3]);
  });

  it("computes competition-style rank with tied_with_above", () => {
    const second = trEntry("BLB", 2);
    second.tied_with_above = true;
    const out = normalizeEntries([trEntry("BSB", 1), second, trEntry("WEB", 3)]);
    expect(out.map((e) => e.rank)).toEqual([1, 1, 3]);
  });

  it("never marks position 1 as tied_with_above", () => {
    const first = trEntry("BSB", 1);
    first.tied_with_above = true;
    const out = normalizeEntries([first]);
    expect(out[0].tied_with_above).toBe(false);
    expect(out[0].rank).toBe(1);
  });
});

describe("moveEntry", () => {
  it("moves a card to a new position and re-normalises", () => {
    const list = [trEntry("BSB", 1), trEntry("BLB", 2), trEntry("WEB", 3)];
    const moved = moveEntry(list, 2, 0); // WEB to top
    expect(moved.map((e) => e.candidate_ref.kind === "translation" && e.candidate_ref.name)).toEqual([
      "WEB",
      "BSB",
      "BLB",
    ]);
    expect(moved.map((e) => e.rank)).toEqual([1, 2, 3]);
  });

  it("returns the same list on a no-op move", () => {
    const list = [trEntry("BSB", 1), trEntry("BLB", 2)];
    expect(moveEntry(list, 1, 1)).toBe(list);
  });
});

describe("candidateRefKey", () => {
  it("distinguishes translation vs claude candidates", () => {
    expect(
      candidateRefKey({ kind: "translation", name: "BSB", verse_range: "5:3" }),
    ).toBe("translation:BSB:5:3");
    expect(candidateRefKey({ kind: "claude", candidate_id: 42 })).toBe(
      "claude:42",
    );
  });
});
