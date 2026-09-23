/**
 * Render one ChapterSummaryResponse with all structured fields surfaced
 * as labelled sections (Slice 8).
 *
 * Empty / null fields are gracefully omitted — the UI doesn't show
 * dangling labels. The `candidate_ids_consulted` footer is rendered
 * mono so the integers stay readable.
 */
import { Link } from "react-router-dom";

import type {
  ChapterSummaryIntertext,
  ChapterSummaryKeySentence,
  ChapterSummaryResponse,
} from "../../api/types";

interface Props {
  summary: ChapterSummaryResponse;
}

export function ChapterSummaryDisplay({ summary }: Props) {
  const s = summary.summary;
  const intertexts = s.key_intertexts ?? [];
  const keySentences = s.key_sentences ?? [];
  const consulted = summary.candidate_ids_consulted ?? [];
  return (
    <article
      className="chapter-summary"
      data-testid="chapter-summary"
      data-summary-id={summary.summary_id}
    >
      {s.summary ? (
        <p
          className="chapter-summary__overview"
          data-testid="chapter-summary__overview"
        >
          {s.summary}
        </p>
      ) : null}

      {s.narrative_arc ? (
        <Section
          label="Narrative arc"
          testId="chapter-summary__narrative-arc"
          body={s.narrative_arc}
        />
      ) : null}
      {s.audience_dynamics ? (
        <Section
          label="Audience dynamics"
          testId="chapter-summary__audience-dynamics"
          body={s.audience_dynamics}
        />
      ) : null}
      {s.cultural_throughline ? (
        <Section
          label="Cultural throughline"
          testId="chapter-summary__cultural-throughline"
          body={s.cultural_throughline}
        />
      ) : null}
      {s.rhetorical_strategy ? (
        <Section
          label="Rhetorical strategy"
          testId="chapter-summary__rhetorical-strategy"
          body={s.rhetorical_strategy}
        />
      ) : null}
      {s.pragmatic_arc ? (
        <Section
          label="Pragmatic arc"
          testId="chapter-summary__pragmatic-arc"
          body={s.pragmatic_arc}
        />
      ) : null}

      {intertexts.length > 0 ? (
        <div
          className="chapter-summary__section"
          data-testid="chapter-summary__intertexts"
        >
          <div className="chapter-summary__label">Key intertexts</div>
          <ul>
            {intertexts.map((it: ChapterSummaryIntertext, i: number) => (
              <li key={i}>
                {it.reference ? <strong>{it.reference}</strong> : null}
                {it.type ? ` (${it.type})` : null}
                {it.note ? ` — ${it.note}` : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {keySentences.length > 0 ? (
        <div
          className="chapter-summary__section"
          data-testid="chapter-summary__key-sentences"
        >
          <div className="chapter-summary__label">Key sentences</div>
          <ul>
            {keySentences.map((ks: ChapterSummaryKeySentence, i: number) => (
              <li key={i}>
                {ks.sentence_id ? (
                  <Link to={`/sentence/${ks.sentence_id}`}>
                    {ks.verse_range ?? ks.sentence_id}
                  </Link>
                ) : (
                  <span>{ks.verse_range ?? "(no reference)"}</span>
                )}
                {ks.why_pivotal ? <> — {ks.why_pivotal}</> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {s.open_questions ? (
        <Section
          label="Open questions"
          testId="chapter-summary__open-questions"
          body={s.open_questions}
        />
      ) : null}

      <footer className="chapter-summary__provenance" data-testid="chapter-summary__provenance">
        <div>
          {summary.model} · {summary.prompt_version} · {summary.generated_at}
        </div>
        <div className="chapter-summary__consulted">
          candidates consulted ({consulted.length}):{" "}
          <code>{consulted.join(", ") || "—"}</code>
        </div>
      </footer>
    </article>
  );
}

interface SectionProps {
  label: string;
  testId: string;
  body: string;
}

function Section({ label, testId, body }: SectionProps) {
  return (
    <div className="chapter-summary__section" data-testid={testId}>
      <div className="chapter-summary__label">{label}</div>
      <div className="chapter-summary__body">{body}</div>
    </div>
  );
}
