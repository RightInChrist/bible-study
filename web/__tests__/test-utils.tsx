/**
 * Shared test scaffolding: a fresh QueryClient per render plus a memory
 * router so route-driven components don't blow up.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { render, type RenderResult } from "@testing-library/react";

interface RenderOpts {
  initialEntries?: string[];
}

export function renderWithProviders(ui: ReactNode, opts: RenderOpts = {}): RenderResult {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={opts.initialEntries ?? ["/"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}
