/**
 * 409 stale_version conflict modal (Designer §Error states "Ranking
 * save conflict").
 *
 * Shows the server's current state (entries + notes + version) so Gavin
 * can compare against the local draft. Two choices:
 *   - **Discard my changes** → load server state into the draft
 *   - **Reapply over server state** → keep local entries/notes, but
 *     bump the local version to match the server's version so the next
 *     save actually lands. Risky; explicit per Designer.
 */
import type { CandidateRef, RankingEntry } from "../../api/types";

export interface ConflictPayload {
  current_version: number;
  current_notes: string | null;
  current_entries: RankingEntry[];
}

interface Props {
  payload: ConflictPayload;
  onDiscardMyChanges(): void;
  onReapplyOverServer(): void;
  onDismiss(): void;
}

function refLabel(ref: CandidateRef): string {
  if (ref.kind === "translation") {
    return `${ref.name} · ${ref.verse_range}`;
  }
  return `claude · candidate ${ref.candidate_id}`;
}

export function ConflictModal({
  payload,
  onDiscardMyChanges,
  onReapplyOverServer,
  onDismiss,
}: Props) {
  return (
    <div
      className="rank-conflict-modal"
      data-testid="rank-conflict-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rank-conflict-modal__title"
    >
      <div className="rank-conflict-modal__panel">
        <h2 id="rank-conflict-modal__title" className="rank-conflict-modal__title">
          Ranking changed in another tab
        </h2>
        <p>
          The server's current version is <strong>{payload.current_version}</strong>.
          You can discard your changes and load the server's state, or reapply
          your local edits over the server's state.
        </p>
        <section className="rank-conflict-modal__section">
          <h3>Server's current entries</h3>
          {payload.current_entries.length === 0 ? (
            <em>(no entries)</em>
          ) : (
            <ol data-testid="rank-conflict-modal__entries">
              {payload.current_entries.map((e) => (
                <li key={`${e.position}-${refLabel(e.candidate_ref)}`}>
                  rank {e.rank}
                  {e.tied_with_above ? " (tied)" : ""} — {refLabel(e.candidate_ref)}
                </li>
              ))}
            </ol>
          )}
          {payload.current_notes ? (
            <p>
              <strong>Server notes:</strong> {payload.current_notes}
            </p>
          ) : null}
        </section>
        <div className="rank-conflict-modal__actions">
          <button
            type="button"
            onClick={onDiscardMyChanges}
            data-testid="rank-conflict-modal__discard"
          >
            Discard my changes
          </button>
          <button
            type="button"
            onClick={onReapplyOverServer}
            data-testid="rank-conflict-modal__reapply"
          >
            Reapply over server state
          </button>
          <button
            type="button"
            onClick={onDismiss}
            data-testid="rank-conflict-modal__dismiss"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
