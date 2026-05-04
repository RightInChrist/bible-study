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

import { apiFetch } from "./client";
import type {
  CreateRunRequest,
  FixtureStatusResponse,
  RunResponse,
  SentenceCandidatesResponse,
  SentenceListResponse,
  SentenceParallelResponse,
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
      return apiFetch<RunResponse>(`/api/v1/runs/${encodeURIComponent(runId)}`);
    },
    enabled: runId !== null,
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
    mutationFn: (request: CreateRunRequest) =>
      apiFetch<RunResponse>("/api/v1/runs", {
        method: "POST",
        body: JSON.stringify(request),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["runs", data.run_id], data);
    },
  });
}
