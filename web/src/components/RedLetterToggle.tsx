/**
 * Inline "Mark / Unmark as red letter" affordance for a single sentence
 * (Slice 7 — JTBD #2). Verse-level only — Designer Flow 3's full
 * word-level UX is deferred per the slice scope.
 *
 * For not-yet-red sentences: a "Mark as red letter" button opens a
 * compact form to confirm the verse range (defaults to the sentence's
 * own verse span). Submission posts to `/api/v1/red-letter/mark` and
 * invalidates the sentence-list / parallel / chapter-overlays caches.
 *
 * For red-letter sentences: an "Unmark" button reads the chapter
 * overlays endpoint to find the head's `version` (the OCC token) and
 * the chain identity to act on. The unmark posts a `rejected=1` leaf
 * via the chain-leaf OCC contract.
 *
 * Wrapped at the call site by `<AuthOnly>` so the static bundle strips it.
 */
import { useState } from "react";

import {
  useChapterOverlays,
  useMarkRedLetter,
  useSentenceParallel,
  useUnmarkRedLetter,
} from "../api/hooks";
import type {
  Overlay,
  OverlayChain,
  SentenceListItem,
} from "../api/types";

interface Props {
  sentence: SentenceListItem;
}

export function RedLetterToggle({ sentence }: Props) {
  if (sentence.is_red_letter) {
    return <UnmarkButton sentence={sentence} />;
  }
  return <MarkForm sentence={sentence} />;
}

function MarkForm({ sentence }: { sentence: SentenceListItem }) {
  const [open, setOpen] = useState(false);
  const [startVerse, setStartVerse] = useState(sentence.start_verse);
  const [endVerse, setEndVerse] = useState(sentence.end_verse);
  const mark = useMarkRedLetter();

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    mark.mutate(
      {
        chapter: sentence.chapter,
        start_verse: startVerse,
        end_verse: endVerse,
      },
      {
        onSuccess: () => setOpen(false),
      },
    );
  };

  if (!open) {
    return (
      <div className="red-letter-toggle" data-testid="red-letter-toggle">
        <button
          type="button"
          className="red-letter-toggle__button"
          data-testid="mark-red-letter-button"
          onClick={() => setOpen(true)}
        >
          Mark as red letter
        </button>
      </div>
    );
  }
  return (
    <form
      onSubmit={submit}
      className="red-letter-toggle red-letter-toggle--form"
      data-testid="mark-red-letter-form"
    >
      <span className="red-letter-toggle__label">
        Mark verses {sentence.chapter}:
      </span>
      <input
        type="number"
        min={1}
        value={startVerse}
        onChange={(e) => setStartVerse(Number.parseInt(e.target.value, 10) || 1)}
        aria-label="start verse"
        data-testid="mark-start-verse"
        className="red-letter-toggle__input"
      />
      <span aria-hidden="true">–</span>
      <input
        type="number"
        min={1}
        value={endVerse}
        onChange={(e) => setEndVerse(Number.parseInt(e.target.value, 10) || 1)}
        aria-label="end verse"
        data-testid="mark-end-verse"
        className="red-letter-toggle__input"
      />
      <button
        type="submit"
        className="red-letter-toggle__button"
        data-testid="mark-submit"
        disabled={mark.isPending}
      >
        {mark.isPending ? "Marking…" : "Mark"}
      </button>
      <button
        type="button"
        className="red-letter-toggle__button red-letter-toggle__button--secondary"
        onClick={() => setOpen(false)}
        data-testid="mark-cancel"
      >
        Cancel
      </button>
      {mark.isError ? (
        <span className="red-letter-toggle__error" data-testid="mark-error">
          {mark.error?.message ?? "mark failed"}
        </span>
      ) : null}
    </form>
  );
}

function findChainForSentence(
  chains: OverlayChain[],
  sentenceId: string,
  headOverlayId: number | null,
  sourceRangeId: number | null,
): OverlayChain | null {
  if (headOverlayId !== null && headOverlayId !== 0) {
    const byHead = chains.find((c) => c.head.overlay_id === headOverlayId);
    if (byHead) return byHead;
  }
  if (sourceRangeId !== null) {
    const bySource = chains.find((c) => c.source_range_id === sourceRangeId);
    if (bySource) return bySource;
  }
  // Last resort — find any chain whose head bounds contain the sentence.
  return (
    chains.find((c) => isSentenceInChainBounds(c.head, sentenceId)) ?? null
  );
}

function isSentenceInChainBounds(head: Overlay, sentenceId: string): boolean {
  if (head.start_sentence_id == null || head.end_sentence_id == null) return false;
  return (
    head.start_sentence_id === sentenceId || head.end_sentence_id === sentenceId
  );
}

function UnmarkButton({ sentence }: { sentence: SentenceListItem }) {
  const parallel = useSentenceParallel(sentence.sentence_id);
  const overlays = useChapterOverlays(sentence.chapter);
  const [confirming, setConfirming] = useState(false);
  const unmark = useUnmarkRedLetter();

  const provenance = parallel.data?.red_letter_provenance ?? null;
  const headOverlayId = provenance?.head_overlay_id ?? null;
  const sourceRangeId = provenance?.source_range_id ?? null;

  const chain =
    overlays.data && provenance
      ? findChainForSentence(
          overlays.data.chains,
          sentence.sentence_id,
          headOverlayId,
          sourceRangeId,
        )
      : null;

  const onConfirm = (): void => {
    if (chain === null) return;
    // Synthetic chains for un-edited Berean source ranges have head.overlay_id=0
    // and head.version=0; the API treats If-Match=0 as "no chain yet, insert
    // first leaf with parent_overlay_id=NULL".
    if (chain.head.overlay_id === 0) {
      const sid = chain.source_range_id;
      if (sid == null) return;
      unmark.mutate(
        {
          body: { scope: "source_range", target_id: sid },
          version: 0,
          chapter: sentence.chapter,
        },
        { onSuccess: () => setConfirming(false) },
      );
      return;
    }
    unmark.mutate(
      {
        body: { scope: "manual_overlay", target_id: chain.head.overlay_id },
        version: chain.head.version,
        chapter: sentence.chapter,
      },
      { onSuccess: () => setConfirming(false) },
    );
  };

  if (!confirming) {
    return (
      <div className="red-letter-toggle" data-testid="red-letter-toggle">
        <button
          type="button"
          className="red-letter-toggle__button"
          data-testid="unmark-red-letter-button"
          onClick={() => setConfirming(true)}
          disabled={parallel.isLoading}
        >
          Unmark
        </button>
      </div>
    );
  }
  return (
    <div
      className="red-letter-toggle red-letter-toggle--form"
      data-testid="unmark-confirm"
    >
      <span className="red-letter-toggle__label">
        Reject the {provenance?.origin ?? "current"} red-letter range?
      </span>
      <button
        type="button"
        className="red-letter-toggle__button"
        data-testid="unmark-confirm-button"
        onClick={onConfirm}
        disabled={unmark.isPending || chain === null}
      >
        {unmark.isPending ? "Unmarking…" : "Confirm"}
      </button>
      <button
        type="button"
        className="red-letter-toggle__button red-letter-toggle__button--secondary"
        onClick={() => setConfirming(false)}
        data-testid="unmark-cancel"
      >
        Cancel
      </button>
      {unmark.isError ? (
        <span className="red-letter-toggle__error" data-testid="unmark-error">
          {unmark.error?.message ?? "unmark failed"}
        </span>
      ) : null}
    </div>
  );
}
