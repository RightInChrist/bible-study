/**
 * One column of reference text alongside a focal SBLGNT sentence.
 * Renders R1/R3 (English) or R1/R2 (Byzantine) — see `lib/rendering.ts`
 * for the rule statements.
 */
import {
  applyReferenceRules,
  shouldShowProjectionCaveat,
  type FocalSentence,
  type RefVerse,
  type Segment,
} from "../lib/rendering";

interface Props {
  label: string;
  focal: FocalSentence;
  verses: readonly RefVerse[];
  /** R2 fires for Byzantine; R3 fires for English. */
  caveat: "R2-byzantine" | "R3-english";
  greek?: boolean;
}

const CAVEAT_TEXT: Record<Props["caveat"], string> = {
  "R2-byzantine":
    "Byzantine is verse-keyed; the highlighted span is a best-effort word-range projection from the SBLGNT sentence boundary.",
  "R3-english":
    "English translations follow verse boundaries; the highlighted portion approximates this Greek sentence.",
};

function SegmentSpan({ segment }: { segment: Segment }) {
  return (
    <span
      className="segment"
      data-emphasis={segment.emphasis}
      title={segment.emphasis === "muted" ? "Muted: not part of this sentence" : undefined}
    >
      <span className="segment-verse-num">{segment.verse}</span>
      {segment.text}{" "}
    </span>
  );
}

export function TranslationCell({ label, focal, verses, caveat, greek }: Props) {
  const segments = applyReferenceRules(focal, verses);
  const showCaveat = shouldShowProjectionCaveat(focal);
  return (
    <div className={`sentence-row__cell${greek ? " sentence-row__greek" : ""}`}>
      <span className="sentence-row__cell-header">
        {label}
        {showCaveat ? (
          <span className="projection-caveat" title={CAVEAT_TEXT[caveat]} aria-label={CAVEAT_TEXT[caveat]}>
            ≈
          </span>
        ) : null}
      </span>
      {segments.length === 0 ? (
        <span className="segment" data-emphasis="muted">— no text —</span>
      ) : (
        segments.map((s) => (
          <SegmentSpan key={`${s.chapter}-${s.verse}`} segment={s} />
        ))
      )}
    </div>
  );
}
