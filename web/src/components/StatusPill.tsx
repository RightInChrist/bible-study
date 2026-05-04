/**
 * Top-of-app fixture status pill (Designer §Surfaces, SPEC.md ~line 78).
 *
 * Reads `GET /api/v1/admin/fixture-status`. Shows fixture version + last
 * imported timestamp. When the disk manifest hash differs from the DB
 * fixture version (`stale=true`), the pill takes on the stale look and
 * surfaces a disabled "Re-import" button explaining that re-import lands
 * in a later slice. The button is wrapped in `<AuthOnly>` so the static
 * build will tree-shake it away.
 */
import type { FixtureStatusResponse } from "../api/types";
import { useFixtureStatus } from "../api/hooks";
import { AuthOnly } from "./AuthOnly";

function shortHash(hash: string | null | undefined): string {
  if (!hash) return "—";
  return hash.slice(0, 12);
}

function relativeTimestamp(ts: string | null): string {
  if (!ts) return "never";
  // ISO-8601 — display compact local-time, not human-relative, since
  // this is a single-user devtool and Gavin wants the actual timestamp.
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
          ? "Disk manifest hash differs from the imported fixture version. Re-import lands in a later slice."
          : "Fixture matches imported manifest."
      }
    >
      <span className="status-pill__dot" aria-hidden="true" />
      <span className="status-pill__hash">fix {shortHash(hash)}</span>
      <span>· {relativeTimestamp(status.last_imported_at)}</span>
      {status.stale ? <span> · stale</span> : null}
      <AuthOnly>
        <button
          type="button"
          className="status-pill__action"
          disabled
          title="Re-import lands in a later slice"
          aria-label="Re-import fixtures (deferred)"
        >
          re-import
        </button>
      </AuthOnly>
    </span>
  );
}

export function StatusPill() {
  const { data, isLoading, isError } = useFixtureStatus();
  return <StatusPillBody status={data} isLoading={isLoading} isError={isError} />;
}
