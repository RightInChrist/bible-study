/**
 * Per-chapter Generate-summary button (Slice 8).
 *
 * Triggers `POST /api/v1/runs` with `{scope: chapter_summary, ...}` and
 * the chapter-summary-v1 prompt. Polls the run until it completes, then
 * invalidates the chapter-summary list so the new row renders.
 *
 * Wrapped in `<AuthOnly>` so the static-site export strips it out.
 */
import { useState } from "react";

import { useGenerateSummary, useRun } from "../../api/hooks";
import { ApiError } from "../../api/client";
import { AuthOnly } from "../AuthOnly";

interface Props {
  chapter: number;
  onCompleted?: () => void;
}

const DEFAULT_MODEL = "claude-opus-4-7";
const DEFAULT_EFFORT = "xhigh";

export function GenerateSummaryButton({ chapter, onCompleted }: Props) {
  return (
    <AuthOnly>
      <GenerateSummaryButtonInner chapter={chapter} onCompleted={onCompleted} />
    </AuthOnly>
  );
}

function GenerateSummaryButtonInner({ chapter, onCompleted }: Props) {
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const generate = useGenerateSummary();
  const runQuery = useRun(activeRunId);

  const isRunning =
    activeRunId !== null &&
    runQuery.data !== undefined &&
    !["completed", "failed", "cancelled", "interrupted"].includes(
      runQuery.data.status,
    );

  const submit = (): void => {
    generate.mutate(
      {
        scope: { kind: "chapter_summary", chapter },
        style_prompt_version: "chapter-summary-v1",
        source_set_id: "CHAPTER_BUNDLE",
        model: DEFAULT_MODEL,
        effort: DEFAULT_EFFORT,
      },
      {
        onSuccess: (run) => {
          setActiveRunId(run.run_id);
        },
      },
    );
  };

  if (
    activeRunId !== null &&
    runQuery.data !== undefined &&
    runQuery.data.status === "completed"
  ) {
    if (onCompleted) onCompleted();
  }

  const createError = generate.error instanceof ApiError ? generate.error.body : null;

  return (
    <div className="generate-summary-button" data-testid="generate-summary-button">
      <button
        type="button"
        onClick={submit}
        disabled={generate.isPending || isRunning}
        data-testid="generate-summary-button__submit"
      >
        {generate.isPending
          ? "Submitting…"
          : isRunning
            ? `Generating summary for Matthew ${chapter}…`
            : `Generate chapter summary (Matthew ${chapter})`}
      </button>
      {createError ? (
        <div className="generate-summary-button__error" role="alert">
          <strong>{createError.code}</strong>: {createError.message}
        </div>
      ) : null}
      {activeRunId !== null && runQuery.data?.status === "failed" ? (
        <div className="generate-summary-button__error" role="alert">
          Run failed.
        </div>
      ) : null}
      {activeRunId !== null && runQuery.data?.status === "completed" ? (
        <div className="generate-summary-button__success" role="status">
          Done — summary generated.
        </div>
      ) : null}
    </div>
  );
}
