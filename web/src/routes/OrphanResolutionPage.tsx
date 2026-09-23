/**
 * Orphan resolution page (Designer §Edge cases — Fixture version change
 * mid-work; SPEC.md §"Apply and re-import").
 *
 * Loads `details.orphan_summary` from router state (passed by the status
 * pill on a 409 `orphans_detected`) and lets the user pick a per-orphan
 * disposition. v1 ships **delete** and **keep** only — full **remap**
 * UX is a follow-up. On Apply, POSTs `/admin/reimport` with `force=true`
 * and the disposition map.
 *
 * Wrapped in `<AuthOnly>` so it's stripped from the static build.
 */
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import type {
  OrphanCandidate,
  OrphanDisposition,
  OrphanOverlay,
  OrphanRanking,
  OrphanSummary,
} from "../api/types";
import { ApiError } from "../api/client";
import { useReimport } from "../api/hooks";
import { AuthOnly } from "../components/AuthOnly";

interface OrphanResolutionLocationState {
  orphanSummary?: OrphanSummary;
}

type OrphanKey = string;

function candidateKey(c: OrphanCandidate): OrphanKey {
  return `candidate:${c.candidate_id}`;
}

function rankingKey(r: OrphanRanking): OrphanKey {
  return `ranking:${r.sentence_id_old}`;
}

function overlayKey(o: OrphanOverlay): OrphanKey {
  return `overlay:${o.overlay_id}`;
}

function OrphanResolutionInner() {
  const location = useLocation();
  const navigate = useNavigate();
  const reimport = useReimport();
  const state = (location.state ?? {}) as OrphanResolutionLocationState;
  const summary = state.orphanSummary ?? null;

  const initialDispositions = useMemo<Record<OrphanKey, OrphanDisposition>>(() => {
    const out: Record<OrphanKey, OrphanDisposition> = {};
    if (!summary) return out;
    for (const c of summary.affected_candidates) out[candidateKey(c)] = "keep";
    for (const r of summary.affected_rankings) out[rankingKey(r)] = "keep";
    for (const o of summary.affected_overlays) out[overlayKey(o)] = "keep";
    return out;
  }, [summary]);

  const [dispositions, setDispositions] =
    useState<Record<OrphanKey, OrphanDisposition>>(initialDispositions);
  const [errorBlock, setErrorBlock] = useState<string | null>(null);

  if (summary === null) {
    return (
      <div className="orphan-page" data-testid="orphan-page-empty">
        <h1>Orphan resolution</h1>
        <div className="empty-block">
          No pending orphans. Trigger a re-import from the status pill to refresh.
        </div>
      </div>
    );
  }

  const setDisposition = (key: OrphanKey, value: OrphanDisposition): void => {
    setDispositions((prev) => ({ ...prev, [key]: value }));
  };

  const onApply = (): void => {
    setErrorBlock(null);
    reimport.mutate(
      { force: true, dispositions },
      {
        onSuccess: () => {
          navigate("/chapter/1");
        },
        onError: (err: Error) => {
          if (err instanceof ApiError && err.body) {
            setErrorBlock(`${err.body.code}: ${err.body.message}`);
            return;
          }
          setErrorBlock(err.message);
        },
      },
    );
  };

  return (
    <div className="orphan-page" data-testid="orphan-page">
      <h1>Orphan resolution</h1>
      <p>
        {summary.total} orphan(s) reference sentences that the new fixture
        set would remove or change. Pick a disposition for each, then click
        <strong> Apply</strong>.
      </p>

      {summary.affected_candidates.length > 0 ? (
        <section data-testid="orphan-candidates">
          <h2>Candidates ({summary.affected_candidates.length})</h2>
          <ul className="orphan-list">
            {summary.affected_candidates.map((c) => {
              const key = candidateKey(c);
              return (
                <li key={key} className="orphan-row">
                  <div className="orphan-row__meta">
                    <code>{c.sentence_id_old}</code> · {c.reason} · candidate
                    #{c.candidate_id}
                  </div>
                  <div className="orphan-row__excerpt">
                    {c.candidate_text_excerpt}
                  </div>
                  <DispositionSelect
                    value={dispositions[key] ?? "keep"}
                    onChange={(v) => setDisposition(key, v)}
                    testId={`disposition-${key}`}
                  />
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {summary.affected_rankings.length > 0 ? (
        <section data-testid="orphan-rankings">
          <h2>Rankings ({summary.affected_rankings.length})</h2>
          <ul className="orphan-list">
            {summary.affected_rankings.map((r) => {
              const key = rankingKey(r);
              return (
                <li key={key} className="orphan-row">
                  <div className="orphan-row__meta">
                    <code>{r.sentence_id_old}</code> · {r.reason} ·
                    {r.ranked_candidate_count} ranked
                  </div>
                  <DispositionSelect
                    value={dispositions[key] ?? "keep"}
                    onChange={(v) => setDisposition(key, v)}
                    testId={`disposition-${key}`}
                  />
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {summary.affected_overlays.length > 0 ? (
        <section data-testid="orphan-overlays">
          <h2>Overlays ({summary.affected_overlays.length})</h2>
          <ul className="orphan-list">
            {summary.affected_overlays.map((o) => {
              const key = overlayKey(o);
              return (
                <li key={key} className="orphan-row">
                  <div className="orphan-row__meta">
                    overlay #{o.overlay_id} ·{" "}
                    <code>{o.range_start_sentence_id_old}</code>–
                    <code>{o.range_end_sentence_id_old}</code> · {o.reason}
                  </div>
                  <DispositionSelect
                    value={dispositions[key] ?? "keep"}
                    onChange={(v) => setDisposition(key, v)}
                    testId={`disposition-${key}`}
                  />
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      <div className="orphan-page__actions">
        <button
          type="button"
          onClick={onApply}
          disabled={reimport.isPending}
          data-testid="orphan-apply"
        >
          {reimport.isPending ? "Applying…" : "Apply and re-import"}
        </button>
      </div>

      {errorBlock !== null ? (
        <div className="error-block" role="alert" data-testid="orphan-error">
          {errorBlock}
        </div>
      ) : null}
    </div>
  );
}

interface DispositionSelectProps {
  value: OrphanDisposition;
  onChange: (next: OrphanDisposition) => void;
  testId: string;
}

function DispositionSelect({ value, onChange, testId }: DispositionSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as OrphanDisposition)}
      data-testid={testId}
    >
      <option value="keep">keep</option>
      <option value="delete">delete</option>
    </select>
  );
}

export function OrphanResolutionPage() {
  return (
    <AuthOnly>
      <OrphanResolutionInner />
    </AuthOnly>
  );
}
