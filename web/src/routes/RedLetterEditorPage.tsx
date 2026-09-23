/**
 * Red-letter range editor (Slice 7 — Designer Flow 3 surface).
 *
 * Slice scope: list every overlay chain touching a chapter; let Gavin
 * Mark a new range, Reject (unmark) an active chain, or Restore a
 * rejected one. Word-level split / merge / extend / retract are
 * deferred — verse-level scope is the v1 use case for curating Mt 8-28.
 *
 * Wrapped at the route level by `<AuthOnly>` so the static bundle
 * strips the entire editor + the write hooks it pulls in.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useChapterOverlays,
  useMarkRedLetter,
  useRestoreRedLetter,
  useUnmarkRedLetter,
} from "../api/hooks";
import type { Overlay, OverlayChain } from "../api/types";

const CHAPTER_MIN = 1;
const CHAPTER_MAX = 28;

function parseChapter(raw: string | undefined): number {
  if (!raw) return CHAPTER_MIN;
  const n = Number.parseInt(raw, 10);
  if (!Number.isInteger(n) || n < CHAPTER_MIN || n > CHAPTER_MAX) return CHAPTER_MIN;
  return n;
}

export function RedLetterEditorPage() {
  const { chapter: chapterParam } = useParams<{ chapter: string }>();
  const chapter = parseChapter(chapterParam);
  const overlays = useChapterOverlays(chapter);

  return (
    <div className="red-letter-editor" data-testid="red-letter-editor">
      <h1>Matthew {chapter} — red-letter range editor</h1>
      <MarkNewRangeForm chapter={chapter} />
      {overlays.isLoading ? <p>Loading overlay chains…</p> : null}
      {overlays.isError ? (
        <p className="red-letter-editor__error">
          Failed to load overlay chains: {overlays.error?.message}
        </p>
      ) : null}
      {overlays.data ? (
        <section className="red-letter-editor__chains" data-testid="chain-list">
          <h2>Chains touching this chapter ({overlays.data.chains.length})</h2>
          {overlays.data.chains.length === 0 ? (
            <p>No red-letter ranges touch chapter {chapter}.</p>
          ) : (
            <ul className="red-letter-editor__chain-list">
              {overlays.data.chains.map((chain) => (
                <ChainCard
                  key={chain.head.overlay_id || `src-${chain.source_range_id ?? "x"}`}
                  chain={chain}
                  chapter={chapter}
                />
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  );
}

function MarkNewRangeForm({ chapter }: { chapter: number }) {
  const [startVerse, setStartVerse] = useState(1);
  const [endVerse, setEndVerse] = useState(1);
  const mark = useMarkRedLetter();

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    mark.mutate({ chapter, start_verse: startVerse, end_verse: endVerse });
  };

  return (
    <form
      onSubmit={submit}
      className="red-letter-editor__mark-form"
      data-testid="mark-new-range-form"
    >
      <span>Mark new range — {chapter}:</span>
      <input
        type="number"
        min={1}
        value={startVerse}
        onChange={(e) => setStartVerse(Number.parseInt(e.target.value, 10) || 1)}
        aria-label="start verse"
        data-testid="mark-new-start-verse"
      />
      <span aria-hidden="true">–</span>
      <input
        type="number"
        min={1}
        value={endVerse}
        onChange={(e) => setEndVerse(Number.parseInt(e.target.value, 10) || 1)}
        aria-label="end verse"
        data-testid="mark-new-end-verse"
      />
      <button
        type="submit"
        disabled={mark.isPending}
        data-testid="mark-new-submit"
      >
        {mark.isPending ? "Marking…" : "Mark"}
      </button>
      {mark.isError ? (
        <span className="red-letter-editor__error" data-testid="mark-new-error">
          {mark.error?.message ?? "mark failed"}
        </span>
      ) : null}
    </form>
  );
}

function ChainCard({ chain, chapter }: { chain: OverlayChain; chapter: number }) {
  const [showHistory, setShowHistory] = useState(false);
  const unmark = useUnmarkRedLetter();
  const restore = useRestoreRedLetter();
  const head = chain.head;

  const onReject = (): void => {
    if (head.overlay_id === 0) {
      // Synthetic source-only head — first reject creates the chain.
      const sid = chain.source_range_id;
      if (sid == null) return;
      unmark.mutate({
        body: { scope: "source_range", target_id: sid },
        version: 0,
        chapter,
      });
      return;
    }
    unmark.mutate({
      body: { scope: "manual_overlay", target_id: head.overlay_id },
      version: head.version,
      chapter,
    });
  };

  const onRestore = (): void => {
    if (head.overlay_id === 0) return;
    restore.mutate({
      body: { scope: "manual_overlay", target_id: head.overlay_id },
      version: head.version,
      chapter,
    });
  };

  return (
    <li className="red-letter-editor__chain" data-testid="chain-card">
      <header className="red-letter-editor__chain-header">
        <span className="red-letter-editor__origin" data-testid="chain-origin">
          {head.origin}
        </span>
        <span data-testid="chain-operation">{head.operation}</span>
        <span data-testid="chain-rejected">
          {head.rejected ? "rejected" : "active"}
        </span>
        <span data-testid="chain-bounds">{describeBounds(head)}</span>
        <span className="red-letter-editor__chain-meta">
          v{head.version} · {head.created_at}
        </span>
      </header>
      <div className="red-letter-editor__chain-actions">
        {!head.rejected ? (
          <button
            type="button"
            onClick={onReject}
            disabled={unmark.isPending}
            data-testid="chain-reject"
          >
            {unmark.isPending ? "Rejecting…" : "Reject"}
          </button>
        ) : null}
        {head.rejected && head.overlay_id !== 0 ? (
          <button
            type="button"
            onClick={onRestore}
            disabled={restore.isPending}
            data-testid="chain-restore"
          >
            {restore.isPending ? "Restoring…" : "Restore"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => setShowHistory((s) => !s)}
          data-testid="chain-history-toggle"
        >
          {showHistory ? "Hide history" : "View history"}
        </button>
      </div>
      {unmark.isError ? (
        <p className="red-letter-editor__error" data-testid="chain-reject-error">
          {unmark.error?.message ?? "reject failed"}
        </p>
      ) : null}
      {restore.isError ? (
        <p className="red-letter-editor__error" data-testid="chain-restore-error">
          {restore.error?.message ?? "restore failed"}
        </p>
      ) : null}
      {showHistory && chain.history.length > 0 ? (
        <ol
          className="red-letter-editor__chain-history"
          data-testid="chain-history-list"
        >
          {chain.history.map((row) => (
            <li key={row.overlay_id} data-testid="chain-history-row">
              <span>v{row.version}</span>
              <span> · {row.operation}</span>
              <span> · {row.rejected ? "rejected" : "active"}</span>
              <span> · {row.created_at}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}

function describeBounds(head: Overlay): string {
  if (head.start_sentence_id == null || head.end_sentence_id == null) {
    return "(rejected — no bounds)";
  }
  if (head.start_sentence_id === head.end_sentence_id) {
    return head.start_sentence_id;
  }
  return `${head.start_sentence_id} → ${head.end_sentence_id}`;
}
