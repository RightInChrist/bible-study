/**
 * Per-sentence Generate button (Designer Flow 4 — minimal v1 entry point).
 *
 * The full Generate page (batch form, recent-runs list, SSE-driven progress)
 * lands in slice 3b. This slice ships the smallest path: a button on the
 * sentence detail surface that POSTs `{scope: one_sentence, ...}` with the
 * primary lens default (`first-century-jewish-v1` × `BOTH_GREEK` ×
 * `claude-opus-4-7`) and polls the run until it completes, then refetches
 * the candidates list.
 *
 * The style-prompt picker is surfaced as a small dropdown so cross-checks
 * (literal/dynamic/plainspoken) remain one click away.
 *
 * Wrapped in `<AuthOnly>` so the static-site export strips it out at
 * build time (Hard decision #14).
 */
import { useState } from "react";

import { useCreateRun, useRun } from "../api/hooks";
import { ApiError } from "../api/client";
import { AuthOnly } from "./AuthOnly";
import type {
  CreateRunRequest,
  StylePromptVersion,
} from "../api/types";

interface Props {
  sentenceId: string;
  onCompleted?: () => void;
}

const DEFAULT_STYLE: StylePromptVersion = "first-century-jewish-v1";
const DEFAULT_MODEL = "claude-opus-4-7";
const DEFAULT_EFFORT = "xhigh";

const STYLE_OPTIONS: ReadonlyArray<{ value: StylePromptVersion; label: string }> = [
  { value: "first-century-jewish-v1", label: "First-century Jewish (primary)" },
  { value: "literal-v1", label: "Literal" },
  { value: "dynamic-v1", label: "Dynamic equivalence" },
  { value: "plainspoken-v1", label: "Plainspoken modern" },
];

const buildRequest = (
  sentenceId: string,
  stylePromptVersion: StylePromptVersion,
): CreateRunRequest => ({
  scope: { kind: "one_sentence", sentence_id: sentenceId },
  style_prompt_version: stylePromptVersion,
  source_set_id: "BOTH_GREEK",
  model: DEFAULT_MODEL,
  effort: DEFAULT_EFFORT,
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
  const [stylePromptVersion, setStylePromptVersion] =
    useState<StylePromptVersion>(DEFAULT_STYLE);
  const createRun = useCreateRun();
  const runQuery = useRun(activeRunId);

  const isRunning =
    activeRunId !== null &&
    runQuery.data !== undefined &&
    !["completed", "failed", "cancelled", "interrupted"].includes(runQuery.data.status);

  const submit = (): void => {
    createRun.mutate(buildRequest(sentenceId, stylePromptVersion), {
      onSuccess: (run) => {
        setActiveRunId(run.run_id);
      },
    });
  };

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
      <select
        aria-label="Style prompt"
        data-testid="generate-button__style"
        value={stylePromptVersion}
        onChange={(e) =>
          setStylePromptVersion(e.target.value as StylePromptVersion)
        }
        disabled={createRun.isPending || isRunning}
      >
        {STYLE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
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
            : `Generate translation (${stylePromptVersion} · Both Greek)`}
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
