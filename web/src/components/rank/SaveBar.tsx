/**
 * Bottom action bar (Designer Flow 5 step 7-ish): Save / Save+next /
 * Prev. Carries a small status indicator showing the last save time.
 */
interface Props {
  isSaving: boolean;
  isDirty: boolean;
  lastSavedAt: string | null;
  hasPrev: boolean;
  hasNext: boolean;
  onSave(): void;
  onSaveAndNext(): void;
  onPrev(): void;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString();
}

export function SaveBar({
  isSaving,
  isDirty,
  lastSavedAt,
  hasPrev,
  hasNext,
  onSave,
  onSaveAndNext,
  onPrev,
}: Props) {
  return (
    <div className="rank-savebar" data-testid="rank-savebar">
      <button
        type="button"
        className="rank-savebar__btn"
        onClick={onPrev}
        disabled={!hasPrev}
        data-testid="rank-savebar__prev"
        aria-label="Previous sentence"
      >
        ← prev
      </button>
      <button
        type="button"
        className="rank-savebar__btn rank-savebar__btn--primary"
        onClick={onSave}
        disabled={isSaving || !isDirty}
        data-testid="rank-savebar__save"
      >
        {isSaving ? "Saving…" : "Save"}
      </button>
      <button
        type="button"
        className="rank-savebar__btn"
        onClick={onSaveAndNext}
        disabled={isSaving || !hasNext}
        data-testid="rank-savebar__save-next"
      >
        Save & next →
      </button>
      <span className="rank-savebar__status" data-testid="rank-savebar__status">
        {isSaving
          ? "saving…"
          : isDirty
            ? "unsaved changes"
            : lastSavedAt
              ? `saved ${formatTime(lastSavedAt)}`
              : "no changes"}
      </span>
    </div>
  );
}
