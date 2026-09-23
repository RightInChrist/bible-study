import { Navigate, Route, Routes } from "react-router-dom";

import { StatusPill } from "./components/StatusPill";
import { ChapterPage } from "./routes/ChapterPage";
import { ChapterSummaryPage } from "./routes/ChapterSummaryPage";
import { GsvPage } from "./routes/GsvPage";
import { OrphanResolutionPage } from "./routes/OrphanResolutionPage";
import { RankPage } from "./routes/RankPage";
import { RedLetterEditorPage } from "./routes/RedLetterEditorPage";
import { SentencePage } from "./routes/SentencePage";

// Admin route path lifted to a build-time constant. In static mode
// (`__STATIC__ === true`) Vite's define-replace inlines `""` and the
// `<Route path="" />` produces dead JSX whose string literal can be
// eliminated by the minifier. PLAN §Build-time guards grep for `/admin/`
// substrings in `dist/` so this string must not survive the build.
const ADMIN_ORPHANS_ROUTE: string = __STATIC__ ? "" : "/admin/orphans";
const RED_LETTER_EDITOR_ROUTE: string = __STATIC__
  ? ""
  : "/red-letter/chapter/:chapter/edit";

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-title">Bible Study · Matthew</div>
        <StatusPill />
      </header>
      <Routes>
        <Route path="/" element={<Navigate to="/chapter/1" replace />} />
        <Route path="/chapter/:chapter" element={<ChapterPage />} />
        <Route path="/chapter/:chapter/summary" element={<ChapterSummaryPage />} />
        <Route path="/gsv/:chapter" element={<GsvPage />} />
        <Route path="/sentence/:id/rank" element={<RankPage />} />
        <Route path="/sentence/:id" element={<SentencePage />} />
        {__STATIC__ ? null : (
          <Route path={ADMIN_ORPHANS_ROUTE} element={<OrphanResolutionPage />} />
        )}
        {__STATIC__ ? null : (
          <Route path={RED_LETTER_EDITOR_ROUTE} element={<RedLetterEditorPage />} />
        )}
        <Route
          path="*"
          element={
            <div className="empty-block">
              Not found. Try <a href="/chapter/1">Matthew 1</a>.
            </div>
          }
        />
      </Routes>
    </div>
  );
}
