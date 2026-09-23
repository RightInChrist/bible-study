/**
 * Free-text notes textarea (Designer Flow 5 #5).
 *
 * Persisted with the same OCC version as the ranking entries (Hard
 * decision #17). The textarea drives a `notes` string in the parent's
 * draft state; saved together with the rank list when Save is clicked.
 *
 * While focus is in this textarea, all keyboard shortcuts must be
 * disabled — that scoping rule is implemented at the page level via
 * `data-keyboard-scope` on the textarea wrapper.
 */
interface Props {
  value: string;
  onChange(next: string): void;
  onFocus?(): void;
  onBlur?(): void;
}

export function NotesField({ value, onChange, onFocus, onBlur }: Props) {
  return (
    <label className="rank-notes" data-testid="rank-notes">
      <span className="rank-notes__label">Notes</span>
      <textarea
        className="rank-notes__textarea"
        data-testid="rank-notes__textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={onFocus}
        onBlur={onBlur}
        placeholder="e.g. 'BLB wins on θεῷ but BSB reads better aloud.'"
        rows={4}
      />
    </label>
  );
}
