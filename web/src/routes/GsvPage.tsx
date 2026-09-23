/**
 * GSV page (Designer Flow 6).
 *
 * Reads:
 *   - GET /api/v1/gsv/coverage              (repo-wide stats)
 *   - GET /api/v1/gsv/{chapter}?format=...  (chapter compilation)
 *
 * Surfaces:
 *   - Coverage indicator at top
 *   - Format toggle (Markdown default)
 *   - Body rendered for the selected format
 *   - Download button (links to ?download=true)
 *   - Build-static button — disabled, lands in slice 6
 *   - Unresolved-ties error block when plain-text 409s
 */
import { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { useGsvChapter, useGsvCoverage } from "../api/hooks";
import type { GsvFormat } from "../api/types";
import { BuildStaticButton } from "../components/BuildStaticButton";
import { ChapterNav } from "../components/ChapterNav";
import { CoverageIndicator } from "../components/gsv/CoverageIndicator";
import { FormatToggle } from "../components/gsv/FormatToggle";
import { GsvBody } from "../components/gsv/GsvBody";

const DEFAULT_FORMAT: GsvFormat = "markdown";

function parseChapter(raw: string | undefined): number {
  if (!raw) return 1;
  const n = Number.parseInt(raw, 10);
  if (!Number.isInteger(n) || n < 1 || n > 28) return 1;
  return n;
}

interface UnresolvedTiesDetails {
  chapter: number;
  offending_sentences: string[];
}

function isUnresolvedTiesError(err: unknown): err is ApiError {
  if (!(err instanceof ApiError)) return false;
  return err.status === 409 && err.body?.code === "unresolved_ties";
}

export function GsvPage() {
  const { chapter: chapterParam } = useParams<{ chapter: string }>();
  const chapter = parseChapter(chapterParam);
  const [format, setFormat] = useState<GsvFormat>(DEFAULT_FORMAT);

  const coverageQuery = useGsvCoverage();
  const chapterQuery = useGsvChapter(chapter, format);

  // We always also fetch the JSON form so the coverage indicator stays
  // accurate even when text format hits a tie 409. This keeps the
  // numbers truthful rather than going blank on tie errors.
  const jsonQuery = useGsvChapter(chapter, "json");

  const tiesError = useMemo<UnresolvedTiesDetails | null>(() => {
    const err = chapterQuery.error;
    if (!isUnresolvedTiesError(err)) return null;
    const details = err.body?.details as Record<string, unknown> | undefined;
    if (!details) return null;
    const offending = Array.isArray(details.offending_sentences)
      ? (details.offending_sentences as string[])
      : [];
    return {
      chapter: typeof details.chapter === "number" ? details.chapter : chapter,
      offending_sentences: offending,
    };
  }, [chapterQuery.error, chapter]);

  const downloadHref = `/api/v1/gsv/${chapter}?format=${format}&download=true`;

  // Coverage stats projected to the current chapter.
  const chapterCoverage =
    coverageQuery.data?.per_chapter.find((c) => c.chapter === chapter) ?? null;

  // The chapter response (json) carries total_sentences for the chapter.
  const totalSentencesInChapter =
    jsonQuery.data?.format === "json"
      ? jsonQuery.data.data.coverage.total_sentences
      : null;

  return (
    <div className="gsv-page" data-testid="gsv-page">
      <ChapterNav chapter={chapter} />
      <div className="gsv-page__header">
        <h1 className="chapter-heading">Matthew {chapter} — GSV</h1>
        <Link to={`/chapter/${chapter}`} className="gsv-page__reader-link">
          ← back to parallel reader
        </Link>
      </div>
      <CoverageIndicator
        chapter={chapter}
        coverage={coverageQuery.data}
        totalSentencesInChapter={totalSentencesInChapter}
        rankedRedLetterInChapter={
          chapterCoverage?.ranked_red_letter_sentences ?? 0
        }
        totalRedLetterInChapter={
          chapterCoverage?.total_red_letter_sentences ?? 0
        }
      />
      <div className="gsv-page__controls">
        <FormatToggle format={format} onChange={setFormat} />
        <a
          className="gsv-page__download"
          href={downloadHref}
          download
          data-testid="gsv-download"
        >
          Download {format}
        </a>
        <BuildStaticButton />
      </div>

      {chapterQuery.isLoading ? (
        <div className="empty-block">Compiling GSV for Matthew {chapter}…</div>
      ) : null}

      {tiesError ? (
        <div className="error-block" role="alert" data-testid="gsv-ties-error">
          <span className="error-block__code">unresolved_ties</span>
          <strong>
            Plain-text format can&apos;t render —{" "}
            {tiesError.offending_sentences.length} sentence(s) have unresolved
            ties.
          </strong>
          <ul className="gsv-ties-list">
            {tiesError.offending_sentences.map((sid) => (
              <li key={sid}>
                <Link to={`/sentence/${sid}/rank`}>
                  Resolve tie at {sid}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {chapterQuery.data && !tiesError ? (
        <GsvBody payload={chapterQuery.data} />
      ) : null}

      {chapterQuery.isError && !tiesError ? (
        <div className="error-block" role="alert">
          <span className="error-block__code">gsv_error</span>
          <strong>
            {chapterQuery.error instanceof Error
              ? chapterQuery.error.message
              : "Failed to load GSV"}
          </strong>
        </div>
      ) : null}
    </div>
  );
}
