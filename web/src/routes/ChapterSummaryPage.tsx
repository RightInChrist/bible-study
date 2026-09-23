/**
 * Chapter-summary page (Slice 8 — Designer Flow 8).
 *
 * Reads:
 *   - GET /api/v1/chapters/{N}/summaries  (list, latest first)
 *
 * Surfaces:
 *   - Generate-summary button (write — gated by AuthOnly)
 *   - Each summary rendered with its structured fields, latest first
 */
import { Link, useParams } from "react-router-dom";

import { useChapterSummaries } from "../api/hooks";
import { ChapterNav } from "../components/ChapterNav";
import { ChapterSummaryDisplay } from "../components/chapter/ChapterSummaryDisplay";
import { GenerateSummaryButton } from "../components/chapter/GenerateSummaryButton";

function parseChapter(raw: string | undefined): number {
  if (!raw) return 1;
  const n = Number.parseInt(raw, 10);
  if (!Number.isInteger(n) || n < 1 || n > 28) return 1;
  return n;
}

export function ChapterSummaryPage() {
  const { chapter: chapterParam } = useParams<{ chapter: string }>();
  const chapter = parseChapter(chapterParam);
  const summariesQuery = useChapterSummaries(chapter);

  const summaries = summariesQuery.data?.summaries ?? [];

  return (
    <div className="chapter-summary-page" data-testid="chapter-summary-page">
      <ChapterNav chapter={chapter} />
      <div className="chapter-summary-page__header">
        <h1 className="chapter-heading">
          Matthew {chapter} — Chapter Summary
        </h1>
        <Link to={`/chapter/${chapter}`} className="chapter-summary-page__reader-link">
          ← back to parallel reader
        </Link>
      </div>

      <GenerateSummaryButton
        chapter={chapter}
        onCompleted={() => {
          // The hook invalidates the summaries query on mutation success.
        }}
      />

      {summariesQuery.isLoading ? (
        <div className="empty-block">Loading chapter summaries…</div>
      ) : null}

      {!summariesQuery.isLoading && summaries.length === 0 ? (
        <div className="empty-block" data-testid="chapter-summary-page__empty">
          No chapter summary yet — Generate to create one.
        </div>
      ) : null}

      {summariesQuery.isError ? (
        <div className="error-block" role="alert">
          <strong>
            {summariesQuery.error instanceof Error
              ? summariesQuery.error.message
              : "Failed to load chapter summaries"}
          </strong>
        </div>
      ) : null}

      <div className="chapter-summary-page__list">
        {summaries.map((summary) => (
          <ChapterSummaryDisplay key={summary.summary_id} summary={summary} />
        ))}
      </div>
    </div>
  );
}
