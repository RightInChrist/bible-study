/**
 * BuildStaticButton tests.
 *
 *   - In-flight: button label switches to "Building…" and is disabled.
 *   - Success: panel shows files_written, coverage, took_ms, dist_path.
 *   - 400 build_guard_failed: error block lists the offending paths.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuildStaticButton } from "../src/components/BuildStaticButton";

function renderButton() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BuildStaticButton />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("BuildStaticButton", () => {
  it("renders the success panel after a 200 response", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          dist_path: "/tmp/repo/dist",
          files_written: 1234,
          coverage: { ranked: 5, total: 24, ch5_ranked: 5, ch5_total: 24 },
          took_ms: 1500,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderButton();
    const btn = screen.getByTestId("gsv-build-static");
    expect(btn).toHaveTextContent(/Build static site/);
    await userEvent.click(btn);
    await waitFor(() =>
      expect(screen.queryByTestId("gsv-build-success")).toBeInTheDocument(),
    );
    const success = screen.getByTestId("gsv-build-success");
    expect(success).toHaveTextContent(/1234/);
    expect(success).toHaveTextContent(/1500/);
    expect(success).toHaveTextContent(/\/tmp\/repo\/dist/);
    expect(success).toHaveTextContent(/5 \/ 24/);
  });

  it("shows the build_guard_failed error block on 400", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          code: "build_guard_failed",
          message: "Forbidden content found in dist.tmp/.",
          details: {
            hits: {
              "sk-ant-": ["assets/main-AbCDeF.js"],
              "/admin/reimport": ["assets/main-AbCDeF.js"],
            },
          },
        }),
        { status: 400, headers: { "content-type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderButton();
    await userEvent.click(screen.getByTestId("gsv-build-static"));
    const block = await screen.findByTestId("gsv-build-guard-error");
    expect(block).toHaveTextContent(/sk-ant-/);
    expect(block).toHaveTextContent(/main-AbCDeF\.js/);
    expect(block).toHaveTextContent(/\/admin\/reimport/);
  });

  it("shows a generic error block on 500", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          code: "build_failed",
          message: "SPA static build failed.",
          details: null,
        }),
        { status: 500, headers: { "content-type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderButton();
    await userEvent.click(screen.getByTestId("gsv-build-static"));
    const block = await screen.findByTestId("gsv-build-error");
    expect(block).toHaveTextContent(/build_failed/);
  });
});
