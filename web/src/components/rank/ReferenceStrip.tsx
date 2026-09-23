/**
 * Sticky top reference strip (Designer Flow 5 step 1, post-Phase-4 review).
 *
 * Renders SBLGNT (always) + Byzantine (verse-keyed) for the focal
 * sentence. Reference rendering rules R1–R5 apply via `lib/rendering.ts`
 * — the rank surface in particular MUST not silently leak adjacent-
 * sentence Byzantine words into Gavin's ranking decision.
 */
import type { SentenceParallelResponse } from "../../api/types";
import {
  showsAnchorGlyph,
  type FocalSentence,
} from "../../lib/rendering";
import { TranslationCell } from "../TranslationCell";

interface Props {
  parallel: SentenceParallelResponse;
}

function verseRangeLabel(p: SentenceParallelResponse): string {
  if (p.start_chapter === p.end_chapter && p.start_verse === p.end_verse) {
    return `${p.start_chapter}:${p.start_verse}`;
  }
  if (p.start_chapter === p.end_chapter) {
    return `${p.start_chapter}:${p.start_verse}-${p.end_verse}`;
  }
  return `${p.start_chapter}:${p.start_verse}-${p.end_chapter}:${p.end_verse}`;
}

export function ReferenceStrip({ parallel }: Props) {
  const focal: FocalSentence = {
    start_chapter: parallel.start_chapter,
    start_verse: parallel.start_verse,
    end_chapter: parallel.end_chapter,
    end_verse: parallel.end_verse,
    starts_at_verse_boundary: parallel.starts_at_verse_boundary,
    ends_at_verse_boundary: parallel.ends_at_verse_boundary,
  };
  const showAnchor = showsAnchorGlyph(focal);
  return (
    <div className="rank-reference" data-testid="rank-reference">
      <div className="rank-reference__meta">
        <strong className="rank-reference__verse">
          {verseRangeLabel(parallel)}
          {showAnchor ? (
            <span
              className="rank-reference__anchor"
              title="starts at verse boundary"
              aria-label="starts at verse boundary"
            >
              ⚓
            </span>
          ) : null}
        </strong>
        <span className="rank-reference__id">{parallel.sentence_id}</span>
        {parallel.is_red_letter ? (
          <span className="rank-reference__red" title="red-letter (Jesus speaking)">
            ● red
          </span>
        ) : null}
      </div>
      <div className="rank-reference__columns">
        <div className="rank-reference__cell sentence-row__greek">
          <span className="sentence-row__cell-header">SBLGNT</span>
          {parallel.text_sblgnt}
        </div>
        <TranslationCell
          label="Byzantine"
          focal={focal}
          verses={parallel.byzantine}
          caveat="R2-byzantine"
          greek
        />
      </div>
    </div>
  );
}
