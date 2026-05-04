/**
 * `<AuthOnly>` — gates authoring affordances behind the static-build
 * tree-shake (Architect §Web UI Components, Hard decision #14).
 *
 * In dev/local mode (`__STATIC__ === false`) the component renders its
 * children verbatim — the local UI is the working surface and gets every
 * authoring control.
 *
 * In static mode (`__STATIC__ === true`) it returns `null`. Combined with
 * the Vite `define` substitution this collapses any `<AuthOnly>...</AuthOnly>`
 * subtree to dead code; the dependent imports become unused and Vite's
 * tree-shake removes them. The PLAN's Security §Build-time guards verify
 * the strip-out is real (grep guards on `dist/` for write-endpoint URLs
 * and authoring identifiers).
 *
 * No authoring affordances exist yet in this slice (read-only UI). The
 * wrapper is in place so the next slice that adds a write surface
 * (re-import button, range editor, ranking, generate) wraps it without
 * extra plumbing.
 */
import type { ReactNode } from "react";

interface AuthOnlyProps {
  children: ReactNode;
}

export function AuthOnly({ children }: AuthOnlyProps): ReactNode {
  if (__STATIC__) {
    return null;
  }
  return children;
}
