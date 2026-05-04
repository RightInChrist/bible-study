/**
 * Parallel reader (Designer Flow 1).
 *
 * Renders the chapter as a vertical list of sentence rows. Between
 * adjacent rows the `↔` divergence glyph appears when SBLGNT and
 * Byzantine boundaries diverge (SPEC.md §Edge cases). Keyboard
 * shortcuts: `j` / `k` (next / prev sentence), `[` / `]` (chapter prev /
 * next). Shortcuts are disabled while focus is in an input / textarea
 * per Designer's global scoping rule.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useSentencesByChapter } from "../api/hooks";
import { ApiError } from "../api/client";
import type { SentenceListItem } from "../api/types";
import { showsDivergenceGlyph } from "../lib/divergence";
import { SentenceRow } from "./SentenceRow";

interface Props {
  chapter: number;
  /** Optional sentence to focus initially (deep-link from /sentence/:id). */
  initialSentenceId?: string;
}

function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}

export function ParallelReader({ chapter, initialSentenceId }: Props) {
  const { data, isLoading, isError, error } = useSentencesByChapter(chapter);
  const navigate = useNavigate();
  const [focusedId, setFocusedId] = useState<string | null>(initialSentenceId ?? null);
  const rowRefs = useRef(new Map<string, HTMLDivElement | null>());

  const sentences = useMemo<SentenceListItem[]>(() => data?.sentences ?? [], [data]);

  // Default focus to the first sentence when chapter loads (only if no
  // explicit deep-link sentence was provided).
  useEffect(() => {
    if (focusedId !== null) return;
    if (sentences.length > 0) {
      setFocusedId(sentences[0].sentence_id);
    }
  }, [sentences, focusedId]);

  // Scroll the deep-linked sentence into view once its row is mounted.
  useEffect(() => {
    if (!initialSentenceId) return;
    const el = rowRefs.current.get(initialSentenceId);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "auto" });
      el.focus({ preventScroll: true });
    }
  }, [initialSentenceId, sentences]);

  const moveFocus = useCallback(
    (delta: 1 | -1) => {
      if (sentences.length === 0) return;
      const currentIndex = focusedId
        ? sentences.findIndex((s) => s.sentence_id === focusedId)
        : -1;
      const fallback = delta === 1 ? 0 : sentences.length - 1;
      const baseIndex = currentIndex >= 0 ? currentIndex : fallback;
      const nextIndex = Math.min(
        sentences.length - 1,
        Math.max(0, baseIndex + delta),
      );
      const nextSentence = sentences[nextIndex];
      if (!nextSentence) return;
      setFocusedId(nextSentence.sentence_id);
      const el = rowRefs.current.get(nextSentence.sentence_id);
      if (el) {
        el.focus({ preventScroll: false });
        el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    },
    [focusedId, sentences],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isEditableTarget(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      switch (e.key) {
        case "j":
          e.preventDefault();
          moveFocus(1);
          break;
        case "k":
          e.preventDefault();
          moveFocus(-1);
          break;
        case "]":
          if (chapter < 28) {
            e.preventDefault();
            navigate(`/chapter/${chapter + 1}`);
          }
          break;
        case "[":
          if (chapter > 1) {
            e.preventDefault();
            navigate(`/chapter/${chapter - 1}`);
          }
          break;
        default:
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chapter, moveFocus, navigate]);

  if (isLoading) {
    return (
      <div data-testid="loading-skeleton">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton-row" aria-hidden="true" />
        ))}
        <span className="shortcut-help">loading Matthew {chapter}…</span>
      </div>
    );
  }

  if (isError) {
    const apiErr = error instanceof ApiError ? error.body : null;
    return (
      <div className="error-block" role="alert">
        <span className="error-block__code">{apiErr?.code ?? "network_error"}</span>
        <strong>Couldn't load chapter {chapter}.</strong>
        <p>
          {apiErr?.message ??
            "The local API may be offline. Run `make dev-api` (or `make dev`) and reload."}
        </p>
      </div>
    );
  }

  if (sentences.length === 0) {
    return (
      <div className="empty-block">
        No sentences for chapter {chapter} — the imported fixture covers Matthew 1–28.
      </div>
    );
  }

  const setRowRef = (sentenceId: string) => (el: HTMLDivElement | null) => {
    if (el) {
      rowRefs.current.set(sentenceId, el);
    } else {
      rowRefs.current.delete(sentenceId);
    }
  };

  return (
    <div data-testid="parallel-reader">
      <h2 className="chapter-heading">Matthew {chapter}</h2>
      <div className="shortcut-help">
        <span className="kbd">j</span>/<span className="kbd">k</span> next/prev sentence ·{" "}
        <span className="kbd">[</span>/<span className="kbd">]</span> prev/next chapter
      </div>
      {sentences.map((s, i) => {
        const previous = i > 0 ? sentences[i - 1] : null;
        const showsGlyph = previous !== null && showsDivergenceGlyph(previous, s);
        return (
          <div key={s.sentence_id}>
            {showsGlyph ? (
              <div
                className="divergence-glyph"
                role="note"
                title="boundaries diverge — Byzantine is verse-keyed; sentence split is approximate"
                data-testid="divergence-glyph"
              >
                <span aria-hidden="true">↔</span>
                <span className="divergence-glyph__caption">boundaries diverge</span>
              </div>
            ) : null}
            <SentenceRow
              ref={setRowRef(s.sentence_id)}
              sentence={s}
              focused={focusedId === s.sentence_id}
              onFocus={() => setFocusedId(s.sentence_id)}
            />
          </div>
        );
      })}
    </div>
  );
}
