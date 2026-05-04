/**
 * Deep-link route `/sentence/:id` — loads the sentence's parallel data,
 * resolves its chapter, and renders the same parallel reader scrolled to
 * the focal sentence. SBLGNT sentence IDs are `mat-${chapter}-${ordinal}`
 * (Architect §Sentence identity); we parse rather than fetch when we can.
 */
import { useParams } from "react-router-dom";

import { ChapterNav } from "../components/ChapterNav";
import { ParallelReader } from "../components/ParallelReader";
import { useSentenceParallel } from "../api/hooks";
import { ApiError } from "../api/client";

interface ParsedId {
  chapter: number | null;
  raw: string;
}

function parseSentenceId(id: string | undefined): ParsedId {
  if (!id) return { chapter: null, raw: "" };
  const match = /^mat-(\d+)-(\d+)$/.exec(id);
  if (!match) return { chapter: null, raw: id };
  const chapter = Number.parseInt(match[1] ?? "0", 10);
  if (!Number.isInteger(chapter) || chapter < 1 || chapter > 28) {
    return { chapter: null, raw: id };
  }
  return { chapter, raw: id };
}

export function SentencePage() {
  const { id } = useParams<{ id: string }>();
  const parsed = parseSentenceId(id);
  // Trigger fetch so a non-existent sentence id surfaces the 404 path.
  const query = useSentenceParallel(parsed.raw || null);

  if (parsed.chapter === null) {
    return (
      <div className="error-block" role="alert">
        <span className="error-block__code">invalid_sentence_id</span>
        <strong>Sentence ID {parsed.raw || "(empty)"} is malformed.</strong>
        <p>
          Expected the canonical form <code>mat-{"{chapter}-{ordinal}"}</code>.
        </p>
      </div>
    );
  }

  if (query.isError && query.error instanceof ApiError && query.error.status === 404) {
    return (
      <div className="error-block" role="alert">
        <span className="error-block__code">{query.error.body?.code ?? "sentence_not_found"}</span>
        <strong>
          Sentence <code>{parsed.raw}</code> is not in the current fixture set.
        </strong>
        <p>
          {query.error.body?.message ??
            "It may have been removed or the fixture version changed. Open Read to navigate."}
        </p>
      </div>
    );
  }

  return (
    <>
      <ChapterNav chapter={parsed.chapter} />
      <ParallelReader chapter={parsed.chapter} initialSentenceId={parsed.raw} />
    </>
  );
}
