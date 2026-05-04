/**
 * BIB interlinear (per-word Greek + Strong's + English gloss) rendered
 * as an expandable sub-row of a sentence.
 *
 * Architect resolved BIB to interlinear-only data so it doesn't share
 * the flowing-text shape of the other columns. We give it its own row
 * inside the sentence box, collapsed by default, expandable per row.
 */
import { useState } from "react";

import type { BibInterlinearWord } from "../api/types";

interface Props {
  words: readonly BibInterlinearWord[];
}

export function BibInterlinear({ words }: Props) {
  const [open, setOpen] = useState(false);
  if (words.length === 0) {
    return null;
  }
  return (
    <div className="bib-interlinear">
      <div className="bib-interlinear__header">
        <span>BIB interlinear · {words.length} words</span>
        <button
          type="button"
          className="bib-interlinear__toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "hide" : "show"}
        </button>
      </div>
      {open ? (
        <div className="bib-interlinear__words">
          {words.map((w) => (
            <div
              key={`${w.chapter}-${w.verse}-${w.position}`}
              className="bib-word"
              title={`${w.transliteration}${w.inflected_meaning ? ` · ${w.inflected_meaning}` : ""}`}
            >
              <span className="bib-word__greek">{w.greek_form}</span>
              <span className="bib-word__strong">{w.strong_id}</span>
              <span className="bib-word__gloss">{w.english_gloss}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
