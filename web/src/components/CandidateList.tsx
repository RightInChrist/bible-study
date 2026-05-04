/**
 * Display of stored Claude candidates for one sentence (Slice 3a).
 *
 * Read-only — full ranking UI ships later. Each candidate shows the
 * five identity fields (Designer Flow 4 step 6: "shown verbatim in any
 * UI that displays the candidate") plus the candidate text.
 *
 * Rendered alongside the parallel reader cell, NOT inside `<AuthOnly>`:
 * candidates are part of the read surface (the static site shows them
 * too) — only authoring affordances (Generate button, range editor,
 * rank UI) are stripped at static build time.
 */
import { useSentenceCandidates } from "../api/hooks";
import type { CandidateResponse } from "../api/types";

interface Props {
  sentenceId: string;
}

export function CandidateList({ sentenceId }: Props) {
  const { data, isLoading, isError } = useSentenceCandidates(sentenceId);
  if (isLoading) {
    return null;
  }
  if (isError) {
    return null;
  }
  const candidates = data?.candidates ?? [];
  if (candidates.length === 0) {
    return null;
  }
  return (
    <div className="candidate-list" data-testid="candidate-list">
      <div className="candidate-list__header">
        Claude candidates ({candidates.length})
      </div>
      {candidates.map((candidate) => (
        <CandidateRow key={candidate.candidate_id} candidate={candidate} />
      ))}
    </div>
  );
}

function CandidateRow({ candidate }: { candidate: CandidateResponse }) {
  const provenance = `claude · ${candidate.style_prompt_version} · ${candidate.source_set_id} · ${candidate.model} · ${candidate.generated_at}`;
  return (
    <div className="candidate-list__row" data-testid="candidate-row">
      <div className="candidate-list__text">{candidate.candidate_text}</div>
      <div className="candidate-list__provenance" title={provenance}>
        {provenance}
      </div>
    </div>
  );
}
