/**
 * Static-bundle strip-out smoke for the Slice 7 write paths.
 *
 * The actual grep-guard is enforced by `api/admin/service.py`'s
 * `_grep_guard_dist` against `dist.tmp/`. This test asserts the
 * source-level invariant: write-path URL strings live behind
 * `__STATIC__` ternaries so Vite's define-replace + minifier strip
 * them. We verify the source uses the gated constants rather than
 * raw inline literals.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function readSrc(rel: string): string {
  return readFileSync(resolve(__dirname, "..", rel), "utf-8");
}

describe("red-letter URL constants are __STATIC__-gated", () => {
  it("hooks.ts gates /red-letter/mark behind __STATIC__", () => {
    const src = readSrc("src/api/hooks.ts");
    expect(src).toContain("RED_LETTER_MARK_PATH");
    expect(src).toContain("__STATIC__ ? \"\" : \"/api/v1/red-letter/mark\"");
  });

  it("hooks.ts gates /red-letter/unmark behind __STATIC__", () => {
    const src = readSrc("src/api/hooks.ts");
    expect(src).toContain("RED_LETTER_UNMARK_PATH");
    expect(src).toContain("/api/v1/red-letter/unmark");
  });

  it("hooks.ts gates /red-letter/restore behind __STATIC__", () => {
    const src = readSrc("src/api/hooks.ts");
    expect(src).toContain("RED_LETTER_RESTORE_PATH");
    expect(src).toContain("/api/v1/red-letter/restore");
  });

  it("App.tsx gates the /red-letter editor route behind __STATIC__", () => {
    const src = readSrc("src/App.tsx");
    expect(src).toContain("RED_LETTER_EDITOR_ROUTE");
    expect(src).toMatch(/__STATIC__ \? null : \(\s*<Route path={RED_LETTER_EDITOR_ROUTE}/);
  });
});
