/**
 * One sentence row in the parallel reader (Designer Flow 1 step 2).
 *
 * Columns: SBLGNT (canonical), Byzantine, BSB, BLB, WEB, plus the BIB
 * interlinear stretched across the row beneath. Red-letter sentences get
 * a left-edge red rule (Flow 1 step 4); `⚓` shows when the sentence
 * starts on a verse boundary (Flow 1 step 3).
 *
 * Each row fetches its own parallel data via `useSentenceParallel`. Why
 * fetch per row instead of one chapter-wide endpoint: keeps the
 * read-side API minimal in v1 (we use only the two endpoints already
 * shipped) and TanStack Query caches across navigation. Performance is
 * fine for a 100-sentence chapter; if it ever gets cramped we add a
 * batch endpoint without rewriting the components.
 */
import { forwardRef } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import type { SentenceListItem, SentenceParallelResponse } from "../api/types";
import { useRanking, useSentenceParallel } from "../api/hooks";
import type { FocalSentence, RefVerse } from "../lib/rendering";
import { showsAnchorGlyph } from "../lib/rendering";
import { AuthOnly } from "./AuthOnly";
import { BibInterlinear } from "./BibInterlinear";
import { CandidateList } from "./CandidateList";
import { GenerateButton } from "./GenerateButton";
import { RedLetterToggle } from "./RedLetterToggle";
import { TranslationCell } from "./TranslationCell";

interface Props {
  sentence: SentenceListItem;
  focused: boolean;
  onFocus(): void;
}

function verseRangeLabel(s: SentenceListItem): string {
  if (s.start_verse === s.end_verse) {
    return `${s.chapter}:${s.start_verse}`;
  }
  return `${s.chapter}:${s.start_verse}–${s.end_verse}`;
}

function focalFromParallel(p: SentenceParallelResponse): FocalSentence {
  return {
    start_chapter: p.start_chapter,
    start_verse: p.start_verse,
    end_chapter: p.end_chapter,
    end_verse: p.end_verse,
    starts_at_verse_boundary: p.starts_at_verse_boundary,
    ends_at_verse_boundary: p.ends_at_verse_boundary,
  };
}

function focalFromList(s: SentenceListItem): FocalSentence {
  return {
    start_chapter: s.chapter,
    start_verse: s.start_verse,
    end_chapter: s.chapter,
    end_verse: s.end_verse,
    starts_at_verse_boundary: s.starts_at_verse_boundary,
    ends_at_verse_boundary: s.ends_at_verse_boundary,
  };
}

function pickEnglish(
  parallel: SentenceParallelResponse | undefined,
  name: "BSB" | "BLB" | "WEB",
): readonly RefVerse[] {
  if (!parallel) return [];
  const col = parallel.english.find((c) => c.translation === name);
  return col ? col.verses : [];
}

export const SentenceRow = forwardRef<HTMLDivElement, Props>(function SentenceRow(
  { sentence, focused, onFocus },
  ref,
) {
  const queryClient = useQueryClient();
  const { data: parallel, isLoading, isError } = useSentenceParallel(sentence.sentence_id);
  const focal = parallel ? focalFromParallel(parallel) : focalFromList(sentence);

  const refreshCandidates = (): void => {
    queryClient.invalidateQueries({
      queryKey: ["sentences", "candidates", sentence.sentence_id],
    });
  };
  const sblgntText = parallel?.text_sblgnt ?? sentence.text_preview;
  const showAnchor = showsAnchorGlyph(focal);

  return (
    <div
      ref={ref}
      className="sentence-row"
      data-red-letter={sentence.is_red_letter ? "true" : "false"}
      data-focused={focused ? "true" : "false"}
      data-sentence-id={sentence.sentence_id}
      tabIndex={0}
      onFocus={onFocus}
      role="article"
      aria-label={`Sentence ${sentence.sentence_id} ${verseRangeLabel(sentence)}`}
    >
      <div className="sentence-row__meta">
        <span className="sentence-row__verse">
          {verseRangeLabel(sentence)}
          {showAnchor ? (
            <span
              className="sentence-row__anchor"
              title="starts at verse boundary"
              aria-label="starts at verse boundary"
            >
              ⚓
            </span>
          ) : null}
        </span>
        <span className="sentence-row__id" data-testid="sentence-id">
          {sentence.sentence_id}
        </span>
        <span>
          {sentence.word_count} words
          {sentence.is_red_letter ? " · red" : ""}
        </span>
      </div>

      <div className="sentence-row__cell sentence-row__greek">
        <span className="sentence-row__cell-header">SBLGNT</span>
        {sblgntText}
      </div>

      <TranslationCell
        label="Byzantine"
        focal={focal}
        verses={parallel?.byzantine ?? []}
        caveat="R2-byzantine"
        greek
      />
      <TranslationCell
        label="BSB"
        focal={focal}
        verses={pickEnglish(parallel, "BSB")}
        caveat="R3-english"
      />
      <TranslationCell
        label="BLB"
        focal={focal}
        verses={pickEnglish(parallel, "BLB")}
        caveat="R3-english"
      />
      <TranslationCell
        label="WEB"
        focal={focal}
        verses={pickEnglish(parallel, "WEB")}
        caveat="R3-english"
      />

      {parallel ? <BibInterlinear words={parallel.bib_interlinear} /> : null}
      {isLoading && !parallel ? (
        <div className="bib-interlinear">
          <span className="sentence-row__cell-header">loading reference text…</span>
        </div>
      ) : null}
      {isError ? (
        <div className="bib-interlinear">
          <span className="sentence-row__cell-header" style={{ color: "#a04040" }}>
            failed to load reference text
          </span>
        </div>
      ) : null}

      <GenerateButton sentenceId={sentence.sentence_id} onCompleted={refreshCandidates} />
      <AuthOnly>
        <RankAffordance sentenceId={sentence.sentence_id} />
      </AuthOnly>
      <AuthOnly>
        <RedLetterToggle sentence={sentence} />
      </AuthOnly>
      <CandidateList sentenceId={sentence.sentence_id} />
    </div>
  );
});

function RankAffordance({ sentenceId }: { sentenceId: string }) {
  const ranking = useRanking(sentenceId);
  const hasSavedRanking =
    ranking.data !== undefined && ranking.data.version > 0;
  return (
    <div className="rank-affordance" data-testid="rank-affordance">
      <Link
        to={`/sentence/${sentenceId}/rank`}
        className="rank-affordance__link"
        data-testid="rank-affordance__link"
      >
        Rank →
      </Link>
      {hasSavedRanking ? (
        <span
          className="rank-affordance__badge"
          data-testid="rank-affordance__badge"
          title="this sentence has a saved ranking"
        >
          ranked
        </span>
      ) : null}
    </div>
  );
}
