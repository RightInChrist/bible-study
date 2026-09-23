/**
 * Coverage block at the top of the GSV page (Designer Flow 6 step 2).
 *
 * Reads the chapter's slice from `GET /api/v1/gsv/coverage` rather than
 * the per-chapter response so the line stays accurate even when a tie
 * blocks the chapter response from rendering. The chapter response also
 * carries coverage, but using the dedicated endpoint keeps the
 * indicator decoupled from the format toggle.
 */
import type { GsvCoverageResponse } from "../../api/types";

interface Props {
  chapter: number;
  coverage: GsvCoverageResponse | undefined;
  totalSentencesInChapter: number | null;
  rankedRedLetterInChapter: number;
  totalRedLetterInChapter: number;
}

export function CoverageIndicator({
  chapter,
  coverage,
  totalSentencesInChapter,
  rankedRedLetterInChapter,
  totalRedLetterInChapter,
}: Props) {
  const ranked = rankedRedLetterInChapter;
  const total = totalRedLetterInChapter;
  const totalSentences = totalSentencesInChapter ?? 0;
  const unrankedRedLetter = Math.max(0, total - ranked);
  const bsbDefault = totalSentences - total;
  return (
    <div
      className="gsv-coverage"
      data-testid="gsv-coverage"
      role="status"
      aria-label="GSV coverage indicator"
    >
      <strong>Matthew {chapter}</strong> — {ranked} / {total} red-letter
      sentences ranked, {totalSentences} / {totalSentences} sentences total in
      GSV ({bsbDefault} BSB-default, {ranked} ranked,{" "}
      {unrankedRedLetter} unranked-red)
      {coverage ? (
        <span className="gsv-coverage__repo">
          {" "}
          · repo: {coverage.ranked_sentences} / {coverage.total_red_letter_sentences}
        </span>
      ) : null}
    </div>
  );
}
