/**
 * Per-sentence ranking surface (Designer Flow 5).
 *
 * Loads `GET /sentences/{id}/parallel` for the reference strip and
 * `GET /sentences/{id}/ranking` for the available cards + saved entries
 * + notes + hidden combos.
 *
 * State machine:
 *   - `draftEntries` is what the user is editing (drag-reorder, ties).
 *   - `draftNotes` is the textarea contents.
 *   - `serverVersion` is the OCC token from the last successful read or
 *     write; sent as `If-Match` on the next save.
 *   - `conflict` is set when a save returns 409; clears when the user
 *     chooses Discard or Reapply.
 *
 * Wrapped in `<AuthOnly>` so the static export tree-shakes the rank
 * page out — ranking is an authoring surface, not a public read view.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  useHideCombo,
  useRanking,
  useSaveRanking,
  useSentenceParallel,
  useSentencesByChapter,
} from "../api/hooks";
import type {
  AvailableCandidate,
  RankingEntry,
  RankingResponse,
} from "../api/types";
import { AuthOnly } from "../components/AuthOnly";
import { ChapterNav } from "../components/ChapterNav";
import {
  buildCardLookup,
  candidateRefKey,
  CandidateList,
  moveEntry,
  normalizeEntries,
} from "../components/rank/CandidateList";
import { ConflictModal, type ConflictPayload } from "../components/rank/ConflictModal";
import { KeyboardHelp } from "../components/rank/KeyboardHelp";
import { NotesField } from "../components/rank/NotesField";
import { ReferenceStrip } from "../components/rank/ReferenceStrip";
import { SaveBar } from "../components/rank/SaveBar";

interface ParsedId {
  chapter: number | null;
  raw: string;
}

function parseSentenceId(id: string | undefined): ParsedId {
  if (!id) return { chapter: null, raw: "" };
  const match = /^mat-(\d+)-(\d+)$/.exec(id);
  if (!match) return { chapter: null, raw: id };
  const chapter = Number.parseInt(match[1] ?? "0", 10);
  if (!Number.isInteger(chapter) || chapter < 1 || chapter > 28) {
    return { chapter: null, raw: id };
  }
  return { chapter, raw: id };
}

/**
 * Build the initial draft from a server `RankingResponse`. If no
 * entries exist yet, seed the draft with one card per available
 * candidate so the user has something to drag-order from.
 */
function seedDraft(response: RankingResponse): RankingEntry[] {
  if (response.entries.length > 0) {
    return response.entries;
  }
  const seeded: RankingEntry[] = response.available_candidates.map((card, idx) => {
    if (card.kind === "translation") {
      return {
        position: idx + 1,
        rank: idx + 1,
        tied_with_above: false,
        candidate_ref: {
          kind: "translation",
          name: card.name,
          verse_range: card.verse_range,
        },
      };
    }
    return {
      position: idx + 1,
      rank: idx + 1,
      tied_with_above: false,
      candidate_ref: { kind: "claude", candidate_id: card.candidate.candidate_id },
    };
  });
  return normalizeEntries(seeded);
}

function entriesEqual(a: RankingEntry[], b: RankingEntry[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    const ea = a[i];
    const eb = b[i];
    if (ea.position !== eb.position) return false;
    if (ea.rank !== eb.rank) return false;
    if (ea.tied_with_above !== eb.tied_with_above) return false;
    if (candidateRefKey(ea.candidate_ref) !== candidateRefKey(eb.candidate_ref)) return false;
  }
  return true;
}

export function RankPage() {
  return (
    <AuthOnly>
      <RankPageInner />
    </AuthOnly>
  );
}

function RankPageInner() {
  const { id } = useParams<{ id: string }>();
  const parsed = parseSentenceId(id);
  const navigate = useNavigate();

  const sentenceId = parsed.raw || null;
  const parallelQuery = useSentenceParallel(sentenceId);
  const rankingQuery = useRanking(sentenceId);
  const chapterQuery = useSentencesByChapter(parsed.chapter ?? 0);

  const saveMutation = useSaveRanking();
  const hideMutation = useHideCombo();

  // Local draft state.
  const [draftEntries, setDraftEntries] = useState<RankingEntry[]>([]);
  const [draftNotes, setDraftNotes] = useState<string>("");
  const [serverVersion, setServerVersion] = useState<number>(0);
  const [focusedIndex, setFocusedIndex] = useState<number>(0);
  const [liftedIndex, setLiftedIndex] = useState<number | null>(null);
  const [conflict, setConflict] = useState<ConflictPayload | null>(null);
  const [helpOpen, setHelpOpen] = useState<boolean>(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const notesFocusedRef = useRef<boolean>(false);

  // Hydrate the draft once the ranking GET resolves.
  const initialised = useRef<boolean>(false);
  useEffect(() => {
    if (!rankingQuery.data) return;
    if (initialised.current) return;
    initialised.current = true;
    setDraftEntries(seedDraft(rankingQuery.data));
    setDraftNotes(rankingQuery.data.notes ?? "");
    setServerVersion(rankingQuery.data.version);
  }, [rankingQuery.data]);

  // Re-hydrate when the sentence changes (route param flip).
  useEffect(() => {
    initialised.current = false;
    setLastSavedAt(null);
  }, [sentenceId]);

  const cardLookup = useMemo(() => {
    if (!rankingQuery.data) return new Map<string, AvailableCandidate>();
    return buildCardLookup(rankingQuery.data.available_candidates);
  }, [rankingQuery.data]);

  const isDirty = useMemo(() => {
    if (!rankingQuery.data) return false;
    const serverNotes = rankingQuery.data.notes ?? "";
    if (serverNotes !== draftNotes) return true;
    return !entriesEqual(rankingQuery.data.entries, draftEntries);
  }, [rankingQuery.data, draftEntries, draftNotes]);

  const sentencesInChapter = chapterQuery.data?.sentences ?? [];
  const myIndex = sentencesInChapter.findIndex((s) => s.sentence_id === sentenceId);
  const prevSentenceId =
    myIndex > 0 ? sentencesInChapter[myIndex - 1].sentence_id : null;
  const nextSentenceId =
    myIndex >= 0 && myIndex < sentencesInChapter.length - 1
      ? sentencesInChapter[myIndex + 1].sentence_id
      : null;

  const performSave = useCallback(
    async (after: "stay" | "next" | "prev"): Promise<void> => {
      if (!sentenceId) return;
      try {
        const result = await saveMutation.mutateAsync({
          sentenceId,
          version: serverVersion,
          body: { entries: draftEntries, notes: draftNotes === "" ? null : draftNotes },
        });
        setServerVersion(result.version);
        setDraftEntries(result.entries);
        setDraftNotes(result.notes ?? "");
        setLastSavedAt(new Date().toISOString());
        setConflict(null);
        if (after === "next" && nextSentenceId) {
          navigate(`/sentence/${nextSentenceId}/rank`);
        } else if (after === "prev" && prevSentenceId) {
          navigate(`/sentence/${prevSentenceId}/rank`);
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 409 && err.body) {
          const details = err.body.details as Record<string, unknown> | null;
          if (details) {
            const cv =
              typeof details.current_version === "number"
                ? details.current_version
                : 0;
            const ce = Array.isArray(details.current_entries)
              ? (details.current_entries as RankingEntry[])
              : [];
            const cn =
              typeof details.current_notes === "string"
                ? details.current_notes
                : null;
            setConflict({
              current_version: cv,
              current_entries: ce,
              current_notes: cn,
            });
          }
        } else {
          // Surface other errors as a non-modal alert.
          console.error("Ranking save failed:", err);
        }
      }
    },
    [
      sentenceId,
      saveMutation,
      serverVersion,
      draftEntries,
      draftNotes,
      navigate,
      nextSentenceId,
      prevSentenceId,
    ],
  );

  const onHide = useCallback(
    async (card: AvailableCandidate): Promise<void> => {
      if (!sentenceId) return;
      if (card.kind !== "claude") return;
      try {
        const result = await hideMutation.mutateAsync({
          sentenceId,
          version: serverVersion,
          body: {
            style_prompt_version: card.candidate.style_prompt_version,
            source_set_id: card.candidate.source_set_id,
            model: card.candidate.model,
          },
        });
        setServerVersion(result.version);
        // Drop the hidden card from the draft.
        setDraftEntries((prev) =>
          normalizeEntries(
            prev.filter((e) => {
              if (e.candidate_ref.kind !== "claude") return true;
              return (
                e.candidate_ref.candidate_id !== card.candidate.candidate_id
              );
            }),
          ),
        );
      } catch (err) {
        if (err instanceof ApiError && err.status === 409 && err.body) {
          const details = err.body.details as Record<string, unknown> | null;
          if (details) {
            setConflict({
              current_version:
                typeof details.current_version === "number"
                  ? details.current_version
                  : 0,
              current_entries: Array.isArray(details.current_entries)
                ? (details.current_entries as RankingEntry[])
                : [],
              current_notes:
                typeof details.current_notes === "string"
                  ? details.current_notes
                  : null,
            });
          }
        } else {
          console.error("Hide failed:", err);
        }
      }
    },
    [sentenceId, hideMutation, serverVersion],
  );

  // Keyboard shortcuts.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (notesFocusedRef.current) return; // disabled while typing notes
      if (helpOpen) {
        if (e.key === "Escape") {
          setHelpOpen(false);
          e.preventDefault();
        }
        return;
      }
      if (conflict !== null) return; // disabled while modal is up
      switch (e.key) {
        case "?": {
          setHelpOpen(true);
          e.preventDefault();
          break;
        }
        case "ArrowUp":
        case "k": {
          if (liftedIndex !== null) {
            const target = Math.max(0, liftedIndex - 1);
            setDraftEntries((prev) => moveEntry(prev, liftedIndex, target));
            setLiftedIndex(target);
            setFocusedIndex(target);
          } else {
            setFocusedIndex((i) => Math.max(0, i - 1));
          }
          e.preventDefault();
          break;
        }
        case "ArrowDown":
        case "j": {
          if (liftedIndex !== null) {
            const target = Math.min(draftEntries.length - 1, liftedIndex + 1);
            setDraftEntries((prev) => moveEntry(prev, liftedIndex, target));
            setLiftedIndex(target);
            setFocusedIndex(target);
          } else {
            setFocusedIndex((i) =>
              Math.min(draftEntries.length - 1, i + 1),
            );
          }
          e.preventDefault();
          break;
        }
        case " ":
        case "Enter": {
          setLiftedIndex((cur) => (cur === null ? focusedIndex : null));
          e.preventDefault();
          break;
        }
        case "t": {
          if (focusedIndex > 0 && focusedIndex < draftEntries.length) {
            setDraftEntries((prev) =>
              normalizeEntries(
                prev.map((entry, i) =>
                  i === focusedIndex
                    ? { ...entry, tied_with_above: !entry.tied_with_above }
                    : entry,
                ),
              ),
            );
          }
          e.preventDefault();
          break;
        }
        case "h": {
          if (focusedIndex >= 0 && focusedIndex < draftEntries.length) {
            const entry = draftEntries[focusedIndex];
            if (entry.candidate_ref.kind === "claude") {
              const card = cardLookup.get(candidateRefKey(entry.candidate_ref));
              if (card) {
                void onHide(card);
              }
            }
          }
          e.preventDefault();
          break;
        }
        case "s": {
          void performSave("stay");
          e.preventDefault();
          break;
        }
        case "n":
        case "ArrowRight": {
          void performSave("next");
          e.preventDefault();
          break;
        }
        case "p":
        case "ArrowLeft": {
          void performSave("prev");
          e.preventDefault();
          break;
        }
        default:
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    cardLookup,
    conflict,
    draftEntries,
    focusedIndex,
    helpOpen,
    liftedIndex,
    onHide,
    performSave,
  ]);

  if (parsed.chapter === null) {
    return (
      <div className="error-block" role="alert">
        <span className="error-block__code">invalid_sentence_id</span>
        <strong>Sentence ID {parsed.raw || "(empty)"} is malformed.</strong>
      </div>
    );
  }
  if (parallelQuery.isError && parallelQuery.error instanceof ApiError && parallelQuery.error.status === 404) {
    return (
      <div className="error-block" role="alert">
        <span className="error-block__code">sentence_not_found</span>
        <strong>
          Sentence <code>{parsed.raw}</code> is not in the current fixture set.
        </strong>
      </div>
    );
  }
  if (!parallelQuery.data || !rankingQuery.data) {
    return (
      <div className="rank-page">
        <ChapterNav chapter={parsed.chapter} />
        <div className="empty-block">Loading rank surface…</div>
      </div>
    );
  }

  const onDiscardConflict = (): void => {
    if (!conflict) return;
    setDraftEntries(conflict.current_entries);
    setDraftNotes(conflict.current_notes ?? "");
    setServerVersion(conflict.current_version);
    setConflict(null);
  };

  const onReapplyConflict = (): void => {
    if (!conflict) return;
    setServerVersion(conflict.current_version);
    setConflict(null);
  };

  return (
    <div className="rank-page" data-testid="rank-page">
      <ChapterNav chapter={parsed.chapter} />
      <ReferenceStrip parallel={parallelQuery.data} />
      <div className="rank-page__body">
        <CandidateList
          entries={draftEntries}
          cardLookup={cardLookup}
          focusedIndex={focusedIndex}
          onFocusIndex={setFocusedIndex}
          onChange={setDraftEntries}
          onHide={(card) => void onHide(card)}
        />
        <div
          className="rank-page__sidebar"
          onFocus={() => {
            notesFocusedRef.current = true;
          }}
          onBlur={() => {
            notesFocusedRef.current = false;
          }}
        >
          <NotesField
            value={draftNotes}
            onChange={setDraftNotes}
            onFocus={() => {
              notesFocusedRef.current = true;
            }}
            onBlur={() => {
              notesFocusedRef.current = false;
            }}
          />
          <button
            type="button"
            className="rank-help-btn"
            onClick={() => setHelpOpen(true)}
            data-testid="rank-help-btn"
            aria-label="Show keyboard shortcuts"
          >
            ? shortcuts
          </button>
          {liftedIndex !== null ? (
            <div className="rank-page__lifted-banner" role="status">
              Card lifted — use ↑/↓ to move; space to drop.
            </div>
          ) : null}
        </div>
      </div>
      <SaveBar
        isSaving={saveMutation.isPending}
        isDirty={isDirty}
        lastSavedAt={lastSavedAt}
        hasPrev={prevSentenceId !== null}
        hasNext={nextSentenceId !== null}
        onSave={() => void performSave("stay")}
        onSaveAndNext={() => void performSave("next")}
        onPrev={() => void performSave("prev")}
      />
      {conflict ? (
        <ConflictModal
          payload={conflict}
          onDiscardMyChanges={onDiscardConflict}
          onReapplyOverServer={onReapplyConflict}
          onDismiss={() => setConflict(null)}
        />
      ) : null}
      {helpOpen ? <KeyboardHelp onClose={() => setHelpOpen(false)} /> : null}
    </div>
  );
}
