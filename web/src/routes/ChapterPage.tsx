import { useParams } from "react-router-dom";

import { ChapterNav } from "../components/ChapterNav";
import { ParallelReader } from "../components/ParallelReader";

const DEFAULT_CHAPTER = 1;

function parseChapter(raw: string | undefined): number {
  if (!raw) return DEFAULT_CHAPTER;
  const n = Number.parseInt(raw, 10);
  if (!Number.isInteger(n) || n < 1 || n > 28) return DEFAULT_CHAPTER;
  return n;
}

export function ChapterPage() {
  const { chapter: chapterParam } = useParams<{ chapter: string }>();
  const chapter = parseChapter(chapterParam);
  return (
    <>
      <ChapterNav chapter={chapter} />
      <ParallelReader chapter={chapter} />
    </>
  );
}
