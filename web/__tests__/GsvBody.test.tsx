import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import type { GsvChapterPayload } from "../src/api/hooks";
import { GsvBody } from "../src/components/gsv/GsvBody";

describe("GsvBody", () => {
  it("pretty-prints JSON content in a <pre>", () => {
    const payload: GsvChapterPayload = {
      format: "json",
      data: {
        chapter: 5,
        coverage: {
          chapter: 5,
          total_sentences: 71,
          total_red_letter_sentences: 68,
          ranked_red_letter_sentences: 0,
          unresolved_ties: 0,
        },
        sentences: [],
      },
    };
    render(<GsvBody payload={payload} />);
    const pre = screen.getByTestId("gsv-body-json");
    expect(pre.tagName).toBe("PRE");
    // Pretty-printed (2-space indent → newlines + spaces)
    expect(pre.textContent).toContain('"chapter": 5');
    expect(pre.textContent).toContain("\n");
  });

  it("preserves whitespace for plain text content", () => {
    const payload: GsvChapterPayload = {
      format: "text",
      data: "Matthew 5\n\nFirst paragraph.\n\nSecond paragraph.\n",
    };
    render(<GsvBody payload={payload} />);
    const pre = screen.getByTestId("gsv-body-text");
    expect(pre.tagName).toBe("PRE");
    expect(pre.textContent).toContain("First paragraph.");
    expect(pre.textContent).toContain("Second paragraph.");
  });

  it("renders Markdown — chapter heading becomes <h1>", () => {
    const payload: GsvChapterPayload = {
      format: "markdown",
      data: "# Matthew 5\n\nA paragraph.",
    };
    render(<GsvBody payload={payload} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Matthew 5");
  });
});
