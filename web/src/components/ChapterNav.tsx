/**
 * Chapter selector for Matthew (1–28), Designer Flow 1 step 3.
 *
 * Renders prev / next buttons plus the chapter list. The list intentionally
 * fits on one row (28 small buttons) so Gavin can pick any chapter
 * without a popover.
 */
import { Link, useNavigate } from "react-router-dom";

const CHAPTERS: readonly number[] = Array.from({ length: 28 }, (_, i) => i + 1);

interface Props {
  chapter: number;
}

export function ChapterNav({ chapter }: Props) {
  const navigate = useNavigate();
  const prev = chapter > 1 ? chapter - 1 : null;
  const next = chapter < 28 ? chapter + 1 : null;
  return (
    <nav className="chapter-nav" aria-label="Matthew chapter selector">
      <span className="chapter-nav__label">Matthew</span>
      <button
        type="button"
        className="chapter-nav__btn"
        onClick={() => prev !== null && navigate(`/chapter/${prev}`)}
        disabled={prev === null}
        aria-label="Previous chapter"
      >
        ← prev
      </button>
      <button
        type="button"
        className="chapter-nav__btn"
        onClick={() => next !== null && navigate(`/chapter/${next}`)}
        disabled={next === null}
        aria-label="Next chapter"
      >
        next →
      </button>
      {CHAPTERS.map((n) => (
        <Link
          key={n}
          to={`/chapter/${n}`}
          className="chapter-nav__btn"
          aria-current={n === chapter ? "page" : undefined}
        >
          {n}
        </Link>
      ))}
    </nav>
  );
}
