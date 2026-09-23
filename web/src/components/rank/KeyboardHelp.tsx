/**
 * Tiny keyboard cheat-sheet popover (Designer Flow 5 — `?` overlay).
 *
 * Stub for slice 4 — full overlay with grouping is a polish slice.
 */
interface Props {
  onClose(): void;
}

const SHORTCUTS: ReadonlyArray<{ keys: string; what: string }> = [
  { keys: "↑ / ↓ (or k / j)", what: "Move focus through cards" },
  { keys: "space", what: "Lift focused card; arrows then move it" },
  { keys: "t", what: "Toggle tied-with-above on focused card" },
  { keys: "h", what: "Hide focused card" },
  { keys: "s", what: "Save" },
  { keys: "n or →", what: "Save and advance to next sentence" },
  { keys: "p or ←", what: "Save and back to previous sentence" },
  { keys: "?", what: "Show this help" },
  { keys: "Esc", what: "Close help" },
];

export function KeyboardHelp({ onClose }: Props) {
  return (
    <div
      className="rank-help"
      data-testid="rank-help"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div className="rank-help__panel">
        <h2 className="rank-help__title">Keyboard shortcuts</h2>
        <table className="rank-help__table">
          <tbody>
            {SHORTCUTS.map((s) => (
              <tr key={s.keys}>
                <td>
                  <span className="kbd">{s.keys}</span>
                </td>
                <td>{s.what}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="rank-help__note">
          Shortcuts are disabled while typing in the notes field.
        </p>
        <button type="button" onClick={onClose} data-testid="rank-help__close">
          Close
        </button>
      </div>
    </div>
  );
}
