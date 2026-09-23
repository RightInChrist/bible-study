/**
 * Drag-and-drop list of ranked cards (Designer Flow 5 — the focal
 * interaction).
 *
 * Implementation: native HTML5 drag-and-drop, no extra dependency. The
 * list owns the local "draft" state of `RankingEntry[]`; this is what
 * the user is editing. The parent passes the saved entries; on drop
 * we call `onChange(newEntries)` with re-positioned + re-ranked rows.
 *
 * v1 simplifications:
 *   - Linear ties only via the `tied_with_above` flag — multi-card tie
 *     groups are achieved by consecutive rows with the flag set.
 *   - Drag-onto-card is just a position-swap; the dedicated tie toggle
 *     is the explicit way to mark a tie.
 */
import { useCallback, useMemo, useRef } from "react";

import type {
  AvailableCandidate,
  CandidateRef,
  RankingEntry,
} from "../../api/types";
import { CandidateCard } from "./CandidateCard";

interface Props {
  entries: RankingEntry[];
  cardLookup: Map<string, AvailableCandidate>;
  focusedIndex: number;
  onFocusIndex(index: number): void;
  onChange(next: RankingEntry[]): void;
  onHide(card: AvailableCandidate): void;
}

/**
 * Stable string key for one candidate ref. Used to look up the
 * available-candidate card and to match a list entry's identity.
 */
export function candidateRefKey(ref: CandidateRef): string {
  if (ref.kind === "translation") {
    return `translation:${ref.name}:${ref.verse_range}`;
  }
  return `claude:${ref.candidate_id}`;
}

export function buildCardLookup(
  cards: AvailableCandidate[],
): Map<string, AvailableCandidate> {
  const map = new Map<string, AvailableCandidate>();
  for (const card of cards) {
    if (card.kind === "translation") {
      map.set(`translation:${card.name}:${card.verse_range}`, card);
    } else {
      map.set(`claude:${card.candidate.candidate_id}`, card);
    }
  }
  return map;
}

/**
 * Recompute `position` (1..N) and `rank` (competition-style) from the
 * current order + tied_with_above flags. Position 1 always gets rank 1
 * with tied_with_above=false; subsequent positions inherit the rank
 * above when their `tied_with_above` is true, else they get a fresh
 * rank equal to the position.
 */
export function normalizeEntries(entries: RankingEntry[]): RankingEntry[] {
  const out: RankingEntry[] = [];
  let lastRank = 0;
  for (let i = 0; i < entries.length; i += 1) {
    const e = entries[i];
    const position = i + 1;
    let rank: number;
    let tiedWithAbove: boolean;
    if (i === 0) {
      rank = 1;
      tiedWithAbove = false;
    } else if (e.tied_with_above) {
      rank = lastRank;
      tiedWithAbove = true;
    } else {
      rank = position;
      tiedWithAbove = false;
    }
    out.push({
      position,
      rank,
      tied_with_above: tiedWithAbove,
      candidate_ref: e.candidate_ref,
    });
    lastRank = rank;
  }
  return out;
}

export function CandidateList({
  entries,
  cardLookup,
  focusedIndex,
  onFocusIndex,
  onChange,
  onHide,
}: Props) {
  const dragSourceRef = useRef<number | null>(null);

  const handleDragStart = useCallback((index: number) => {
    dragSourceRef.current = index;
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  }, []);

  const handleDrop = useCallback(
    (targetIndex: number) => {
      const sourceIndex = dragSourceRef.current;
      if (sourceIndex === null || sourceIndex === targetIndex) {
        dragSourceRef.current = null;
        return;
      }
      const next = [...entries];
      const [moved] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, moved);
      onChange(normalizeEntries(next));
      dragSourceRef.current = null;
    },
    [entries, onChange],
  );

  const handleDragEnd = useCallback(() => {
    dragSourceRef.current = null;
  }, []);

  const toggleTie = useCallback(
    (index: number) => {
      if (index === 0) return; // first card cannot be tied with the (non-existent) one above
      const next = [...entries];
      next[index] = {
        ...next[index],
        tied_with_above: !next[index].tied_with_above,
      };
      onChange(normalizeEntries(next));
    },
    [entries, onChange],
  );

  const cardsByEntry = useMemo(() => {
    return entries.map((e) => cardLookup.get(candidateRefKey(e.candidate_ref)));
  }, [entries, cardLookup]);

  return (
    <div className="rank-list" data-testid="rank-list" role="list">
      {entries.map((entry, index) => {
        const card = cardsByEntry[index];
        if (!card) {
          return (
            <div
              key={candidateRefKey(entry.candidate_ref)}
              className="rank-card rank-card--missing"
              role="listitem"
            >
              <div className="rank-card__position">
                <span className="rank-card__position-label">{entry.rank}</span>
              </div>
              <div className="rank-card__body">
                <div className="rank-card__text">
                  Candidate no longer available — was it hidden?
                </div>
              </div>
            </div>
          );
        }
        return (
          <CandidateCard
            key={candidateRefKey(entry.candidate_ref)}
            entry={entry}
            card={card}
            isFocused={index === focusedIndex}
            onFocus={() => onFocusIndex(index)}
            onToggleTie={() => toggleTie(index)}
            onHide={() => onHide(card)}
            onDragStart={() => handleDragStart(index)}
            onDragOver={handleDragOver}
            onDrop={() => handleDrop(index)}
            onDragEnd={handleDragEnd}
          />
        );
      })}
    </div>
  );
}

/**
 * Move the card at index `from` to index `to`, returning a new
 * normalized entries list. Pure helper for keyboard-driven moves.
 */
export function moveEntry(
  entries: RankingEntry[],
  from: number,
  to: number,
): RankingEntry[] {
  if (from === to) return entries;
  if (from < 0 || from >= entries.length) return entries;
  if (to < 0 || to >= entries.length) return entries;
  const next = [...entries];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return normalizeEntries(next);
}
