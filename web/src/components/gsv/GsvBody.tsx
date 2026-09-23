/**
 * Renders the GSV body in one of three formats.
 *
 *  - JSON: pretty-printed JSON in a <pre> block.
 *  - Plain text: <pre> with white-space preserved.
 *  - Markdown: react-markdown with a small <h1> override so the chapter
 *    heading is testable.
 */
import ReactMarkdown from "react-markdown";

import type { GsvChapterPayload } from "../../api/hooks";

interface Props {
  payload: GsvChapterPayload;
}

export function GsvBody({ payload }: Props) {
  if (payload.format === "json") {
    return (
      <pre className="gsv-body gsv-body--json" data-testid="gsv-body-json">
        {JSON.stringify(payload.data, null, 2)}
      </pre>
    );
  }
  if (payload.format === "text") {
    return (
      <pre className="gsv-body gsv-body--text" data-testid="gsv-body-text">
        {payload.data}
      </pre>
    );
  }
  return (
    <div
      className="gsv-body gsv-body--markdown"
      data-testid="gsv-body-markdown"
    >
      <ReactMarkdown>{payload.data}</ReactMarkdown>
    </div>
  );
}
