/**
 * Static-bundle smoke for the Slice 8 chapter-summary write path.
 *
 * The chapter-summary generate path routes through `POST /api/v1/runs`,
 * which is already in the build deny-list. This test verifies the
 * source-level invariant: the URL string lives behind a `__STATIC__`
 * ternary so Vite's define-replace + minifier strip it from the static
 * bundle.
 *
 * The read paths (`/chapters/{N}/summaries`) are static-bundle safe
 * (the static-site builder emits them as JSON snapshots).
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function readSrc(rel: string): string {
  return readFileSync(resolve(__dirname, "..", rel), "utf-8");
}

describe("chapter-summary URL constants are __STATIC__-gated", () => {
  it("hooks.ts gates the runs path behind __STATIC__ for the generate-summary mutation", () => {
    const src = readSrc("src/api/hooks.ts");
    expect(src).toContain("CHAPTER_SUMMARY_RUNS_PATH");
    expect(src).toContain("__STATIC__ ? \"\" : \"/api/v1/runs\"");
  });

  it("the read endpoint string is present (read paths are static-safe)", () => {
    const src = readSrc("src/api/hooks.ts");
    expect(src).toContain("/api/v1/chapters/");
    expect(src).toContain("/summaries");
  });
});
