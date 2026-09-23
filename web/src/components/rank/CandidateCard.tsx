/**
 * One ranked card on the rank page (Designer Flow 5).
 *
 * Shows: position number on the left, candidate text in the middle,
 * provenance footer, drag handle + actions on the right.
 *
 * Provenance:
 *   - translation kind → just the translation/edition name + verse range
 *   - claude kind → the five-tuple identity per Designer Flow 4 step 6
 *
 * Tied-with-above is rendered by the *parent* (CandidateList) since it
 * affects the position number; we just expose a toggle button here.
 */
import type {
  AvailableCandidate,
  CandidateRef,
  CandidateResponse,
  RankingEntry,
} from "../../api/types";

interface Props {
  entry: RankingEntry;
  card: AvailableCandidate;
  isFocused: boolean;
  onFocus(): void;
  onToggleTie(): void;
  onHide(): void;
  onDragStart(): void;
  onDragOver(e: React.DragEvent<HTMLDivElement>): void;
  onDrop(): void;
  onDragEnd(): void;
}

function refLabel(ref: CandidateRef): string {
  if (ref.kind === "translation") {
    return `${ref.name} · ${ref.verse_range}`;
  }
  return `claude · candidate ${ref.candidate_id}`;
}

function ClaudeProvenance({ candidate }: { candidate: CandidateResponse }) {
  const provenance = `claude · ${candidate.style_prompt_version} · ${candidate.source_set_id} · ${candidate.model} · ${candidate.generated_at}`;
  return (
    <div className="rank-card__provenance" title={provenance}>
      {provenance}
    </div>
  );
}

function cardText(card: AvailableCandidate): string {
  if (card.kind === "translation") {
    return card.text;
  }
  return card.candidate.candidate_text;
}

export function CandidateCard({
  entry,
  card,
  isFocused,
  onFocus,
  onToggleTie,
  onHide,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: Props) {
  const text = cardText(card);
  const positionLabel = entry.tied_with_above
    ? `=${entry.rank}`
    : String(entry.rank);
  return (
    <div
      className="rank-card"
      data-testid="rank-card"
      data-focused={isFocused ? "true" : "false"}
      data-position={entry.position}
      data-rank={entry.rank}
      data-tied-with-above={entry.tied_with_above ? "true" : "false"}
      data-candidate-kind={entry.candidate_ref.kind}
      tabIndex={0}
      onFocus={onFocus}
      role="article"
      aria-label={`Rank ${entry.rank}: ${refLabel(entry.candidate_ref)}`}
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
    >
      <div className="rank-card__position" aria-label={`rank ${entry.rank}`}>
        <span className="rank-card__drag-handle" aria-hidden="true" title="drag to reorder">
          ⋮⋮
        </span>
        <span className="rank-card__position-label">{positionLabel}</span>
      </div>
      <div className="rank-card__body">
        <div className="rank-card__text">{text}</div>
        {card.kind === "claude" ? (
          <ClaudeProvenance candidate={card.candidate} />
        ) : (
          <div className="rank-card__provenance">
            {card.name} · {card.verse_range}
          </div>
        )}
      </div>
      <div className="rank-card__actions">
        <button
          type="button"
          className="rank-card__action"
          data-testid="rank-card__tie"
          onClick={onToggleTie}
          aria-pressed={entry.tied_with_above}
          title="Toggle tied-with-above"
        >
          {entry.tied_with_above ? "untie" : "tie ↑"}
        </button>
        <button
          type="button"
          className="rank-card__action rank-card__action--hide"
          data-testid="rank-card__hide"
          onClick={onHide}
          disabled={card.kind === "translation"}
          title={
            card.kind === "translation"
              ? "Open-source candidates cannot be hidden"
              : "Hide this combo from this sentence"
          }
        >
          hide
        </button>
      </div>
    </div>
  );
}
