/**
 * Display of stored Claude candidates for one sentence (Slice 3a-redo).
 *
 * Read-only — full ranking UI ships later. Each candidate shows the
 * five identity fields (Designer Flow 4 step 6: "shown verbatim in any
 * UI that displays the candidate") plus the candidate text.
 *
 * Structured-JSON candidates (e.g. ``first-century-jewish-v1``) are
 * rendered as a structured view with the cultural-grounding fields
 * surfaced as their own labelled sections. Plain-text candidates
 * (literal/dynamic/plainspoken) keep the simple rendering. We
 * discriminate via ``JSON.parse``-and-shape-check.
 *
 * Rendered alongside the parallel reader cell, NOT inside `<AuthOnly>`:
 * candidates are part of the read surface (the static site shows them
 * too) — only authoring affordances (Generate button, range editor,
 * rank UI) are stripped at static build time.
 */
import { useSentenceCandidates } from "../api/hooks";
import type {
  CandidateResponse,
  FirstCenturyJewishCandidate,
} from "../api/types";

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

function tryParseStructured(
  text: string,
): FirstCenturyJewishCandidate | null {
  try {
    const parsed = JSON.parse(text) as unknown;
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      "english" in parsed &&
      typeof (parsed as { english: unknown }).english === "string"
    ) {
      return parsed as FirstCenturyJewishCandidate;
    }
  } catch {
    return null;
  }
  return null;
}

function CandidateRow({ candidate }: { candidate: CandidateResponse }) {
  const provenance = `claude · ${candidate.style_prompt_version} · ${candidate.source_set_id} · ${candidate.model} · ${candidate.generated_at}`;
  const structured = tryParseStructured(candidate.candidate_text);
  return (
    <div className="candidate-list__row" data-testid="candidate-row">
      {structured ? (
        <StructuredCandidate candidate={structured} />
      ) : (
        <div className="candidate-list__text">{candidate.candidate_text}</div>
      )}
      <div className="candidate-list__provenance" title={provenance}>
        {provenance}
      </div>
    </div>
  );
}

function StructuredCandidate({
  candidate,
}: {
  candidate: FirstCenturyJewishCandidate;
}) {
  return (
    <div
      className="candidate-list__structured"
      data-testid="candidate-structured"
    >
      <div className="candidate-list__english">{candidate.english}</div>
      {candidate.underlying_hypothesis ? (
        <Section
          label="Underlying-language hypothesis"
          testId="candidate-structured__underlying"
        >
          {candidate.underlying_hypothesis}
        </Section>
      ) : null}
      {candidate.cultural_notes ? (
        <Section label="Cultural notes" testId="candidate-structured__cultural">
          {candidate.cultural_notes}
        </Section>
      ) : null}
      {candidate.intertexts && candidate.intertexts.length > 0 ? (
        <Section label="Intertexts" testId="candidate-structured__intertexts">
          <ul>
            {candidate.intertexts.map((it, i) => (
              <li key={i}>
                {it.reference ? <strong>{it.reference}</strong> : null}
                {it.type ? ` (${it.type})` : null}
                {it.note ? ` — ${it.note}` : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
      {candidate.audience ? (
        <Section label="Audience" testId="candidate-structured__audience">
          {candidate.audience}
        </Section>
      ) : null}
      {candidate.pragmatic_act ? (
        <Section
          label="Pragmatic act"
          testId="candidate-structured__pragmatic"
        >
          {candidate.pragmatic_act}
        </Section>
      ) : null}
      {candidate.confidence ? (
        <Section
          label="Confidence"
          testId="candidate-structured__confidence"
        >
          {candidate.confidence}
        </Section>
      ) : null}
    </div>
  );
}

interface SectionProps {
  label: string;
  testId: string;
  children: React.ReactNode;
}

function Section({ label, testId, children }: SectionProps) {
  return (
    <div className="candidate-list__section" data-testid={testId}>
      <div className="candidate-list__section-label">{label}</div>
      <div className="candidate-list__section-body">{children}</div>
    </div>
  );
}
