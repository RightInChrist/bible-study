/**
 * Top-of-app fixture status pill (Designer §Surfaces).
 *
 * Reads `GET /api/v1/admin/fixture-status`. Shows fixture version + last
 * imported timestamp. When the disk manifest hash differs from the DB
 * fixture version (`stale=true`), the pill takes on the stale look and
 * surfaces an active **Re-import** button.
 *
 * Slice 6 wired the button:
 *   - 200 → toast "Re-imported"; queries invalidated by the mutation hook.
 *   - 409 `orphans_detected` → navigate to `/admin/orphans`, passing the
 *     `details.orphan_summary` through router state.
 *   - 409 `import_in_progress` → toast + 5s cooldown.
 *   - 400 `fixture_hash_mismatch` → inline error block.
 *
 * Wrapped in `<AuthOnly>` so the static build tree-shakes it.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { FixtureStatusResponse, OrphanSummary } from "../api/types";
import { ApiError } from "../api/client";
import { useFixtureStatus, useReimport } from "../api/hooks";
import { AuthOnly } from "./AuthOnly";

function shortHash(hash: string | null | undefined): string {
  if (!hash) return "—";
  return hash.slice(0, 12);
}

function relativeTimestamp(ts: string | null): string {
  if (!ts) return "never";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
  } catch {
    return ts;
  }
}

interface StatusPillBodyProps {
  status: FixtureStatusResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}

interface ReimportButtonProps {
  stale: boolean;
}

function ReimportButton({ stale }: ReimportButtonProps) {
  const navigate = useNavigate();
  const reimport = useReimport();
  const [cooldownUntil, setCooldownUntil] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [errorBlock, setErrorBlock] = useState<string | null>(null);

  useEffect(() => {
    if (toast === null) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const inCooldown = cooldownUntil !== null && Date.now() < cooldownUntil;

  const onClick = (): void => {
    setErrorBlock(null);
    reimport.mutate(
      { force: false },
      {
        onSuccess: () => {
          setToast("Re-imported");
        },
        onError: (err: Error) => {
          if (!(err instanceof ApiError) || !err.body) {
            setErrorBlock(err.message);
            return;
          }
          if (err.status === 409 && err.body.code === "orphans_detected") {
            const details = err.body.details ?? {};
            const summary = (details as Record<string, unknown>)
              .orphan_summary as OrphanSummary | undefined;
            // Path lifted to build-time constant so it strips in static
            // mode (PLAN §Build-time guards greps for `/admin/`).
            const target: string = __STATIC__ ? "" : "/admin/orphans";
            navigate(target, { state: { orphanSummary: summary } });
            return;
          }
          if (err.status === 409 && err.body.code === "import_in_progress") {
            setToast("An import is already running");
            setCooldownUntil(Date.now() + 5000);
            setTimeout(() => setCooldownUntil(null), 5000);
            return;
          }
          if (err.status === 400 && err.body.code === "fixture_hash_mismatch") {
            const errs = (err.body.details as Record<string, unknown>)?.errors;
            const summary = Array.isArray(errs) ? (errs as string[]).join("; ") : err.body.message;
            setErrorBlock(`fixture_hash_mismatch: ${summary}`);
            return;
          }
          setErrorBlock(err.body.message);
        },
      },
    );
  };

  return (
    <>
      <button
        type="button"
        className="status-pill__action"
        disabled={!stale || reimport.isPending || inCooldown}
        title={
          stale
            ? "Re-import fixtures into SQLite"
            : "Fixture matches imported manifest"
        }
        aria-label="Re-import fixtures"
        onClick={onClick}
        data-testid="status-pill-reimport"
      >
        {reimport.isPending ? "re-importing…" : "re-import"}
      </button>
      {toast !== null ? (
        <span className="status-pill__toast" role="status" data-testid="status-pill-toast">
          {toast}
        </span>
      ) : null}
      {errorBlock !== null ? (
        <span
          className="status-pill__error"
          role="alert"
          data-testid="status-pill-error"
        >
          {errorBlock}
        </span>
      ) : null}
    </>
  );
}

function StatusPillBody({ status, isLoading, isError }: StatusPillBodyProps) {
  if (isLoading) {
    return (
      <span className="status-pill" aria-live="polite">
        <span className="status-pill__dot" aria-hidden="true" />
        <span>checking fixtures…</span>
      </span>
    );
  }
  if (isError || !status) {
    return (
      <span className="status-pill" data-stale="true" role="status">
        <span className="status-pill__dot" aria-hidden="true" />
        <span>fixture status unavailable</span>
      </span>
    );
  }
  const hash = status.db_fixture_version ?? status.disk_manifest_hash;
  return (
    <span
      className="status-pill"
      data-stale={status.stale ? "true" : "false"}
      role="status"
      title={
        status.stale
          ? "Disk manifest hash differs from imported fixture version. Click Re-import."
          : "Fixture matches imported manifest."
      }
    >
      <span className="status-pill__dot" aria-hidden="true" />
      <span className="status-pill__hash">fix {shortHash(hash)}</span>
      <span>· {relativeTimestamp(status.last_imported_at)}</span>
      {status.stale ? <span> · stale</span> : null}
      <AuthOnly>
        <ReimportButton stale={status.stale} />
      </AuthOnly>
    </span>
  );
}

export function StatusPill() {
  const { data, isLoading, isError } = useFixtureStatus();
  return <StatusPillBody status={data} isLoading={isLoading} isError={isError} />;
}
