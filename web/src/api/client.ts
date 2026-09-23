/**
 * Tiny HTTP client.
 *
 * Reads are unconditional GETs (Architect §HTTP API). Non-GET writes
 * include the `X-Requested-By: bible-study-ui` header per Security
 * §CSRF — no writes happen in this slice but the helper sets the header
 * anyway so future mutations cannot forget.
 *
 * In static mode (`__STATIC__ = true`) the URL `/api/v1/foo` is mapped
 * to the file path `/api/v1/foo.json` under the deploy root. The actual
 * static-export pipeline lands in a later slice; this branch is wired
 * now so the components don't grow a runtime dependency on the FastAPI
 * server.
 */
import type { ErrorResponse } from "./types";

const STATIC_BUILD = typeof __STATIC__ !== "undefined" && __STATIC__;

export class ApiError extends Error {
  readonly status: number;
  readonly body: ErrorResponse | null;
  constructor(status: number, body: ErrorResponse | null, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function resolveUrl(path: string): string {
  if (!path.startsWith("/api/")) {
    throw new Error(`apiFetch path must start with /api/, got ${path}`);
  }
  if (STATIC_BUILD) {
    const [base, query] = path.split("?", 2);
    // Sentences-by-chapter is emitted as one file per chapter under
    // `/api/v1/sentences/chapter-N.json` — see api/admin/service.py
    // _emit_snapshots. The dev API takes `?chapter=N`; the static
    // resolver translates `?chapter=N` → the per-chapter file.
    if (base === "/api/v1/sentences" && typeof query === "string") {
      const params = new URLSearchParams(query);
      const chapter = params.get("chapter");
      if (chapter !== null) {
        return `/api/v1/sentences/chapter-${chapter}.json`;
      }
    }
    const queryFragment = typeof query === "string" ? `?${query}` : "";
    return `${base}.json${queryFragment}`;
  }
  return path;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-Requested-By", "bible-study-ui");
    if (!headers.has("Content-Type") && init.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
  }
  const response = await fetch(resolveUrl(path), { ...init, headers });
  const text = await response.text();
  let parsed: unknown = null;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!response.ok) {
    const body = isErrorResponse(parsed) ? parsed : null;
    const message = body?.message ?? `HTTP ${response.status} for ${path}`;
    throw new ApiError(response.status, body, message);
  }
  return parsed as T;
}

/**
 * Variant of `apiFetch` that returns the raw response body as a string.
 * Used by GSV's text and markdown formats — the server returns
 * `text/plain` / `text/markdown` bodies, not JSON. Errors still come
 * back as the canonical `ErrorResponse` JSON shape (e.g. 409
 * `unresolved_ties`), which we parse and surface via `ApiError` so the
 * caller can introspect `err.body.code`.
 */
export async function apiFetchText(
  path: string,
  init: RequestInit = {},
): Promise<string> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  // Don't force Accept: application/json — let the server pick from
  // text/plain / text/markdown / application/json based on the route's
  // content-type. The route handler returns JSON only for errors.
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-Requested-By", "bible-study-ui");
    if (!headers.has("Content-Type") && init.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
  }
  const response = await fetch(resolveUrl(path), { ...init, headers });
  const text = await response.text();
  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
    const body = isErrorResponse(parsed) ? parsed : null;
    const message = body?.message ?? `HTTP ${response.status} for ${path}`;
    throw new ApiError(response.status, body, message);
  }
  return text;
}

function isErrorResponse(x: unknown): x is ErrorResponse {
  if (typeof x !== "object" || x === null) return false;
  const candidate = x as Record<string, unknown>;
  return typeof candidate.code === "string" && typeof candidate.message === "string";
}
