/**
 * Per-sentence Generate button (Designer Flow 4 — minimal v1 entry point).
 *
 * The full Generate page (batch form, recent-runs list, SSE-driven progress)
 * lands in slice 3b. This slice ships the smallest path: a button on the
 * sentence detail surface that POSTs `{scope: one_sentence, ...}` with a
 * sensible default (literal-v1 + BOTH_GREEK + claude-sonnet-4-6) and
 * polls the run until it completes, then refetches the candidates list.
 *
 * Wrapped in `<AuthOnly>` so the static-site export strips it out at
 * build time (Hard decision #14).
 */
import { useState } from "react";

import { useCreateRun, useRun } from "../api/hooks";
import { ApiError } from "../api/client";
import { AuthOnly } from "./AuthOnly";
import type { CreateRunRequest } from "../api/types";

interface Props {
  sentenceId: string;
  onCompleted?: () => void;
}

const DEFAULT_REQUEST = (sentenceId: string): CreateRunRequest => ({
  scope: { kind: "one_sentence", sentence_id: sentenceId },
  style_prompt_version: "literal-v1",
  source_set_id: "BOTH_GREEK",
  model: "claude-sonnet-4-6",
});

export function GenerateButton({ sentenceId, onCompleted }: Props) {
  return (
    <AuthOnly>
      <GenerateButtonInner sentenceId={sentenceId} onCompleted={onCompleted} />
    </AuthOnly>
  );
}

function GenerateButtonInner({ sentenceId, onCompleted }: Props) {
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const createRun = useCreateRun();
  const runQuery = useRun(activeRunId);

  const isRunning =
    activeRunId !== null &&
    runQuery.data !== undefined &&
    !["completed", "failed", "cancelled", "interrupted"].includes(runQuery.data.status);

  const submit = (): void => {
    createRun.mutate(DEFAULT_REQUEST(sentenceId), {
      onSuccess: (run) => {
        setActiveRunId(run.run_id);
      },
    });
  };

  // Auto-clear the active run when terminal so the next click starts fresh.
  if (
    activeRunId !== null &&
    runQuery.data !== undefined &&
    ["completed", "failed", "cancelled", "interrupted"].includes(runQuery.data.status)
  ) {
    if (onCompleted) onCompleted();
  }

  const createError = createRun.error instanceof ApiError ? createRun.error.body : null;

  return (
    <div className="generate-button" data-testid="generate-button">
      <button
        type="button"
        onClick={submit}
        disabled={createRun.isPending || isRunning}
        data-testid="generate-button__submit"
      >
        {createRun.isPending
          ? "Submitting…"
          : isRunning
            ? `Generating (${runQuery.data?.items_completed ?? 0}/${runQuery.data?.items_count ?? 1})`
            : "Generate translation (literal-v1 · Both Greek)"}
      </button>
      {createError ? (
        <div className="generate-button__error" role="alert">
          <strong>{createError.code}</strong>: {createError.message}
        </div>
      ) : null}
      {activeRunId !== null && runQuery.data?.status === "failed" ? (
        <div className="generate-button__error" role="alert">
          Run failed — {runQuery.data.items_failed} of {runQuery.data.items_count} items failed.
        </div>
      ) : null}
      {activeRunId !== null && runQuery.data?.status === "completed" ? (
        <div className="generate-button__success" role="status">
          Done — {runQuery.data.items_completed}/{runQuery.data.items_count} candidates generated.
        </div>
      ) : null}
    </div>
  );
}
