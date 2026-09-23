/**
 * Format toggle (Designer Flow 6 step 3): JSON / Plain text / Markdown.
 *
 * Default is Markdown — most readable, mirrors the file the static-site
 * builder will eventually emit, and surfaces provenance footnotes inline
 * so Gavin can scan them without flipping format.
 */
import type { GsvFormat } from "../../api/types";

interface Props {
  format: GsvFormat;
  onChange: (next: GsvFormat) => void;
}

const FORMATS: ReadonlyArray<{ value: GsvFormat; label: string }> = [
  { value: "markdown", label: "Markdown" },
  { value: "text", label: "Plain text" },
  { value: "json", label: "JSON" },
];

export function FormatToggle({ format, onChange }: Props) {
  return (
    <div
      className="gsv-format-toggle"
      role="radiogroup"
      aria-label="GSV format"
      data-testid="gsv-format-toggle"
    >
      {FORMATS.map((f) => (
        <button
          key={f.value}
          type="button"
          role="radio"
          aria-checked={format === f.value}
          onClick={() => onChange(f.value)}
          className="gsv-format-toggle__btn"
          data-testid={`gsv-format-toggle__${f.value}`}
          data-active={format === f.value ? "true" : "false"}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}
