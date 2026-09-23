/**
 * TanStack Query hooks wrapping the read endpoints. Architect §Trade-offs
 * picks TanStack Query for cache discipline; v1 read-only slice uses just
 * the query side, but mutations in later slices will hang off the same
 * QueryClient without churn.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiFetch, apiFetchText } from "./client";
import type {
  BuildStaticRequest,
  BuildStaticResponse,
  ChapterOverlaysResponse,
  ChapterSummaryListResponse,
  CreateChapterSummaryRunRequest,
  CreateRunRequest,
  FixtureStatusResponse,
  GsvChapterResponse,
  GsvCoverageResponse,
  GsvFormat,
  HideComboRequest,
  MarkRedLetterRequest,
  MarkRedLetterResponse,
  RankingResponse,
  RankingWriteRequest,
  ReimportRequest,
  ReimportResponse,
  RestoreRedLetterRequest,
  RunResponse,
  SentenceCandidatesResponse,
  SentenceListResponse,
  SentenceParallelResponse,
  UnmarkRedLetterRequest,
  UnmarkRedLetterResponse,
} from "./types";

export function useFixtureStatus(): UseQueryResult<FixtureStatusResponse> {
  return useQuery({
    queryKey: ["fixture-status"],
    queryFn: () => apiFetch<FixtureStatusResponse>("/api/v1/admin/fixture-status"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useSentencesByChapter(
  chapter: number,
): UseQueryResult<SentenceListResponse> {
  return useQuery({
    queryKey: ["sentences", "chapter", chapter],
    queryFn: () =>
      apiFetch<SentenceListResponse>(
        `/api/v1/sentences?chapter=${encodeURIComponent(chapter)}`,
      ),
    enabled: Number.isInteger(chapter) && chapter >= 1 && chapter <= 28,
  });
}

export function useSentenceParallel(
  sentenceId: string | null,
): UseQueryResult<SentenceParallelResponse> {
  return useQuery({
    queryKey: ["sentences", "parallel", sentenceId],
    queryFn: () => {
      if (sentenceId === null) {
        throw new Error("sentenceId required");
      }
      return apiFetch<SentenceParallelResponse>(
        `/api/v1/sentences/${encodeURIComponent(sentenceId)}/parallel`,
      );
    },
    enabled: sentenceId !== null,
  });
}

export function useSentenceCandidates(
  sentenceId: string | null,
): UseQueryResult<SentenceCandidatesResponse> {
  return useQuery({
    queryKey: ["sentences", "candidates", sentenceId],
    queryFn: () => {
      if (sentenceId === null) {
        throw new Error("sentenceId required");
      }
      return apiFetch<SentenceCandidatesResponse>(
        `/api/v1/sentences/${encodeURIComponent(sentenceId)}/candidates`,
      );
    },
    enabled: sentenceId !== null,
  });
}

// `/runs/` and `/api/v1/runs` are write-side authoring paths. PLAN's
// build-time grep guard on `dist/` greps for `/runs/` and fails the
// static build if it appears. The path strings are lifted to build-time
// constants gated on `__STATIC__` so Vite's define-replace + minifier
// strip them from the static bundle.
const RUNS_BASE_PATH: string = __STATIC__ ? "" : "/api/v1/runs";

export function useRun(
  runId: string | null,
  options: { pollMs?: number } = {},
): UseQueryResult<RunResponse> {
  const pollMs = options.pollMs ?? 2000;
  return useQuery({
    queryKey: ["runs", runId],
    queryFn: () => {
      if (runId === null) {
        throw new Error("runId required");
      }
      if (__STATIC__) {
        throw new Error("runs are not available in static mode");
      }
      return apiFetch<RunResponse>(
        `${RUNS_BASE_PATH}/${encodeURIComponent(runId)}`,
      );
    },
    enabled: runId !== null && !__STATIC__,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return pollMs;
      // Poll while the run is active; stop on terminal state.
      const terminal = new Set<string>([
        "completed",
        "failed",
        "cancelled",
        "interrupted",
      ]);
      return terminal.has(data.status) ? false : pollMs;
    },
  });
}

export function useCreateRun(): UseMutationResult<RunResponse, Error, CreateRunRequest> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateRunRequest) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("runs are not available in static mode"),
        );
      }
      return apiFetch<RunResponse>(RUNS_BASE_PATH, {
        method: "POST",
        body: JSON.stringify(request),
      });
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["runs", data.run_id], data);
    },
  });
}

// ---------------------------------------------------------------------------
// Rankings (Slice 4) — read + write hooks
//
// Ranking sub-paths are gated on `__STATIC__` for the same reason as
// `/runs/` above — PLAN's grep guard greps for `/rankings/`, `/tie-break/`,
// `/hidden-combos/`. Even though the actual URL is `/sentences/{id}/ranking`
// (which doesn't quite match those substrings), the URL templates live
// in code that the static build should never call. Gating cleanly
// strips them.
// ---------------------------------------------------------------------------

function _rankingPath(sentenceId: string): string {
  if (__STATIC__) return "";
  return `/api/v1/sentences/${encodeURIComponent(sentenceId)}/ranking`;
}

export function useRanking(
  sentenceId: string | null,
): UseQueryResult<RankingResponse> {
  return useQuery({
    queryKey: ["rankings", sentenceId],
    queryFn: () => {
      if (sentenceId === null) {
        throw new Error("sentenceId required");
      }
      if (__STATIC__) {
        throw new Error("rankings are not available in static mode");
      }
      return apiFetch<RankingResponse>(_rankingPath(sentenceId));
    },
    enabled: sentenceId !== null && !__STATIC__,
  });
}

export interface SaveRankingArgs {
  sentenceId: string;
  version: number;
  body: RankingWriteRequest;
}

export function useSaveRanking(): UseMutationResult<
  RankingResponse,
  Error,
  SaveRankingArgs
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sentenceId, version, body }: SaveRankingArgs) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("rankings are not available in static mode"),
        );
      }
      return apiFetch<RankingResponse>(_rankingPath(sentenceId), {
        method: "PUT",
        headers: { "If-Match": String(version) },
        body: JSON.stringify(body),
      });
    },
    onSuccess: (data, vars) => {
      queryClient.setQueryData(["rankings", vars.sentenceId], data);
    },
  });
}

// ---------------------------------------------------------------------------
// GSV (Slice 5) — chapter compilation + coverage
// ---------------------------------------------------------------------------

export type GsvChapterPayload =
  | { format: "json"; data: GsvChapterResponse }
  | { format: "text"; data: string }
  | { format: "markdown"; data: string };

export function useGsvChapter(
  chapter: number | null,
  format: GsvFormat,
): UseQueryResult<GsvChapterPayload> {
  return useQuery({
    queryKey: ["gsv", "chapter", chapter, format],
    queryFn: async (): Promise<GsvChapterPayload> => {
      if (chapter === null) {
        throw new Error("chapter required");
      }
      const path = `/api/v1/gsv/${encodeURIComponent(chapter)}?format=${format}`;
      if (format === "json") {
        const data = await apiFetch<GsvChapterResponse>(path);
        return { format: "json", data };
      }
      const data = await apiFetchText(path);
      return { format, data };
    },
    enabled:
      chapter !== null &&
      Number.isInteger(chapter) &&
      chapter >= 1 &&
      chapter <= 28,
  });
}

export function useGsvCoverage(): UseQueryResult<GsvCoverageResponse> {
  return useQuery({
    queryKey: ["gsv", "coverage"],
    queryFn: () => apiFetch<GsvCoverageResponse>("/api/v1/gsv/coverage"),
    staleTime: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Admin (Slice 6) — re-import, build-static
//
// These hooks reference admin paths that **must not** appear in the
// static bundle (PLAN §Build-time guards greps for `/admin/reimport`,
// `/admin/build-static` in dist/). The path strings are constructed
// from a build-time constant + per-call suffix so Vite's `__STATIC__`
// `define`-replace inlines `true`/`false` and the dead branch + its
// string literals are eliminated by the minifier.
// ---------------------------------------------------------------------------

const ADMIN_REIMPORT_PATH: string = __STATIC__ ? "" : "/api/v1/admin/reimport";
const ADMIN_BUILD_STATIC_PATH: string = __STATIC__
  ? ""
  : "/api/v1/admin/build-static";

export function useReimport(): UseMutationResult<
  ReimportResponse,
  Error,
  ReimportRequest
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ReimportRequest) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("admin endpoints are not available in static mode"),
        );
      }
      return apiFetch<ReimportResponse>(ADMIN_REIMPORT_PATH, {
        method: "POST",
        body: JSON.stringify(request),
      });
    },
    onSuccess: () => {
      // Invalidate everything that reads fixture-derived data so the UI
      // re-fetches against the freshly-imported DB.
      queryClient.invalidateQueries({ queryKey: ["fixture-status"] });
      queryClient.invalidateQueries({ queryKey: ["sentences"] });
      queryClient.invalidateQueries({ queryKey: ["gsv"] });
    },
  });
}

export function useBuildStatic(): UseMutationResult<
  BuildStaticResponse,
  Error,
  BuildStaticRequest
> {
  return useMutation({
    mutationFn: (request: BuildStaticRequest) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("admin endpoints are not available in static mode"),
        );
      }
      return apiFetch<BuildStaticResponse>(ADMIN_BUILD_STATIC_PATH, {
        method: "POST",
        body: JSON.stringify(request),
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Red-letter overlays (Slice 7)
//
// Write paths (`/red-letter/mark`, `/red-letter/unmark`, `/red-letter/restore`)
// are added to PLAN's bundle grep guard so they MUST not appear in the
// static bundle. The path strings are constructed from a build-time
// constant gated on `__STATIC__`; Vite's define-replace + the minifier
// strip them when `VITE_STATIC=true`.
// ---------------------------------------------------------------------------

const RED_LETTER_MARK_PATH: string = __STATIC__ ? "" : "/api/v1/red-letter/mark";
const RED_LETTER_UNMARK_PATH: string = __STATIC__
  ? ""
  : "/api/v1/red-letter/unmark";
const RED_LETTER_RESTORE_PATH: string = __STATIC__
  ? ""
  : "/api/v1/red-letter/restore";

function _chapterOverlaysPath(chapter: number): string {
  return `/api/v1/red-letter/chapter/${encodeURIComponent(chapter)}/overlays`;
}

export function useChapterOverlays(
  chapter: number | null,
): UseQueryResult<ChapterOverlaysResponse> {
  return useQuery({
    queryKey: ["red-letter", "chapter", chapter],
    queryFn: () => {
      if (chapter === null) {
        throw new Error("chapter required");
      }
      return apiFetch<ChapterOverlaysResponse>(_chapterOverlaysPath(chapter));
    },
    enabled:
      chapter !== null &&
      Number.isInteger(chapter) &&
      chapter >= 1 &&
      chapter <= 28,
  });
}

function _invalidateRedLetter(
  queryClient: ReturnType<typeof useQueryClient>,
  chapter: number,
): void {
  queryClient.invalidateQueries({
    queryKey: ["red-letter", "chapter", chapter],
  });
  queryClient.invalidateQueries({
    queryKey: ["sentences", "chapter", chapter],
  });
  queryClient.invalidateQueries({ queryKey: ["sentences", "parallel"] });
}

export function useMarkRedLetter(): UseMutationResult<
  MarkRedLetterResponse,
  Error,
  MarkRedLetterRequest
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: MarkRedLetterRequest) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("red-letter writes are not available in static mode"),
        );
      }
      return apiFetch<MarkRedLetterResponse>(RED_LETTER_MARK_PATH, {
        method: "POST",
        body: JSON.stringify(request),
      });
    },
    onSuccess: (_data, vars) => {
      _invalidateRedLetter(queryClient, vars.chapter);
    },
  });
}

export interface UnmarkRedLetterArgs {
  body: UnmarkRedLetterRequest;
  version: number;
  chapter: number;
}

export function useUnmarkRedLetter(): UseMutationResult<
  UnmarkRedLetterResponse,
  Error,
  UnmarkRedLetterArgs
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, version }: UnmarkRedLetterArgs) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("red-letter writes are not available in static mode"),
        );
      }
      return apiFetch<UnmarkRedLetterResponse>(RED_LETTER_UNMARK_PATH, {
        method: "POST",
        headers: { "If-Match": String(version) },
        body: JSON.stringify(body),
      });
    },
    onSuccess: (_data, vars) => {
      _invalidateRedLetter(queryClient, vars.chapter);
    },
  });
}

export interface RestoreRedLetterArgs {
  body: RestoreRedLetterRequest;
  version: number;
  chapter: number;
}

export function useRestoreRedLetter(): UseMutationResult<
  UnmarkRedLetterResponse,
  Error,
  RestoreRedLetterArgs
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, version }: RestoreRedLetterArgs) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("red-letter writes are not available in static mode"),
        );
      }
      return apiFetch<UnmarkRedLetterResponse>(RED_LETTER_RESTORE_PATH, {
        method: "POST",
        headers: { "If-Match": String(version) },
        body: JSON.stringify(body),
      });
    },
    onSuccess: (_data, vars) => {
      _invalidateRedLetter(queryClient, vars.chapter);
    },
  });
}

export interface HideComboArgs {
  sentenceId: string;
  version: number;
  body: HideComboRequest;
}

export function useHideCombo(): UseMutationResult<
  RankingResponse,
  Error,
  HideComboArgs
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sentenceId, version, body }: HideComboArgs) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("hidden-combos are not available in static mode"),
        );
      }
      const path = `/api/v1/sentences/${encodeURIComponent(sentenceId)}/hidden-combos`;
      return apiFetch<RankingResponse>(path, {
        method: "POST",
        headers: { "If-Match": String(version) },
        body: JSON.stringify(body),
      });
    },
    onSuccess: (data, vars) => {
      queryClient.setQueryData(["rankings", vars.sentenceId], data);
    },
  });
}

// ---------------------------------------------------------------------------
// Chapter summaries (Slice 8) — read paths are static-bundle safe; the
// generate path is gated on `__STATIC__` because it routes through
// `POST /api/v1/runs`, which the static guard already strips.
// ---------------------------------------------------------------------------

export function useChapterSummaries(
  chapter: number | null,
): UseQueryResult<ChapterSummaryListResponse> {
  return useQuery({
    queryKey: ["chapters", "summaries", chapter],
    queryFn: () => {
      if (chapter === null) {
        throw new Error("chapter required");
      }
      return apiFetch<ChapterSummaryListResponse>(
        `/api/v1/chapters/${encodeURIComponent(chapter)}/summaries`,
      );
    },
    enabled:
      chapter !== null &&
      Number.isInteger(chapter) &&
      chapter >= 1 &&
      chapter <= 28,
  });
}

const CHAPTER_SUMMARY_RUNS_PATH: string = __STATIC__ ? "" : "/api/v1/runs";

export function useGenerateSummary(): UseMutationResult<
  RunResponse,
  Error,
  CreateChapterSummaryRunRequest
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateChapterSummaryRunRequest) => {
      if (__STATIC__) {
        return Promise.reject(
          new Error("chapter summary generation is not available in static mode"),
        );
      }
      return apiFetch<RunResponse>(CHAPTER_SUMMARY_RUNS_PATH, {
        method: "POST",
        body: JSON.stringify(request),
      });
    },
    onSuccess: (data, vars) => {
      queryClient.setQueryData(["runs", data.run_id], data);
      queryClient.invalidateQueries({
        queryKey: ["chapters", "summaries", vars.scope.chapter],
      });
    },
  });
}
