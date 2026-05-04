import { Navigate, Route, Routes } from "react-router-dom";

import { StatusPill } from "./components/StatusPill";
import { ChapterPage } from "./routes/ChapterPage";
import { SentencePage } from "./routes/SentencePage";

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
        <Route path="/sentence/:id" element={<SentencePage />} />
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
