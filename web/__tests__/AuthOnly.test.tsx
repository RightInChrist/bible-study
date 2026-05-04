/**
 * Confirms the `<AuthOnly>` wrapper renders children in dev/local mode
 * (the only mode the test environment exercises). The static-mode
 * strip-out is verified by Vite's tree-shake at build time and the PLAN
 * §Build-time guards grep checks; the wrapper's runtime contract — that
 * children render normally when `__STATIC__` is false — is what unit
 * tests pin down.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { AuthOnly } from "../src/components/AuthOnly";

describe("AuthOnly", () => {
  it("renders its children in dev/local mode", () => {
    render(
      <AuthOnly>
        <button>secret authoring control</button>
      </AuthOnly>,
    );
    expect(screen.getByRole("button", { name: /secret authoring/ })).toBeInTheDocument();
  });
});
