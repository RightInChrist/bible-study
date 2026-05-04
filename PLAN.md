# PLAN

## Security

### Threat model

This is a single-user localhost-only tool. The trust boundary is "Gavin's logged-in OS session on his own machine." Anything inside that boundary is trusted. The interesting threats are accidents and supply-chain, not adversarial network traffic.

**Actors considered:**

- **Network neighbour on Gavin's LAN / coffee-shop Wi-Fi** — capability: send TCP to Gavin's IP. Goal: read fixtures / cause Anthropic spend / probe the API. Mitigation: the FastAPI server MUST bind `127.0.0.1`, never `0.0.0.0`, never `::`. The launcher refuses to start if the configured `BIND_HOST` is anything else.
- **Browser tab on a website Gavin visits** — capability: cross-origin requests / DNS rebinding to `localhost`. Goal: invoke admin endpoints (re-import, build-static), trigger Anthropic spend, exfiltrate ranking data. Mitigation: CORS allow-list to the local Vite dev origin only; reject `Host:` headers that aren't `127.0.0.1` / `localhost` (DNS-rebinding defence); state-changing endpoints require a same-origin custom header (`X-Requested-By: bible-study-ui`) that browsers won't add cross-origin without preflight.
- **Future maintainer pushing a public branch** — capability: accidental commit of `.env`, SQLite snapshot, or `dist/` containing a baked-in API key. Goal: not malicious — but the result is leaked credentials. Mitigation: gitignore + pre-commit secret scanner, key-handling rules below, `dist/` audit step.
- **Upstream fixture host (`bereanbible.com`, SBLGNT mirror)** — capability: serve a tampered file at the pinned URL on a future re-fetch. Goal: prompt-inject Claude through a poisoned BIB row, or corrupt the GSV. Mitigation: SHA-256 pinning per file in `fixtures/manifest.json`; importer aborts on hash mismatch with a clear error and refuses to write to SQLite.
- **Anthropic API itself** — capability: return adversarial text in a candidate. Goal: trick downstream rendering / export. Mitigation: candidate text is treated as untrusted user content — never executed, never interpolated into HTML without React's default escaping, never used to compose a follow-up prompt without quoting.
- **Gavin himself, accidentally** — capability: hit "Build static site," `git add dist/`, `make dev` on a public network, paste an API key into a prompt fixture, run with `BIND_HOST=0.0.0.0` to test from his phone. Goal: none — but each of these leaks. The single-user framing does not exempt us from defending against accidental footguns.

**Explicit non-goals (v1):**

- A malicious local user who already has shell access on Gavin's machine. They can read `.env` and the SQLite file directly; we do not encrypt at rest.
- A compromised npm/PyPI dependency with a typosquat or postinstall script. Mitigated only by lockfiles + pinned versions; no runtime sandboxing.
- Side-channel attacks (timing, cache) on a localhost API.
- Browser-side XSS hardening beyond React's default escaping and a basic CSP — no nonces, no Trusted Types in v1.
- Multi-user authorization. Every endpoint is reachable by anyone who can reach the loopback socket.

### Authn / Authz

- **No authentication.** Per Manager scope. Authorization is "you can reach `127.0.0.1:8000`."
- **Loopback bind is the security boundary.** This is a hard rule, not a default.
- **CSRF / cross-origin defence (since there is no auth):** state-changing routes (`POST /api/v1/admin/*`, `POST /api/v1/runs`, `POST /api/v1/runs/{id}/cancel`, `POST /api/v1/runs/{id}/resume`, every ranking/overlay/tie-break/hidden-combo write) check (a) `Origin` is in the allow-list (`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:8000`, `http://127.0.0.1:8000`), and (b) the custom header `X-Requested-By: bible-study-ui` is present. Both are required. Reads are unconditional.
- **Host-header allow-list (DNS-rebinding defence):** middleware rejects requests whose `Host:` header is not `127.0.0.1[:port]` or `localhost[:port]` with `400 invalid_host`. Without this, a malicious site could resolve `attacker.com` to `127.0.0.1` and reach the API from a browser.
- **Static site has no auth surface at all** because it has no write surface — but this only holds if the Vite-time strip-out is real (see Data exposure below).

### Test-only admin hooks (env-gated, mounted only in development)

The eval seeds (Reliability `bs/fixture/`, Data `bs/import/`) need to mutate fixture bytes and seed overlay rows from outside the API's normal write surface. **Routes, request/response schemas, and the env-gate mechanism are pinned here in Security** because the env-gate is the security control that prevents these endpoints from existing on staging or production. **Fixture-isolation semantics (idempotent rebuild, dirty-state recovery) are pinned in Data §Test-only admin hook fixture isolation**; **eval invocation patterns** (where each seed posts these requests) are pinned in Reliability §Eval coverage.

- **Mount discipline**: the test-only admin router (`api/admin/test_only/`) is included in the FastAPI app **only** when `settings.env == "development"`. The check is on `==` (allowlist), not `!=` (denylist) — a future env value (`qa`, `preview`, etc.) is deny-by-default until explicitly added. In `staging` and `production` the router is not registered at all (not merely returning 403 — the routes do not exist in the OpenAPI surface).
- **Belt-and-braces middleware**: even in `development`, every test-only route additionally checks `settings.env == "development"` at request time and returns `404 not_found` if the gate has been flipped at runtime (e.g. a misconfigured env reload). The 404 (not 403) makes the routes indistinguishable from non-existent ones to a probing caller.
- **Routes**:
  - `POST /api/v1/admin/test-only/mutate-fixture-byte` — request `MutateFixtureByteRequest { fixture_path: str, byte_offset: int }`; response `MutateFixtureByteResponse { previous_byte: int, new_byte: int, new_sha256: str }`. Flips one byte in the named fixture file (path is constrained to `fixtures/**` and rejected with `400 invalid_fixture_path` otherwise) so the next `POST /admin/reimport` triggers `fixture_hash_mismatch`. Used by `bs/fixture/`. The handler captures the original byte so a sibling restore endpoint can revert it (see Data's fixture-isolation pattern for the cleanup contract).
  - `POST /api/v1/admin/test-only/restore-fixture-byte` — request `RestoreFixtureByteRequest { fixture_path: str, byte_offset: int, original_byte: int }`; response `RestoreFixtureByteResponse { restored: bool, current_sha256: str }`. Idempotent: if the byte is already at `original_byte`, returns `restored: false`.
  - `POST /api/v1/admin/test-only/seed-overlay` — request `SeedOverlayRequest { start_sentence_id: str, start_word_offset: int, end_sentence_id: str, end_word_offset: int, origin: Literal["berean","manual"], operation: Literal["create","split","merge","extend","retract","reject"] = "create" }`; response `SeedOverlayResponse { overlay_id: int, version: int }`. Inserts a `red_letter_overlays` row directly so `bs/import/` can assert overlay survival across reimport. Uses the same OCC contract (`version=1` on insert) as the production write path.
  - `POST /api/v1/admin/test-only/reset-fixture-state` — request `ResetFixtureStateRequest { confirm: bool }`; response `ResetFixtureStateResponse { fixtures_restored: int, overlays_deleted: int }`. The fixture-isolation **cleanup hook**: re-reads `fixtures/manifest.json`, restores any byte-mutated files to their pinned hash from the immutable manifest backup (Data §Test-only admin hook fixture isolation pins the backup mechanism), and deletes every overlay row tagged with `origin='manual'` and a test-only marker. Called by every eval seed in its teardown step. `confirm=true` is required to prevent a stray hit.
- **Logging discipline applies**: test-only routes log under `event=admin.test_only.<route>` and the request body is logged in full (no redaction) because by the env-gate they only ever exist in development where there are no production secrets to leak. `fixture_path`, `byte_offset`, and overlay coordinates are not sensitive in dev.
- **No CSRF / `X-Requested-By` requirement on test-only routes**: the eval harness drives them programmatically without a browser; requiring the same-origin custom header would force the harness to spoof it pointlessly. Any CSRF concern is covered by the env-gate — the routes are unreachable from a real user's browser because they don't exist in production / staging.

The eval seeds invoke the cleanup hook in their teardown step (Reliability §Eval coverage); without that, a crashed eval leaves `fixtures/` with a flipped byte and the next eval (or the next `make import`) fails with `fixture_hash_mismatch`.

### Data exposure

- **Anthropic API key (`ANTHROPIC_API_KEY`) handling:**
  - Loaded from `.env` (gitignored) via `pydantic-settings`. `.env.example` is committed with a placeholder, never the real value.
  - Lives only in the FastAPI process memory. Never serialized to a response, never logged, never echoed in error messages. The `api/claude/` module is the only module that reads it.
  - Configuration printer / `/api/v1/admin/fixture-status` / any debug endpoint redacts the key (returns `***` or omits the field entirely; redaction is on the Pydantic response model, not at log time).
  - Never reaches the React bundle. Vite's `import.meta.env` only exposes `VITE_*`-prefixed vars; we explicitly do not prefix the Anthropic key. A build-time grep guard (see tests) refuses to ship a `dist/` containing the literal `ANTHROPIC_API_KEY` or any string matching `sk-ant-` prefix.
- **`<AuthOnly>` strip-out — code-level, not CSS:** Hard decision #14 says authoring code is stripped at Vite build time, not merely hidden. We must verify this is genuine tree-shaking:
  - `<AuthOnly>` is implemented as a module-level conditional — `if (import.meta.env.VITE_STATIC === 'true') return null;` is **not sufficient** because the wrapped imports still ship in the bundle.
  - Correct shape: a Vite `define` constant `__STATIC__: true`, plus a Babel/SWC plugin or hand-rolled `vite-plugin-conditional` that replaces `<AuthOnly>{X}</AuthOnly>` with `null` at parse time, so `X`'s import statements are never reached and Rollup tree-shakes them. Equivalently, a separate entry point (`main.static.tsx`) that imports only the read-only component subtree.
  - Test (load-bearing): `dist/` static build is grep-asserted to contain zero references to identifiers from `web/components/admin/`, `web/components/generate/`, `web/components/rank/edit/`, and zero references to write-endpoint URLs (`/admin/`, `/runs`, `If-Match`).
- **Static-site export sanitization (`dist/`):**
  - No API keys (assertion: bundle contains no string matching `/sk-ant-[A-Za-z0-9_-]{20,}/`, no occurrence of `ANTHROPIC_API_KEY`).
  - No Anthropic-bound code (assertion: `dist/` contains no reference to `api.anthropic.com`, `@anthropic-ai/sdk`, or any module under `api/claude/`).
  - No admin endpoints in the JSON tree (assertion: `dist/api/v1/admin/` does not exist; `dist/api/v1/runs/` does not exist).
  - No `source_snapshots.payload_json` blobs (the resolved-source bundles can include full SBLGNT spans, which are public, but they can also include prompt-template fragments that may evolve into something we don't want public; the export script writes provenance metadata only — `style_prompt_version`, `source_set_id`, `model`, `generated_at`, `snapshot_hash` — and never the `payload_json` itself).
  - No prompt template bodies in `dist/` (the `fixtures/prompts/*.md` files stay server-side; the static site references prompts by version string only).
  - No `.env`, no SQLite file, no `fixtures/manifest.json` (the manifest can leak retrieval URLs and internal paths). The static builder writes only an explicit allow-list of paths; no `cp -r` of source directories.
- **Logging discipline:**
  - Structured logs (JSON to stdout). Never log: request bodies for Claude calls (may contain prompt templates), full Anthropic responses (may be large), `Authorization` / `X-Api-Key` / any header matching `*key*`, `Set-Cookie`, `.env` contents, `payload_json` blobs.
  - Do log: route, status, duration, `run_id`, `sentence_id`, `candidate_id`, error code (the coarse enum from `generation_run_items.error_code`).
  - On Anthropic error, log only `error_code` + a hash of the failing prompt's `snapshot_hash`, not the prompt body. The `error_message` shown in the UI may quote Anthropic's refusal verbatim (Designer Flow 4 error state) — that's fine for the UI but the log keeps a hash, not the text, to avoid disk-leaking refusal contents that may quote prompt material.
- **Error responses:** `ErrorResponse { code, message, details }` is the only shape. `details` is opt-in per route; never auto-populated with stack traces, file paths, SQL queries, or env-var names. FastAPI's default 500 handler is replaced to return `{code: "internal", message: "internal error"}` with no detail in production-style runs.
- **SQLite file (`data/bible_study.db`)** is gitignored. The Make target ensures it lives under `data/` (never in repo root), and `.gitignore` covers `data/`, `*.db`, `*.db-wal`, `*.db-shm`.
- **`.env` is gitignored**; pre-commit hook scans for `sk-ant-` and known secret prefixes and refuses the commit.
- **`dist/` build artifacts gitignored**: `.gitignore` covers `dist/`, `dist.tmp/`, and `dist.bak/` (the staging and previous-build directories Reliability §Static-site atomic swap creates). Without explicit patterns for `dist.tmp/` and `dist.bak/`, a mid-build crash could leave a partial bundle that's then committed by reflex `git add .`.

### Supply chain & secrets

- **Python deps:** locked via `uv.lock` (or `requirements.lock` if uv is rejected). `pip install --require-hashes` in CI / setup. No unpinned versions.
- **JS deps:** `package-lock.json` committed; `npm ci` (not `npm install`) in setup; `npm audit --audit-level=high` runs in `make test`.
- **Anthropic SDK** pinned to a specific minor; bumps are deliberate.
- **Fixture provenance:** `fixtures/manifest.json` records per file: `name`, `path`, `source_url`, `retrieved_at`, `release_tag`, `sha256`. Importer recomputes SHA-256 on every run and aborts with `code: "fixture_hash_mismatch"` on any divergence — clear error naming the file, expected hash, and actual hash. Refuse to write a single row to SQLite when any hash fails (atomic-or-nothing).
- **Fixture re-fetch is a deliberate event:** there is no "auto-update fixtures" path. Re-fetching is a manual `make fetch-fixtures` (or a documented procedure) that re-downloads, re-hashes, and bumps `manifest.json`'s `version`. Hash mismatches between the new download and the old pinned hash are surfaced in the diff as a manifest change for the developer to review before commit.
- **Secret rotation:** Anthropic key rotation is "edit `.env`, restart the server." Documented in the README. No long-lived keys in CI (we don't run Claude in CI; tests stub the client).
- **`fixtures/prompts/` is part of the supply chain too.** Prompts are version-controlled markdown; a malicious prompt edit could exfiltrate snapshot contents to the model output. Mitigation: prompt edits go through normal code review. The Claude client validates that the prompt's `compatible_source_sets` front-matter includes the chosen `source_set_id` before substituting placeholders — prevents a renamed prompt from silently consuming source data it shouldn't.
- **No telemetry, no auto-update, no remote logging.** The only outbound network call is to `api.anthropic.com`.

### Abuse, rate-limiting, and cost safety

The most realistic "harm" vector for a single-user tool is **Gavin (or a runaway script, or an open browser tab) spending real money on Anthropic by mistake**. Mitigations:

- **Per-run hard cap:** `CreateRunRequest` is rejected if the resolved sentence list exceeds `MAX_SENTENCES_PER_RUN` (default 200, env-overridable). One chapter (~100 sentences) is the realistic max; "all unranked red-letter" is bounded by Matthew's red-letter count.
- **Estimated cost gate:** the runner computes the character-count cost estimate (Architect Flow 4 step 3) before dispatching the first sentence and rejects with `code: "estimated_cost_exceeds_cap"` if `estimated_cost_usd × 1.20` (the +20% upper bound) exceeds `MAX_RUN_COST_USD` (default $5, env-overridable). The UI's Estimate-then-Generate flow already shows the user the number; the server enforces it independently — a UI bypass cannot evade the cap.
- **Daily spend cap:** the runner sums `estimated_cost_usd` across `generation_runs` rows whose `created_at` is within the last 24h (UTC) and rejects new runs with `code: "daily_cost_cap_exceeded"` once the rolling sum × 1.20 would exceed `MAX_DAILY_COST_USD` (default $20). Estimates are recorded on the run row at creation time so this sum is a pure SQL aggregate.
- **In-flight concurrency cap:** `MAX_CONCURRENT_ANTHROPIC_CALLS` (default 4) bounds how many sentences within a run can be in-flight at once. Prevents accidentally-large fan-out from rate-limit-storming Anthropic, and prevents a runaway loop from spending in parallel.
- **Per-IP request-rate limit on POST endpoints:** SlowAPI (or equivalent) at e.g. 60 req/min on `/api/v1/runs*` and `/api/v1/admin/*`. Defends against a misbehaving browser tab in a tight retry loop.
- **Cancel is honoured promptly:** `POST /runs/{id}/cancel` flips the run's status; the dispatcher checks the flag between sentences and stops dispatching. In-flight calls drain (Architect's design: don't waste tokens already in flight).
- **No auto-retry of Anthropic failures.** "Retry this sentence" is an explicit user click that creates a new single-sentence run — no exponential-backoff loop that quietly multiplies spend.

### Build-time / publish-time guards

- **`make dev` refuses to start** if `BIND_HOST` resolves to anything other than `127.0.0.1` or `::1`. Override is an explicit `BIND_HOST_ALLOW_NON_LOCAL=1` env var, documented as "you almost certainly do not want this." The error message names the bound address and the env var.
- **`make build-static` refuses to write `dist/`** if any of the following grep checks fail:
  - `dist/` contains `sk-ant-` or any other configured secret prefix.
  - `dist/` contains `ANTHROPIC_API_KEY` (the literal string).
  - `dist/` contains `api.anthropic.com`.
  - `dist/` contains identifiers from the authoring component subtree (an explicit deny-list maintained alongside `<AuthOnly>`).
  - `dist/api/v1/admin/` exists or `dist/api/v1/runs/` exists.
  - The build runs the checks against the just-written tree and fails (deletes `dist/`) on any hit. The in-UI Build button surfaces the failure via the Designer error-state path.
- **Pre-commit hook** (lightweight): `gitleaks` or equivalent against staged files; refuses commits containing `sk-ant-` prefixes, `.env` content, or files matching `data/*.db*`.
- **CI check:** `dist/` build is run with a synthetic test API key (e.g. `sk-ant-TESTKEY-DO-NOT-USE`) and the resulting `dist/` is grepped for that key — if it appears, the strip-out is broken and CI fails. This is the load-bearing test that proves the Vite tree-shake is real.

### Mitigations summary (threat → mitigation)

| Threat | Mitigation |
|---|---|
| LAN attacker reaching the API | Hard `127.0.0.1` bind, refuse to start otherwise |
| Cross-origin / DNS-rebinding | `Origin` allow-list, `Host` allow-list, `X-Requested-By` custom header |
| Anthropic key in client bundle | `ANTHROPIC_*` not `VITE_*`-prefixed; build-time grep fails on key in `dist/` |
| Authoring code in static site | `<AuthOnly>` is Vite-tree-shaken (separate entry or define-replace), CI grep guard |
| Tampered fixture file | SHA-256 pinned; importer aborts atomic-or-nothing on mismatch |
| Tampered prompt fixture | Code review; `compatible_source_sets` validation at substitution time |
| Anthropic spend runaway | Per-run cap, daily cap, concurrency cap, server-enforced (not UI-only) |
| Browser-tab CSRF | Same-origin custom-header requirement on all writes |
| Accidental secret commit | Gitignore + pre-commit secret scanner |
| Static-site secret leak | Build-step deny-list grep; `dist/` written from explicit allow-list of paths |
| Adversarial Claude output | Treated as untrusted text; React-escaped; never re-prompted without quoting |

### Security-relevant tests and eval-seed entries

Every threat above must have a test. Naming convention: `tests/security/test_<threat>.py`.

1. **`test_bind_refuses_non_loopback`** — start the app with `BIND_HOST=0.0.0.0`, assert it exits non-zero with a message naming the env var.
2. **`test_host_header_rejected`** — request with `Host: evil.com` returns 400 `invalid_host`.
3. **`test_origin_required_on_writes`** — POST to `/api/v1/runs` with no `Origin` header → 403; with `Origin: http://evil.com` → 403; with allow-listed origin and `X-Requested-By` → 200.
4. **`test_x_requested_by_required_on_writes`** — POST without the custom header → 403.
4a. **`test_csrf_coverage_parametrised_over_openapi`** — introspect the running app's OpenAPI schema (`app.openapi()`); for **every** route with method ∈ {`POST`, `PUT`, `PATCH`, `DELETE`} (i.e. every state-changing route, automatically picking up routes added in future PRs without test maintenance), issue (a) a request with no `Origin` and assert `403 origin_required`, and (b) a request with allow-listed `Origin` but no `X-Requested-By` and assert `403 x_requested_by_required`. Test-only admin hooks (`/api/v1/admin/test-only/*`) are excluded from the parametrisation per Security §Test-only admin hooks (those routes intentionally bypass CSRF because they only exist under the env-gate). **Why parametrised, not hand-written per route**: tests #3 and #4 only cover `POST /api/v1/runs`; a future write route added without a sibling CSRF test would silently bypass the middleware. The parametrisation makes the OpenAPI surface itself the test fixture — adding a route adds a test case.
5. **`test_anthropic_key_never_in_response`** — set a recognisable key, hit every GET endpoint, assert the key string never appears in any response body.
6. **`test_anthropic_key_never_in_logs`** — capture stdout during a Claude call (mocked), assert the key string never appears.
7. **`test_static_build_strips_authoring_code`** — run the static build, grep `dist/` for known authoring-component identifiers and write-endpoint URLs; must be zero hits. **Load-bearing for Hard decision #14.**
8. **`test_static_build_strips_anthropic_key`** — run the static build with `ANTHROPIC_API_KEY=sk-ant-TESTKEY-DO-NOT-USE`, grep `dist/` for that literal; must be zero hits.
9. **`test_static_build_no_admin_endpoints`** — assert `dist/api/v1/admin/` and `dist/api/v1/runs/` do not exist.
10. **`test_static_build_no_payload_json`** — assert `source_snapshots.payload_json` content does not appear in any file under `dist/`.
11. **`test_fixture_hash_mismatch_aborts`** — flip one byte in a fixture file, run the importer, assert it exits with `fixture_hash_mismatch` and writes zero rows to SQLite.
12. **`test_prompt_template_rejects_incompatible_source_set`** — call the Claude client with a `(prompt, source_set_id)` pair where the prompt's front-matter does not include the source set; assert the call is rejected before any HTTP request.
13. **`test_run_rejected_when_estimated_cost_exceeds_cap`** — set `MAX_RUN_COST_USD=0.01`, post a chapter run, assert 400 `estimated_cost_exceeds_cap` and zero Anthropic calls were attempted.
14. **`test_daily_cost_cap_enforced`** — pre-seed `generation_runs` with 24h of recent runs summing to the cap, post a new run, assert 400 `daily_cost_cap_exceeded`.
15. **`test_max_sentences_per_run_enforced`** — `CreateRunRequest` with a scope expanding past the cap is rejected at run-creation time, before any sentence is dispatched.
16. **`test_concurrency_cap_enforced`** — start a run; assert never more than `MAX_CONCURRENT_ANTHROPIC_CALLS` outstanding HTTP calls to the (mocked) Anthropic endpoint at any moment.
17. **`test_cancel_stops_dispatch`** — start a run, immediately POST cancel, assert the count of dispatched sentences ≤ `MAX_CONCURRENT_ANTHROPIC_CALLS` (in-flight drain) and remaining items are `cancelled`.
18. **`test_error_response_no_internals`** — force an uncaught exception in a route, assert response body is exactly `{code: "internal", message: "internal error"}` with no traceback / file path / SQL.
19. **`test_dist_does_not_contain_env_or_db`** — run the build, assert `dist/` contains no `.env`, no `*.db`, no `fixtures/manifest.json`, no `fixtures/prompts/*.md`.
20. **`test_adversarial_claude_output_rendered_safely`** — Claude returns `<script>alert(1)</script>` as candidate text; render it through the rank UI's component (RTL test); assert it appears as escaped text, not an executed script.

**Eval-seed entries** (for the eval harness CLAUDE.md describes; phrased as twin-style assertions even though `X-Twin-*` is waived for v1 — when v1 grows a second outbound dependency these become real twin tests):

- `eval_no_anthropic_call_when_run_creation_rejected_for_cost`: post a run that exceeds the daily cap, assert the mocked Anthropic endpoint saw zero requests attributable to the run_id.
- `eval_static_build_makes_no_outbound_calls`: run `make build-static` with all outbound network blocked at the OS level, assert success.
- `eval_importer_makes_no_outbound_calls`: same for `make import`.
- `eval_dist_passes_secret_scan`: run a fresh build with a synthetic key, run the secret scanner against `dist/`, assert clean.

### Open concerns (need user decision)

- **`MAX_RUN_COST_USD` and `MAX_DAILY_COST_USD` defaults** — proposed $5 / $20 are a guess. Gavin should confirm; both are env-overridable so this is not a blocker, but the default should not surprise him.
- **Pre-commit hook scope** — gitleaks is the lightest option, but it is an extra dependency. Acceptable to skip and rely on `.gitignore` discipline if Gavin prefers, with the understanding that the only line of defence then is gitignore + reviewer attention.
- **`<AuthOnly>` strip-out implementation** — separate Vite entry point (`main.static.tsx`) is the most defensible shape; a `define`-replace plugin is more compact but harder to verify. Recommendation: separate entry point, since the test that proves the strip-out (test #7) is much easier to argue about when the static entry literally cannot reach the authoring tree.
- **Daily cap window** — 24h rolling vs. calendar-day UTC. Rolling is friendlier (the cap doesn't reset at a fixed wall-clock moment); calendar-day is simpler to reason about. Picking rolling unless Gavin objects.
- **CSP header** — adding a strict CSP to the local UI is cheap but can break Vite HMR in dev. Proposal: enable a strict CSP only on the static build's served HTML (`script-src 'self'; object-src 'none'; base-uri 'self'`). Skip on dev. Confirm.
- **`EXTRA_ALLOWED_HOSTS` env-var (dev-only)** — comma-separated additions to the Host-header allowlist. **Honored only when `settings.env == "development"`** (matches the twin-override pattern: dev-only allowlists, never the inverse "anywhere except production"). The eval harness needs `host.docker.internal` since it runs in Docker and the API runs on the host. In staging / production the env var is silently dropped; tested in `api/tests/test_security_middleware.py::test_extra_allowed_hosts_ignored_outside_development`. Default is empty.

## Reliability

The Security section already covered: `127.0.0.1` bind-refuse-otherwise, `Origin`/`Host`/`X-Requested-By` middleware, `ANTHROPIC_API_KEY` redaction, `dist/` strip-out grep guards, fixture SHA-256 mismatch as `fixture_hash_mismatch`, per-run / daily / concurrency cost caps, no auto-retry on Anthropic. This section does not re-litigate any of those — they are accepted preconditions. The work here is **what happens after a fault, restart, or stale write**: how the in-process runner survives a crash, how SSE clients reconverge on the truth, how OCC actually serializes under SQLite WAL, how the fixture import reaches an all-or-nothing boundary, and how each invariant is pinned by a test.

### Failure modes

- **Server crash / `make dev` restart mid-run**: in-process async runner is gone; `generation_runs` rows in `running` and any `generation_run_items` in `pending` / `running` are now orphaned (no live owner). Symptom: UI shows a "running" run that will never produce events. Blast radius: one run; in-flight Anthropic calls already returned (or were cancelled by socket close) and any partial completions that landed in SQLite before the crash are intact.
- **Anthropic 429 / 5xx / read-timeout**: the affected sentence is marked `failed` with `error_code` from the coarse enum (`anthropic_api_error | anthropic_refusal | timeout | rate_limit | invalid_response | internal`); the runner does **not** auto-retry (Security: "Retry this sentence" is an explicit user click). Other sentences in the same run continue. Run finishes in `completed` with mixed item statuses — that is a normal terminal state, not `failed`. The run-level `status=failed` is reserved for a runner-level fault (e.g. uncaught exception in the dispatcher itself), not for per-item failures.
- **Anthropic returns adversarial / empty / malformed JSON**: `error_code=invalid_response`; nothing written to `claude_candidates`; `error_message` carries the parser's complaint (truncated to a configured `MAX_ERROR_MESSAGE_BYTES`, default 2 KiB, so a multi-MB Anthropic blob can't bloat the row).
- **SSE client disconnect / browser refresh / reconnect**: TCP socket close triggers `sse-starlette`'s disconnect path; the runner does **not** stop on disconnect (the run owns its own lifecycle; readers are detachable observers). On reconnect the client reaches a deterministic state via the replay protocol below.
- **Run completed before the reconnect**: `GET /runs/{run_id}/stream` against a `completed`/`failed`/`cancelled`/`interrupted` run replays every `generation_run_items` row in `(run_id, ordinal)` order, then emits a single terminal event matching the run's status (`run_finished` for `completed`, `run_cancelled`, `run_interrupted`, or `run_failed`), then closes the stream. No "live tail" attach. The client's reducer reaches the same final state as if it had been connected throughout.
- **Cancel mid-run**: `POST /runs/{run_id}/cancel` flips the run row to `cancelled` and updates every `generation_run_items.status` that is `pending` to `cancelled` in one transaction; the dispatcher's per-iteration check (`SELECT status FROM generation_runs WHERE run_id=?` between sentences) sees the flip and stops dispatching. In-flight Anthropic calls drain (Security cost-safety: don't waste tokens already in flight); whichever finish before drain commit as `completed`, the rest commit as `cancelled` with the in-flight request's actual outcome ignored.
- **Resume after `cancelled` / `interrupted`**: `POST /runs/{run_id}/resume` opens a transaction, computes the unfinished slice (`status IN ('cancelled','interrupted','pending')` from the source run), creates a **new** `generation_runs` row with a new `run_id` and `parent_run_id` set, and inserts dense `1..M` `generation_run_items` rows over that slice. The original run is not mutated. If the source run has zero unfinished items, return `409 nothing_to_resume` and write nothing.
- **Stale writes from a second tab** (rankings, red-letter overlays, tie-break decisions, `hidden_combos`): every write checks `If-Match` against the current `version`; mismatch returns `409 stale_version` with the current `version` in the body so the conflict modal can resolve.
- **SQLite locked / busy**: WAL + a deliberate `busy_timeout` (default 5000 ms) absorbs typical contention; a writer that still times out returns `503 db_busy` with `Retry-After: 1`. A read that times out returns the same; the client retries idempotently.
- **Disk full** during fixture import → SQLite raises `OperationalError: database or disk is full`; importer transaction is uncommitted (no partial rows); `ErrorResponse {code: "disk_full", details: {bytes_required_estimate: int}}` is returned by `POST /api/v1/admin/reimport`. CLI `make import` exits non-zero with the same message.
- **Disk full** during `claude_candidates` insert mid-run → the affected sentence is marked `failed` with **`error_code='disk_full'`** (a typed enum value on `generation_run_items.error_code` — Data §`generation_run_items` schema includes `disk_full` in the CHECK enum so this is a first-class code, not a free-text `error_message='disk_full'`); the run continues (subsequent inserts will also fail, marking each `failed` with the same typed code); the dispatcher does **not** treat this as a runner-level fault until **N consecutive sentences** fail with `error_code='disk_full'` (`N=3`, configurable). The dispatcher counts consecutive failures by typed `error_code` (`SELECT COUNT(*) FROM generation_run_items WHERE run_id=? AND status='failed' AND error_code='disk_full' AND ordinal > <last_non_disk_full_ordinal>`) — string-equality on `error_message` is fragile (the Pydantic-validated message could be wrapped, truncated by `MAX_ERROR_MESSAGE_BYTES`, or localized) and is explicitly avoided. At the threshold the run-level status flips to `failed` and the dispatcher exits. This bounds the damage from a fully-full disk without conflating "Anthropic flake on one sentence" with "the disk is gone."
- **Disk full** during `make build-static` → builder writes to a temp directory `dist.tmp/`; on completion it atomic-renames `dist/` ↔ `dist.tmp/` (see Recovery → Static-site atomic swap). Mid-build write failure leaves `dist/` exactly as it was; `BuildStaticResponse` is replaced by a `503 disk_full` with structured details.
- **Schema migration during reimport**: an `alembic`-style versioned migration runs **before** the import transaction opens; if the migration aborts, the import never starts. If the migration succeeds but the import fails, the schema is at the new version with empty source-text tables — the `fixture_version` row is `NULL` so the status pill flags the DB as not-imported. Overlay tables are preserved (Hard decision #7); only source tables are dropped/rebuilt by import.
- **Checksum mismatch on a fixture file** (`fixture_hash_mismatch`, owned by Security): importer aborts before the import transaction begins; nothing is written to SQLite. Existing DB state is untouched.
- **Orphan-resolution race**: between the 409-return and the user resolving dispositions, a different fixture refetch lands. Resolution payload carries the manifest hash the orphan summary was computed against; if the current `fixtures/manifest.json` hash differs at apply time, return `409 orphan_summary_stale` and re-issue a fresh `OrphanSummary` (the UI re-renders).
- **Sentence-segmenter changes upstream**: a re-segmentation that changes SBLGNT sentence count is a fixture-version bump that triggers the orphan flow (Architect §sentence identity). Reliability concern: never let an import silently re-key candidates / rankings / overlays to wrong sentences. The pre-flight FK check (every overlay/ranking/candidate `sentence_id` resolves under the new fixture set) is the load-bearing guard; without it the orphan flow is meaningless.
- **Anthropic SDK hang (no response, no error)**: `httpx` client timeout pinned at `ANTHROPIC_REQUEST_TIMEOUT_SECONDS` (default 120s; matches Anthropic's longest reasonable response); the runner additionally guards each sentence call with `asyncio.wait_for(call, timeout=ANTHROPIC_REQUEST_TIMEOUT_SECONDS + 30)` as belt-and-braces — a hung SDK shouldn't be able to wedge a sentence forever. On timeout the item is `failed` with `error_code=timeout`.
- **Browser tab keeping the SSE stream open after the run finished**: `sse-starlette` sends keepalive pings; once the terminal event is emitted the server closes the stream; the client's `EventSource` reconnects, the server immediately replays + emits the terminal event + closes again. This is idempotent and bounded — the loop only runs once per reconnect, not in a tight retry.

### Idempotency

- **`POST /api/v1/admin/reimport`**: the operation is idempotent **with respect to source-text tables** (drop + rebuild — running it twice with the same `fixtures/manifest.json` produces byte-identical row content). It is **not** request-level idempotent — no `Idempotency-Key` header. Two concurrent reimports are prevented by a process-wide `asyncio.Lock` on the importer module; the second caller gets `409 import_in_progress`. Justification: this is a one-user tool, the importer is invoked manually, and the lock is sufficient.
- **`POST /api/v1/runs`**: not idempotent at the request level; each call creates a new `run_id`. Caller-side idempotency is the duplicate-batch soft warning (Designer Flow 4 step 4) — a UI-level affordance, not a server enforcement. The server intentionally does not dedupe runs because Gavin explicitly wants intentional re-runs of the same combo (Designer Flow 4 step 7).
- **`POST /runs/{run_id}/cancel`**: idempotent. Cancelling an already-`cancelled` / `completed` / `failed` / `interrupted` run returns `200` with the current run state (no error); the underlying SQL is `UPDATE generation_runs SET status='cancelled' WHERE run_id=? AND status IN ('pending','running')` and silently no-ops on terminal states.
- **`POST /runs/{run_id}/resume`**: not idempotent. Each call produces a new `run_id` over whatever the source run's unfinished slice is **at that moment**. Calling resume twice on the same source run could create two concurrent resume runs over (subsets of) the same items — bounded because the second call's "unfinished slice" excludes items already picked up by the first resume. To prevent two concurrent owners of the same item, the runner takes a row-scoped lock on `generation_run_items` via `SELECT … FOR UPDATE` semantics emulated under SQLite by writing `status='running'` in the same transaction that reads the candidate slice; a second resumer sees no `pending` items and returns `409 nothing_to_resume`.
- **OCC writes (rankings, red-letter overlays, tie-break decisions, `hidden_combos`)**: idempotent under matching `If-Match`. A retried `PUT` with the same `If-Match` header and body is safe — first call increments `version`, second call's `If-Match` is now stale and returns `409` (the client recognises its own write succeeded by reading back the row and observing the `version`). Bodies are not compared for idempotent-on-replay because that would require body fingerprinting; the cost of a `409` on a duplicate is harmless.
- **Anthropic candidate insertion**: a successful Anthropic call writes `(source_snapshots upsert-on-hash, claude_candidates insert, generation_run_items.status='completed')` in **one** SQLite transaction. There is no window where the candidate exists without the run-item pointing at it, or vice versa. If the transaction commits but the SSE event fails to emit, the client will see the result on next reconnect via replay — the persisted state is the source of truth, the SSE event is a hint.
- **Source-snapshot upsert**: keyed by `snapshot_hash`; `INSERT OR IGNORE` semantics. Two concurrent runners producing the same snapshot dedupe to one row; whichever lost the race reads the existing row's hash back. The canonicalization rule (Architect §source_snapshots) is what makes the hash deterministic.
- **Static-site build (`POST /api/v1/admin/build-static`)**: idempotent with respect to inputs — running it twice with identical SQLite state produces identical `dist/`. Concurrent builds are prevented by a process-wide `asyncio.Lock`; second caller gets `409 build_in_progress`.

### Retries & backoff

- **Anthropic outbound calls**: **no automatic retry, no backoff**. The Security section pinned this — a quiet retry loop multiplies spend. A failed sentence is surfaced to the UI; the user clicks "Retry this sentence" which dispatches a fresh single-sentence run with `parent_run_id`. The SDK's *built-in* connection-level retries (idempotent `GET`s, transport-level transient failures) are left at SDK defaults; we do **not** add a wrapper retry on top.
- **SQLite `busy_timeout`**: 5000 ms at connection open. Inside that window the engine retries internally; outside it we surface `503 db_busy`. No application-level retry loop on top.
- **SSE client reconnect**: the **browser** retries automatically (the `EventSource` default). Server side does nothing. The replay protocol below makes this safe.
- **Importer**: no retries. Either it succeeds atomically or it doesn't — fix the input (manifest hash, disk space, schema migration) and re-run. Silent retries on a fixture import would mask a real fault.
- **Static-site build**: no retries. Same reasoning — the input is SQLite, the output is a directory, the failure mode is deterministic.

### SSE replay protocol (load-bearing — pinned)

This is the most failure-prone surface in v1. Client reducers must converge to the same final state regardless of whether they connected at run start, joined mid-run, reconnected after a network blip, or opened the page for the first time after the run already terminated. Architect §generation_run_items pins the wire ordering (`(run_id, ordinal)`); this section pins the **events**, **sequencing**, and **client-reducer contract**.

**Event types** (lowercase `event:` field; all carry JSON `data:` payloads):

- `replay_begin` — `{run_id: str, total_items: int, run_status: str}`. First event on every connection. Tells the client a replay is about to start so it can clear any local `(run_id, *)` state and re-apply from scratch.
- `item_completed` — `{run_id, ordinal, sentence_id, candidate_id, latency_ms, generated_at}`. Replay or live.
- `item_failed` — `{run_id, ordinal, sentence_id, error_code, error_message}`. Replay or live.
- `item_cancelled` — `{run_id, ordinal, sentence_id}`. Replay or live.
- `item_interrupted` — `{run_id, ordinal, sentence_id}`. Replay-only (only set on startup; live runs don't produce this).
- `replay_end` — `{run_id, replayed: int}`. Sent after the last replay row, before the live tail attaches (or before the terminal event for a terminated run).
- `run_finished` — `{run_id, status: "completed"}`. Terminal.
- `run_cancelled` — `{run_id}`. Terminal.
- `run_interrupted` — `{run_id}`. Terminal.
- `run_failed` — `{run_id, error_code, error_message}`. Terminal (runner-level fault, not per-item failure).
- `keepalive` — empty `data: {}`. Sent every 15s while the live tail is attached, never during replay. Signals "connection alive, no new events"; clients ignore it for state purposes.

**Sequencing invariants** (every conforming connection sees this):

1. Exactly one `replay_begin` as the first event.
2. Zero or more `item_*` events, strictly ascending by `ordinal` within the replay phase. Replay reads `SELECT * FROM generation_run_items WHERE run_id=? AND status != 'pending' ORDER BY ordinal` so the client never sees a `pending` row.
3. Exactly one `replay_end`.
4. **If the run is in a terminal state at replay_end time**: exactly one terminal event (`run_finished`/`run_cancelled`/`run_interrupted`/`run_failed`); stream closes.
5. **If the run is still active at replay_end time**: zero or more live `item_*` events as completions land, interleaved with `keepalive`s, until a terminal event closes the stream.

**Client reducer contract**: a conforming client `seenOrdinals: Set<int>` initialised on `replay_begin`, accepts `item_*` events idempotently keyed by `(run_id, ordinal)` (re-applying a duplicate is a no-op), and ignores any event whose `run_id` does not match the current connection's. This is what makes "client reconnects three times during a run" produce the same final state as "client connects once and stays."

**The `pending` exclusion is intentional**: replaying a `pending` row would tell the client about work not yet started, which is useful only for "show a list of upcoming sentences" — the UI gets that from the `CreateRunResponse.sentence_ids` payload at run start, so SSE doesn't need to carry it. Reducing replay traffic to non-`pending` rows also bounds the replay payload to the work actually done.

### Optimistic concurrency control under SQLite WAL

OCC tables (rankings, red-letter overlay rows, tie-break decisions, `hidden_combos`) all carry a monotonically-incrementing `version INT NOT NULL`. The write contract:

```
BEGIN IMMEDIATE;
SELECT version FROM <table> WHERE <pk> = ?;
-- if NULL (row doesn't exist) and method is PUT: 404
-- if version != If-Match: 409 stale_version, current_version=<server-version>
UPDATE <table> SET <cols>=?, version=version+1 WHERE <pk>=? AND version=?;
-- the WHERE version=? guard makes the write atomic with the read inside the transaction
-- (defence-in-depth — the BEGIN IMMEDIATE already serialises writers under WAL)
COMMIT;
```

`BEGIN IMMEDIATE` (not `DEFERRED`) is mandatory — it acquires the WAL `RESERVED` lock at transaction start, which serialises this writer against all other writers without waiting for the first conflicting statement. Without `IMMEDIATE` two writers can both pass the version check on `DEFERRED` reads and then race at `UPDATE` time; one would silently lose the version-bump even with the `WHERE version=?` guard. This is a known SQLite footgun and the test in §test coverage proves we avoid it.

**Atomic ranking writes** (Hard decision #17 — rank list + ties + notes share one `version`): the entire ranking save is **one** `BEGIN IMMEDIATE` transaction:

```
BEGIN IMMEDIATE;
-- read & version-check: SELECT version FROM rankings WHERE sentence_id=?
-- DELETE FROM ranking_entries WHERE sentence_id=?
-- INSERT INTO ranking_entries (sentence_id, candidate_ref, rank, tied_with_above, hidden) VALUES … (N rows)
-- UPDATE rankings SET notes=?, version=version+1 WHERE sentence_id=? AND version=?
COMMIT;
```

The `DELETE`+`INSERT` on `ranking_entries` is the simplest correct shape — diffing entries against the prior list to issue minimal updates is a footgun (re-ranks are tiny so the perf saving is nothing, and a partial diff that gets the tied-with-above flag wrong is a silent data corruption). Notes is a column on the `rankings` parent row, not a sibling table — that's the schema commitment that makes "one `version` covers all of it" honest.

### Generation runner durability

- **Per-sentence transaction boundary**: every successful Anthropic call commits in **one** SQLite transaction: `INSERT OR IGNORE INTO source_snapshots`, `INSERT INTO claude_candidates`, `UPDATE generation_run_items SET status='completed', candidate_id=?, completed_at=? WHERE run_id=? AND ordinal=?`. If any statement fails the whole transaction rolls back and the item stays `running`; the next dispatcher tick will retry by re-marking `failed` with `error_code=internal`. There is no window where the candidate exists without a corresponding `generation_run_items.completed`.
- **Status transitions are explicit**: `pending → running → (completed | failed | cancelled)`; `running → interrupted` on startup recovery; no other transitions are legal. The `UPDATE` statements name both the old and new status as a guard (`WHERE status='pending'` when claiming, `WHERE status='running'` when finishing) so a concurrent cancel can't be silently overwritten by a finish.
- **Dispatcher cancel-check granularity**: between every sentence dispatch the runner re-reads `generation_runs.status`; if `cancelled`, it stops dispatching new work but allows already-launched in-flight calls to drain into `completed` or `cancelled` per the in-flight outcome.
- **Concurrency cap interaction with cancel**: `MAX_CONCURRENT_ANTHROPIC_CALLS` (Security §abuse) caps the in-flight set; on cancel the drain finishes at most `MAX_CONCURRENT_ANTHROPIC_CALLS` extra items. This is the bound the cancel-stops-dispatch test asserts (Security test #17 / Reliability test below).

### Static-site build atomicity

- **Atomic swap**: the builder writes to `dist.tmp/` (sibling of `dist/`); on success it does `os.replace("dist.tmp", "dist")` after first moving any existing `dist` to `dist.bak/` (single rename, atomic on POSIX). On failure mid-build, `dist.tmp/` is left around for inspection but `dist/` is untouched. A previous-run's `dist.bak/` is removed only **after** the new `dist/` is in place. This gives "the previous build is intact until the new one is fully written," which is what Designer Flow 6 step 5's failure-state UX assumes.
- **Lock**: process-wide `asyncio.Lock` (already noted under Idempotency) prevents two concurrent builds from racing on `dist.tmp/`.
- **Service-layer error propagation**: builder calls `api/<feature>/service.py` functions; any exception is caught at the builder boundary and converted to a `BuildStaticResponse`-shaped error. The route handler maps to an `ErrorResponse` (`code` from a coarse enum: `build_failed | disk_full | service_layer_error | build_in_progress`); the in-UI Build button surfaces `error_message` per Designer Flow 6 step 5 failure state.

### Backups / recovery

- **Recovery model**: the durable source of truth is `fixtures/` (in git); the runtime store is `data/bible_study.db` (gitignored). On total disk loss, recovery is `git clone` → `make import` → re-run rankings / overlays / generation runs from scratch. Source texts and candidates are reproducible (same fixtures, same prompts, same model — modulo Anthropic non-determinism); rankings / overlays / notes / tie-breaks / hidden combos are **not** reproducible and represent Gavin's authored work.
- **Backup decision (v1)**: **automatic periodic SQLite snapshot via `VACUUM INTO`**, not "user is responsible." Picking automatic because (a) Manager pinned OCC at the contract level explicitly because "silent last-write-wins on rankings is the failure mode that would cost Gavin the most work to recover from" (Architect §trade-offs) — that same logic applies to disk loss, and "remember to back up your SQLite file" is a worse contract than "the tool already did"; (b) the volume is tiny (overlay tables for one book are KB-to-MB scale), so the cost is negligible; (c) `VACUUM INTO` is the SQLite-blessed online-backup primitive and runs without taking exclusive locks.
- **Backup mechanism**: a startup-scheduled async task (`apscheduler` or a hand-rolled `asyncio.create_task` loop) runs `VACUUM INTO 'data/backups/bible_study-YYYY-MM-DDTHH-MM-SSZ.db'` every `BACKUP_INTERVAL_SECONDS` (default 3600 = 1h) and on every `POST /api/v1/admin/reimport` (immediately before the import transaction opens, so the most recent backup is pre-import state). **The scheduler bootstrap creates `data/backups/` if it does not exist** (`Path("data/backups").mkdir(parents=True, exist_ok=True)`) **before** scheduling the periodic task or the pre-import hook — otherwise the first `VACUUM INTO` fails with `unable to open database file`. Data §`data/backups/` directory creation cross-references this. Backups directory is gitignored. Retention: keep the **last `BACKUP_RETENTION_COUNT`** snapshots (default 24, ~24h at default interval) and the **most recent pre-import** snapshot regardless of retention count; older snapshots are deleted in the same task. Default totals to <100 MB on disk for a v1 dataset — well within tolerance for a single-user tool. If Gavin objects, both knobs are env-overridable; setting `BACKUP_INTERVAL_SECONDS=0` disables the periodic task while keeping the pre-import backup.
- **Backup integrity check**: each snapshot is opened read-only and `PRAGMA integrity_check` is run before the old backup is deleted; failure surfaces `code: "backup_integrity_failed"` in logs and skips the deletion (so the older known-good snapshot isn't dropped on a bad new one). **Hard cap to bound disk growth on persistent integrity failure**: a separate `BACKUP_HARD_CAP` (default `32`, env-overridable) caps the total number of retained snapshots **regardless** of integrity-check outcome. If `len(snapshots_on_disk) >= BACKUP_HARD_CAP`, the cleanup deletes the **oldest** snapshot even if the most-recent integrity check failed — the alternative (unbounded retention while integrity fails) would silently fill the disk over hours and cause the disk-full damage-bounding logic in `claude_candidates` insert (§Failure modes) to fire. Cap is set above `BACKUP_RETENTION_COUNT` (32 > 24) so steady-state operation never hits it; it is purely a fail-safe for the pathological "integrity check has been failing for a day" scenario. A log line `event=backup.hard_cap_eviction` fires when the cap evicts a snapshot the retention rule would have kept, so Gavin can see the fail-safe is engaging.
- **Restore**: `make restore SNAPSHOT=path/to/file.db` copies the snapshot over `data/bible_study.db` after refusing if the FastAPI process is running (PID file check at `data/bible_study.pid` written at startup, removed at clean shutdown). A stale PID file with no live process is overridable with `FORCE=1`.
- **Server-restart recovery (already pinned by Architect §generation runner)**: on startup, mark every `generation_runs.status='running'` row as `interrupted`, cascade `generation_run_items` `pending`/`running` to `interrupted`. This runs **before** the FastAPI app starts accepting requests so the UI never sees a "running" run that has no live owner.
- **WAL checkpointing**: `PRAGMA wal_autocheckpoint=1000` (default) is fine; we do **not** disable it. After a clean shutdown the WAL is checkpointed to the main DB; after a crash the WAL replay on next open completes the durability story for already-committed transactions.

### Observability

Single-user tool, but Gavin still needs to debug a failed run from logs alone. Security pinned the redaction rules; Reliability pins the **content** of logs and the per-route metric set.

- **Structured JSON logs to stdout**, one line per event. Required keys on every line: `ts` (ISO-8601 UTC), `level`, `event` (a stable identifier — see event list below), `request_id` (UUID4 generated by middleware on every inbound request, propagated via contextvar so service-layer logs carry it). Optional keys correlate the line to domain objects: `run_id`, `sentence_id`, `candidate_id`, `ordinal`, `route`, `status_code`, `duration_ms`, `error_code`. `error_message` may appear; Anthropic refusal text is hashed not logged (Security §logging discipline).
- **Stable event identifiers** (the load-bearing log-grep surface): `run.created`, `run.dispatch.started`, `run.dispatch.cancelled`, `run.item.started`, `run.item.completed`, `run.item.failed`, `run.finished`, `run.interrupted`, `run.resumed`, `import.started`, `import.committed`, `import.aborted`, `import.orphans_detected`, `build.started`, `build.completed`, `build.failed`, `backup.completed`, `backup.failed`, `occ.conflict`, `db.busy`, `anthropic.call.started`, `anthropic.call.completed`, `anthropic.call.failed`. The importer pair uses SQL transaction verbs (`committed` / `aborted`) deliberately — they are precise about whether the SQLite transaction reached COMMIT, and the `aborted` event always carries a `stage` key (`preflight | transaction`) so the on-call grep can distinguish pre-transaction failures (hash mismatch, missing manifest) from in-transaction rollbacks. Adding an event is a code change; renaming one breaks log queries — that's the discipline.
- **Metrics** (recorded as structured-log lines under `event=metric.<name>`; no Prometheus, no StatsD — overkill for one user). Names and units pinned:
  - `metric.run.duration_ms` (per-run, on completion) — total wall-clock from `run.created` to terminal event
  - `metric.run.item_count` (per-run, on completion) — N sentences in the run's resolved scope
  - `metric.anthropic.call.latency_ms` (per call, on completion or failure) — labelled with `model`, `source_set_id`, `outcome` (`success | error | timeout | rate_limit`)
  - `metric.anthropic.call.error_count` (per call, on failure only) — labelled with `error_code`
  - `metric.db.write.latency_ms` (per OCC write) — labelled with `table`, `outcome` (`ok | conflict | busy`)
  - `metric.import.took_ms` (per import) — labelled with `outcome` (`ok | hash_mismatch | orphans_detected | disk_full | other`)
  - `metric.build.took_ms` (per build) — labelled with `outcome`
  - `metric.backup.took_ms`, `metric.backup.bytes` (per backup)
- **What the on-call view (Gavin in his terminal) sees when something misbehaves**: `tail -f logs.json | jq 'select(.level=="ERROR" or .event | startswith("run.item.failed"))'` surfaces every failure with enough context (`run_id`, `ordinal`, `sentence_id`, `error_code`, `error_message`, `request_id`) to walk forward to the offending Claude call or backward to the run that produced it. The `request_id` correlates HTTP-route logs to runner logs for "what triggered this run."
- **No remote logging, no telemetry** (Security §supply chain): logs are stdout; redirection is the user's responsibility (`make dev` documents the redirect target).
- **A `/api/v1/admin/diag` endpoint** (read-only, no auth surface beyond the loopback bind): returns `{commit_hash, fixture_version, db_size_bytes, last_backup_at, last_backup_filename, in_flight_runs: list[{run_id, ordinal_completed, ordinal_total}], wal_size_bytes}`. `commit_hash` matches the user-CLAUDE.md "verify your assumptions" rule: Gavin can compare it to `git rev-parse --short HEAD` before debugging further. **`last_backup_filename` is the basename only** (e.g. `bible_study-2026-05-03T12-34-56Z.db`), never the absolute path — the loopback-bind makes path leakage low-risk in practice but the diag response is the kind of surface that gets pasted into bug reports / screenshots, and exposing absolute paths (which include the user's home directory and project layout) violates the same logging-discipline rule that hashes Anthropic refusals. The basename is sufficient for the user-facing question diag answers ("which snapshot is the most recent?"); the full path is reconstructible as `data/backups/<filename>` by anyone reading the source.

### Recovery procedures

- **"My run is stuck `running` after a server restart"**: not possible after this PLAN — the startup hook flips it to `interrupted`. RTO: ~seconds (startup completes before the app accepts requests). User action: click Resume.
- **"I cancelled a run but the UI still shows in-flight calls"**: expected up to `MAX_CONCURRENT_ANTHROPIC_CALLS` items per the in-flight drain. RTO: bounded by Anthropic latency p99 (~30s typical, 120s worst-case at the request timeout). User action: wait; the runner emits the terminal event when drain completes.
- **"My ranking save failed with 409"**: conflict modal. User action: Reload (re-fetch the server's current ranking and re-rank against it) or Overwrite (re-fetch, merge, send a fresh `If-Match`-bearing write). RTO: seconds.
- **"My import failed mid-way"**: the import transaction rolled back; SQLite is in its previous state; the pre-import backup is in `data/backups/`. RTO: `make import` again after fixing the input. If the reason was `fixture_hash_mismatch` the manifest needs to be re-pinned (Security §supply chain); if `disk_full`, free space; if `orphans_detected`, walk the orphan-resolution screen.
- **"`dist/` is broken / I want yesterday's static site back"**: `dist.bak/` is the immediately-previous build (kept until the next successful build replaces it). `mv dist dist.broken && mv dist.bak dist` recovers. If both are bad, `make build-static` regenerates from SQLite.
- **"My SQLite file is corrupted"**: `make restore SNAPSHOT=data/backups/bible_study-<timestamp>.db`. RTO: <1 minute for the copy. RPO: bounded by `BACKUP_INTERVAL_SECONDS` (worst case 1h of recent rankings/overlays lost).
- **"Total disk loss"**: `git clone` → `make import` → `cp <off-machine backup> data/bible_study.db`. RPO: whatever Gavin's off-machine backup cadence is for `data/`. v1 does not ship an off-machine backup story — it's documented in README as "if you care about your rankings, copy `data/backups/` somewhere off-machine periodically." Acceptable for a personal tool; revisit if Gavin wants a Dropbox/iCloud sync.

### Test coverage

This subsection is what makes the invariants above survive future refactors. Without these tests, every claim above is a comment that drifts.

**Unit tests** (live in `api/tests/test_*.py` for backend, `web/__tests__/*.test.tsx` for the React client; one file per concern, named after the test list, not the module):

- `api/tests/test_runner_recovery.py::test_orphaned_running_runs_marked_interrupted_on_startup` — seed `generation_runs(status='running')` + `generation_run_items(status='pending'|'running')`, call the startup recovery hook, assert run row → `interrupted` and items → `interrupted`. **Invariant: server-restart recovery (Hard decision #10).**
- `api/tests/test_runner_recovery.py::test_startup_recovery_runs_before_routes_accept_traffic` — start the app, assert `GET /api/v1/admin/diag` never reports a `running` run with no live owner. **Invariant: no race window where the UI sees stale `running` runs.**
- `api/tests/test_sse_replay.py::test_replay_then_terminal_for_completed_run` — seed a completed run with mixed `completed`/`failed`/`cancelled` items, connect to `/runs/{run_id}/stream`, assert the event sequence matches the protocol exactly: one `replay_begin`, items in `ordinal` order, one `replay_end`, one `run_finished`, then close. **Invariant: SSE replay protocol §sequencing invariants 1-4.**
- `api/tests/test_sse_replay.py::test_replay_excludes_pending_rows` — seed a run with one `completed` and one `pending` item, connect, assert exactly one `item_completed` and zero events for the `pending` row. **Invariant: replay reads `WHERE status != 'pending'`.**
- `api/tests/test_sse_replay.py::test_reconnect_midrun_replays_then_attaches_live` — start a real in-process run, connect, drop the connection, reconnect, assert the second connection re-replays from `ordinal=1` and then attaches to the live tail. **Invariant: client reducer convergence under reconnect.**
- `api/tests/test_sse_replay.py::test_reducer_convergence_under_three_reconnects` — drive a fake client through three replay-then-disconnect cycles, assert final reduced state is identical to a single uninterrupted client's. **Invariant: idempotent client reducer.**
- `api/tests/test_occ_serialisation.py::test_concurrent_ranking_saves_one_wins_one_409` — open two real SQLite connections (real Postgres-style — actual `aiosqlite` connections against a temp DB file, no mock), both `BEGIN IMMEDIATE` against the same `sentence_id`, assert exactly one commits, one returns `409 stale_version`, and the resulting `version` is incremented by exactly 1. **Invariant: OCC under SQLite WAL with `BEGIN IMMEDIATE` (the load-bearing test that proves we avoid the deferred-read race).**
- `api/tests/test_occ_serialisation.py::test_ranking_atomic_save_rank_list_ties_notes_one_version` — write a ranking with rank list + ties + notes; perform two concurrent saves where one only changes notes and one only changes rank order; assert exactly one wins. **Invariant: Hard decision #17 (one `version` for the whole ranking row).**
- `api/tests/test_occ_serialisation.py::test_hidden_combos_occ` — hide / unhide race; one wins, the other 409s. **Invariant: Hard decision #12 (`hidden_combos` are OCC-protected).**
- `api/tests/test_occ_serialisation.py::test_overlay_occ` — same pattern for `red_letter_overlays`.
- `api/tests/test_occ_serialisation.py::test_red_letter_overlay_chain_leaf_race_one_winner` — explicitly names the chain-leaf race: two `BEGIN IMMEDIATE` transactions both read the same overlay head (the leaf row with no child), each attempts to `INSERT` a new leaf with `parent_overlay_id = head.overlay_id` and `version = head.version + 1`, the "no other child" guard (a `WHERE NOT EXISTS (SELECT 1 FROM red_letter_overlays c WHERE c.parent_overlay_id = ?)` predicate inside the INSERT statement) ensures exactly one INSERT succeeds and the other returns `409 stale_version`. Asserts: (a) exactly one row was added to `red_letter_overlays`, (b) the resulting chain has a single leaf, (c) the loser saw `409` with the current head's version. **Invariant: chain-leaf race protection (Data §`red_letter_overlays` "no other child" guard).** This is named distinctly from `test_overlay_occ` because the overlay table's OCC mechanic is *chain-shaped*, not row-shaped — the standard `WHERE version=?` guard on a single row is not sufficient; the additional "no other child has me as parent" predicate is the load-bearing guard and must have its own test.
- `api/tests/test_occ_serialisation.py::test_tie_break_occ` — same pattern for `tie_break_decisions`.
- `api/tests/test_idempotency.py::test_cancel_idempotent_on_terminal_states` — call cancel on a `completed` run, assert 200 + run state unchanged; same for `failed`, `cancelled`, `interrupted`. **Invariant: cancel idempotency.**
- `api/tests/test_idempotency.py::test_concurrent_reimport_returns_409_in_progress` — drive two simultaneous `POST /admin/reimport`, assert exactly one runs and the other returns `409 import_in_progress`. **Invariant: importer lock.**
- `api/tests/test_idempotency.py::test_concurrent_resume_returns_nothing_to_resume` — start two simultaneous `POST /runs/{id}/resume`, assert one creates a new run and the other returns `409 nothing_to_resume`. **Invariant: row-scoped slice claim under WAL.**
- `api/tests/test_idempotency.py::test_concurrent_build_returns_409_in_progress` — same for the static builder.
- `api/tests/test_idempotency.py::test_per_sentence_transaction_atomicity` — inject a SQLite error after `claude_candidates` insert but before `generation_run_items` update; assert no row in `claude_candidates` is committed. **Invariant: per-sentence transaction boundary.**
- `api/tests/test_runner_failure_modes.py::test_anthropic_429_marks_item_rate_limit` — mock the Anthropic client to raise `RateLimitError`, assert the item is `failed` with `error_code='rate_limit'` and the run continues to subsequent items. **Invariant: per-item failures don't fail the run.**
- `api/tests/test_runner_failure_modes.py::test_anthropic_5xx_marks_item_anthropic_api_error` — same shape for 500/502/503.
- `api/tests/test_runner_failure_modes.py::test_anthropic_timeout_bounded_by_wait_for` — mock the SDK to hang (`asyncio.sleep(99999)`), assert the item is `failed` with `error_code='timeout'` within `ANTHROPIC_REQUEST_TIMEOUT_SECONDS + 30 + slack` seconds. **Invariant: hung-SDK guard.**
- `api/tests/test_runner_failure_modes.py::test_three_consecutive_disk_full_fails_run` — mock disk-full on three sentence inserts in a row; assert each item has typed `error_code='disk_full'` (not `error_code='internal'` with a magic string in `error_message`); assert run-level `status='failed'` after the third. **Invariant: disk-full damage bounding via typed `error_code`.**
- `api/tests/test_runner_failure_modes.py::test_cancel_stops_dispatch_drains_in_flight` — start a 10-sentence run with `MAX_CONCURRENT_ANTHROPIC_CALLS=2`, cancel after item 3 starts, assert at most `3 + 2` items reach `running`/`completed`, the rest are `cancelled`. **Invariant: cancel-stops-dispatch with bounded in-flight drain.**
- `api/tests/test_runner_failure_modes.py::test_resume_creates_new_run_with_parent_run_id` — cancel a run mid-flight, resume, assert new `run_id` ≠ original, `parent_run_id` set, dense `1..M` ordinals over the unfinished slice only.
- `api/tests/test_runner_failure_modes.py::test_resume_with_no_unfinished_returns_409` — resume a fully-completed run, assert `409 nothing_to_resume`.
- `api/tests/test_importer_atomicity.py::test_partial_import_rolls_back` — inject a parse error mid-import; assert the SQLite source-text tables are byte-identical to their pre-import state (i.e. transaction rolled back). **Invariant: importer is atomic-or-nothing.**
- `api/tests/test_importer_atomicity.py::test_import_preserves_overlay_tables` — seed overlay tables with rows, run a successful import, assert overlay rows survive. **Invariant: overlay tables preserved (Hard decision #7).**
- `api/tests/test_importer_atomicity.py::test_import_drops_and_rebuilds_source_tables` — seed source tables with garbage rows, run import, assert source-text rows match fixtures byte-for-byte. **Invariant: source tables rebuilt from fixtures.**
- `api/tests/test_importer_atomicity.py::test_orphan_resolution_stale_summary_returns_409` — stage an orphan summary, change `fixtures/manifest.json`, apply the dispositions, assert `409 orphan_summary_stale`. **Invariant: orphan-resolution race protection.**
- `api/tests/test_importer_atomicity.py::test_disk_full_during_import_returns_structured_error` — fault-inject `OperationalError("database or disk is full")`, assert `ErrorResponse {code: "disk_full"}` and zero rows written.
- `api/tests/test_importer_atomicity.py::test_pre_flight_fk_check_catches_orphan_candidate_before_import_begins` — seed a candidate referencing a sentence that the new fixtures will remove, run import without `force`, assert `409 orphans_detected` *before* any source table is dropped. **Invariant: never silently re-key candidates.**
- `api/tests/test_static_build.py::test_atomic_swap_preserves_previous_dist_on_failure` — populate `dist/`, fault-inject mid-build, assert `dist/` is untouched and `dist.tmp/` exists for inspection. **Invariant: atomic swap.**
- `api/tests/test_static_build.py::test_successful_build_replaces_dist_atomically` — successful build, assert old contents replaced and `dist.bak/` holds the prior tree.
- `api/tests/test_static_build.py::test_build_calls_service_layer_not_route_handlers` — assert via import-graph inspection (or test double on the FastAPI app) that the builder does not import `api.<feature>.routes`. **Invariant: Hard decision #6 service-layer reuse.**
- `api/tests/test_static_build.py::test_disk_full_during_build_returns_disk_full_error` — fault-inject, assert `BuildStaticResponse` errored with `code='disk_full'` and `dist/` untouched.
- `api/tests/test_observability.py::test_request_id_propagated_to_runner_logs` — fire a `POST /api/v1/runs`, capture stdout, assert every log line for the resulting run carries the same `request_id`.
- `api/tests/test_observability.py::test_anthropic_refusal_text_not_in_logs` — mock Anthropic to return a refusal, assert refusal text never appears in stdout (only its hash, per Security §logging discipline).
- `api/tests/test_observability.py::test_metric_events_emitted_for_run` — run a 3-sentence run, assert `metric.run.duration_ms`, `metric.run.item_count`, three `metric.anthropic.call.latency_ms` events appear with the documented label set.
- `api/tests/test_observability.py::test_diag_endpoint_returns_commit_hash` — assert `/admin/diag` returns a commit hash matching `git rev-parse --short HEAD`. **Invariant: user-CLAUDE.md "verify your assumptions" guarantee.**
- `api/tests/test_backup.py::test_periodic_backup_creates_snapshot` — run with `BACKUP_INTERVAL_SECONDS=1`, wait, assert a file lands in `data/backups/` and `PRAGMA integrity_check` passes against it.
- `api/tests/test_backup.py::test_pre_import_backup_runs_before_import_transaction` — call reimport, assert a backup snapshot exists with a timestamp before the importer's `import.started` log line.
- `api/tests/test_backup.py::test_retention_keeps_last_n_plus_pre_import` — seed `BACKUP_RETENTION_COUNT+5` snapshots and one pre-import snapshot older than the retention window, run the cleanup, assert the most recent N + pre-import survive.
- `api/tests/test_backup.py::test_make_restore_refuses_when_pid_file_live` — write a PID file pointing at a live process, run restore, assert refusal.

**Frontend unit tests** (`web/__tests__/sse.test.tsx`):

- `test_sse_reducer_idempotent_on_duplicate_item_completed` — feed the reducer an `item_completed` for the same `(run_id, ordinal)` twice, assert state is identical to one application.
- `test_sse_reducer_resets_on_replay_begin` — feed `replay_begin` after stale state, assert the `seenOrdinals` set is empty.
- `test_sse_reducer_ignores_mismatched_run_id` — feed events with a stale `run_id`, assert no state change.

**Eval coverage** (registers the project as a new factory under the user's evals harness — `bible_study` joins `pagehub`, `prayers`, etc.). All seeds copy `pagehub_messaging.py` as the structural template and assume the evals service at `http://localhost:4002` is up (`curl -sf http://localhost:4002/health` is documented as the seeding precondition; `make seed-evals` runs the precondition check before invoking the seed scripts):

- **Spec markdown**: `~/github/pagehub-io/platform/evals/specs/bible-study/<spec>.md` for each user-story below; one markdown per story so a failure traces to one story.
- **Seed scripts** (each adds request templates + per-request `evaluations` that assert HTTP status + JSON-path conditions on the response):
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_happy_path.py` — request prefix `bs/happy/`. Captures: `manifest_hash`, `run_id`, `candidate_id`, `dist_path`. Steps: `POST /admin/reimport` (assert 200, `fixture_version` present); `GET /admin/fixture-status` (assert `stale=false`); `POST /api/v1/runs` with a one-sentence scope (assert 200, capture `run_id`); poll `GET /runs/{run_id}/stream` until terminal (assert `run_finished`); `GET /api/v1/sentences/{id}` (assert candidate present); `POST /api/v1/rankings/{id}` with `If-Match: 0` (assert 200, `version=1`); `POST /api/v1/admin/build-static` (assert 200, capture `dist_path`); `GET /api/v1/gsv/export?format=json` (assert 200, sentence appears with `kind: "claude"` provenance). **Covers: ingest → run → rank → build → export.**
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_run_completion.py` — request prefix `bs/run/`. Asserts a 3-sentence run reaches `run_finished` with all items `completed`. Captures `run_id` and asserts `generation_run_items` count via `GET /admin/diag`.
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_sse_reconnect.py` — request prefix `bs/sse/`. Two SSE connects to the same `run_id`, asserts both end in identical `seenOrdinals` sets (the eval harness's SSE inspector collects the sequence). **Covers: reconnect-replay convergence.**
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_occ_conflict.py` — request prefix `bs/occ/`. Two `POST /api/v1/rankings/{id}` calls with the same `If-Match`; first asserts 200, second asserts 409 with `code='stale_version'` and `current_version` in the body. **Covers: 409 conflict surface.**
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_cancel_resume.py` — request prefix `bs/cancel/`. Start a run, immediately `POST /runs/{id}/cancel` (assert 200), then `POST /runs/{id}/resume` (assert 200, capture new `run_id`); assert new `run_id` ≠ original and `parent_run_id` matches. **Covers: cancel + resume.**
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_restart_recovery.py` — request prefix `bs/restart/`. Start a run, signal the test harness to restart the API process between SSE events, reconnect; assert the run row's terminal status is `interrupted`, then `POST /runs/{id}/resume` (assert 200, new `run_id`). **Covers: server-restart recovery + Resume contract. Requires the harness to support a process-restart hook between requests; if the harness does not, this seed is replaced with a unit-test-only check (`test_orphaned_running_runs_marked_interrupted_on_startup` already covers the in-process invariant) and the gap is recorded under "Open coverage gaps."**
  - `~/github/pagehub-io/platform/evals/seeds/bible_study_fixture_hash_mismatch.py` — request prefix `bs/fixture/`. **Precondition: harness must run against `env=development`** — the test-only admin hooks are mounted only when `settings.env == "development"` (Security §Test-only admin hooks). Steps: `POST /api/v1/admin/test-only/mutate-fixture-byte { fixture_path: "fixtures/sblgnt/matthew.json", byte_offset: <chosen> }` (captures `previous_byte` and `new_sha256` for teardown); `POST /admin/reimport` and asserts 400 with `code='fixture_hash_mismatch'` and that the response names the file path. **Teardown step (mandatory)**: `POST /api/v1/admin/test-only/restore-fixture-byte { fixture_path, byte_offset, original_byte: <captured previous_byte> }` followed by `POST /api/v1/admin/test-only/reset-fixture-state { confirm: true }` as belt-and-braces (per Data §Test-only admin hook fixture isolation). Without the teardown a crashed eval leaves the fixture mutated and every subsequent `make import` fails. **Covers: fixture checksum failure mode.**

  Each seed asserts (a) the documented status code, (b) the documented `code`/`error_code` enum value where applicable, and (c) for the happy-path seed, the absence of any leaked secret in the response (re-uses Security §test #5's pattern as a defence-in-depth at the eval layer).

- **Concurrency / fault injection**: the OCC tests above use real `aiosqlite` against a temp file (not a mock) — Postgres-equivalent fidelity isn't applicable since SQLite is the target. The cancel-during-in-flight test uses a real `asyncio` runner with mocked Anthropic returning controlled latencies so the in-flight count is observable. The disk-full tests use `unittest.mock.patch` on the SQLite connection's `execute` to raise `OperationalError`; these are unit-level and don't touch the real filesystem.
- **Open coverage gaps** (acknowledged, named so they don't drift):
  - **Real Anthropic 429 / 5xx behaviour**: tests mock the SDK; we don't exercise live Anthropic. Acceptable for v1 — a contract test against the SDK's documented error types is what we get.
  - **Real OS-level disk-full**: tests mock `OperationalError`; we don't fill a real partition. Acceptable — the SQLite error message matching is what the importer/runner branch on, and that's what we mock.
  - **Real browser SSE reconnect timing**: the frontend reducer test feeds events synchronously; we don't run a real browser disconnecting / reconnecting at network level. The eval-seed `bs/sse/` covers the protocol contract; full browser fidelity is deferred to manual smoke-testing in `make dev`.
  - **Eval-harness process-restart hook**: if the evals harness does not support restarting the API mid-flight, `bs/restart/` falls back to unit-test-only coverage (named above). The PLAN slice that ships `bs/restart/` should verify harness support before merge; if absent, drop the seed and document that the unit test is the sole guardrail.
  - **Backup correctness over time**: `test_periodic_backup_creates_snapshot` covers one cycle; we don't simulate weeks of backups + retention pressure. Acceptable — retention is a `range(BACKUP_RETENTION_COUNT)` loop, not a complex eviction policy.

AGREE: yes

## Data

The Security and Reliability sections already pinned: SHA-256 fixture hashes, OCC `version` columns on rankings/overlays/tie-breaks/`hidden_combos`, `BEGIN IMMEDIATE` for OCC writes, atomic per-sentence transactions in the runner, importer atomic-or-nothing, `VACUUM INTO` backups, `data/bible_study.db` gitignored. This section turns those preconditions into concrete column lists, indexes, FK / cascade rules, retention policies, and the algorithms that compute `OrphanSummary` and `BuildStaticCoverage`. The Architect section pinned canonicalization (UTF-8 + `ensure_ascii=False` + `sort_keys=True` + `(',',':')` + integers-only + NFC); we re-state it only where it lands in `INSERT` paths.

### SQLite engine settings (pinned)

Applied at every connection open (`api/db/connection.py`); these are part of the data contract, not a tuning knob:

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;            -- WAL durability + crash safety; FULL is overkill, OFF is unsafe
PRAGMA foreign_keys = ON;                -- SQLite default is OFF; FKs below assume this
PRAGMA busy_timeout = 5000;              -- Reliability §SQLite locked
PRAGMA wal_autocheckpoint = 1000;        -- Reliability §WAL checkpointing
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -20000;              -- ~20 MB page cache; one-user workload, plenty
```

`PRAGMA foreign_keys = ON` is mandatory — every `FOREIGN KEY` declared below is enforced only with this flag set, and several invariants (orphan detection, cascade-delete on overlay parent chain) rely on it. Tests open a connection and assert `PRAGMA foreign_keys` returns 1.

### Migrations

**Tool: Alembic** (with the `--sql` offline mode used to generate human-reviewable migration scripts; runtime applies them via `alembic.command.upgrade`). Trade-offs considered:

- **Alembic** (chosen): batteries-included version table (`alembic_version`), forward + downgrade per revision, and the team-standard pattern for FastAPI projects. Slightly heavyweight for a one-user tool but the cost is a single `alembic/` directory and a `revision.py` per change. Pays off the first time a fixture-version bump requires a real schema migration (e.g. v2 Byzantine sentences).
- **Hand-written SQL files** (`migrations/0001_init.sql`, `migrations/0002_*.sql`) iterated by a tiny custom runner: rejected — we'd reinvent `alembic_version` and a downgrade ledger badly.
- **`sqlite-utils` migrate**: rejected — it's an authoring tool, not a versioned migration framework; no downgrade path.

**Migration discipline:**
- Every revision has a working `downgrade()` (Alembic enforces this; we enforce non-trivial bodies in code review). For destructive forward ops on overlay tables, `downgrade()` is a `raise NotImplementedError("forward-only — restore from backup")` with the load-bearing comment `# pre-import backup is the rollback path` — but only for explicitly forward-only revisions (e.g. dropping a deprecated column where the data is unrecoverable). Default is real downgrade.
- **Online-safety**: SQLite's `ALTER TABLE` is limited (no `DROP COLUMN` until 3.35, no `ALTER COLUMN`). For any change that SQLite can't do in place, the revision uses the **rebuild dance**: `CREATE TABLE <t>_new (…)` + `INSERT INTO <t>_new SELECT …` + `DROP TABLE <t>` + `ALTER TABLE <t>_new RENAME TO <t>` + recreate indexes. This is well-understood and Alembic's `batch_alter_table` automates it.
- **Migration runs before the import transaction opens** (Reliability §Failure modes). Migration is idempotent (Alembic version table); a partial migration leaves the version table at the last successful revision so rerunning resumes correctly.
- **Downgrade rollback for v1**: every v1 revision has a tested `downgrade()` (test below). v1 `0001_init` has `downgrade()` that drops every table — equivalent to `rm data/bible_study.db`, deliberately destructive, and gated behind an Alembic explicit invocation.

Initial migration `0001_init` creates everything below in one revision. Subsequent revisions are per-feature (e.g. `0002_add_word_strong_id_index`).

### Shapes

All `TEXT` for IDs (sentence IDs, run IDs, hashes) is intentional — SQLite's storage classes are duck-typed and the surrogate-integer shortcut (`INTEGER PRIMARY KEY` ROWID alias) is reserved for tables where identity is system-assigned and never displayed (`claude_candidates.candidate_id`, `red_letter_overlays.overlay_id`). All timestamps are ISO-8601 UTC strings (`2026-05-03T12:34:56.789Z` format) — SQLite has no native datetime; storing as text keeps queries grep-able and avoids the `julianday` footguns.

#### `fixture_version` (manifest pin, single-row table)

```sql
CREATE TABLE fixture_version (
  id INTEGER PRIMARY KEY CHECK (id = 1),    -- single-row sentinel
  manifest_hash TEXT NOT NULL,              -- SHA-256 of fixtures/manifest.json (canonicalised same as source_snapshots)
  manifest_version TEXT NOT NULL,           -- the manifest's own version field, e.g. "2026.05-01"
  segmenter_rule TEXT NOT NULL,             -- "sblgnt-punctuation-v1"
  word_tokenizer_rule TEXT NOT NULL,        -- "greek-letters-with-clitics-v1"
  byzantine_mode TEXT NOT NULL,             -- "verse-only-v1"
  source_snapshot_canon_version TEXT NOT NULL,  -- "v1" — Architect's canonicalization rule version
  last_imported_at TEXT NOT NULL,
  files_imported INT NOT NULL,
  sentences_built INT NOT NULL,
  words_built INT NOT NULL,
  red_letter_source_ranges INT NOT NULL
);
```

The `CHECK (id = 1)` is the SQLite single-row idiom; `INSERT OR REPLACE` writes the new manifest pin atomically. The status pill's `GET /admin/fixture-status` reads this row and compares `manifest_hash` to the current disk hash.

**Indexes:** none (single row).

#### `sentences` (SBLGNT-canonical, immutable from fixtures)

```sql
CREATE TABLE sentences (
  sentence_id TEXT PRIMARY KEY,             -- "mat-{chapter}-{ordinal_in_chapter}", chapter 1..28, ordinal 1-indexed
  chapter INT NOT NULL CHECK (chapter BETWEEN 1 AND 28),
  ordinal_in_chapter INT NOT NULL CHECK (ordinal_in_chapter >= 1),
  text_sblgnt TEXT NOT NULL,                -- full SBLGNT sentence text, NFC-normalised at import
  start_chapter INT NOT NULL,               -- always == chapter for v1 (no cross-chapter sentences)
  start_verse INT NOT NULL,
  end_chapter INT NOT NULL,
  end_verse INT NOT NULL,
  starts_at_verse_boundary INT NOT NULL CHECK (starts_at_verse_boundary IN (0, 1)),
  ends_at_verse_boundary INT NOT NULL CHECK (ends_at_verse_boundary IN (0, 1)),
  word_count INT NOT NULL CHECK (word_count >= 1),
  byte_size INT NOT NULL,
  CHECK (start_chapter <= end_chapter),
  CHECK (start_chapter < end_chapter OR start_verse <= end_verse),
  UNIQUE (chapter, ordinal_in_chapter)
);
CREATE INDEX idx_sentences_chapter ON sentences (chapter, ordinal_in_chapter);
CREATE INDEX idx_sentences_verse_range ON sentences (start_chapter, start_verse, end_chapter, end_verse);
```

**`starts_at_verse_boundary` and `ends_at_verse_boundary` (Designer R1/R4 lock-down):** computed by the segmenter as integer flags (SQLite has no native bool). `starts_at_verse_boundary=1` iff the sentence's first character is the first non-whitespace character of `start_verse`; `ends_at_verse_boundary=1` iff the sentence's last non-whitespace character is the last non-whitespace character of `end_verse`. Whitespace-and-punctuation between verses is part of neither verse's contribution to the test. Both flags are stored, not derived at query time — Designer's R1/R4 rules are read on every parallel-reader render and the cost of recomputing per row is silly; storage is one byte each.

The `(start_chapter, start_verse, end_chapter, end_verse)` index supports the `RunScope` `verse_range` expansion (`WHERE start_chapter = ? AND end_verse >= ? AND start_verse <= ?`) and the parallel reader's per-chapter row fetch.

#### `words` (per-sentence tokenisation; Hard decision #3)

```sql
CREATE TABLE words (
  sentence_id TEXT NOT NULL,
  ordinal INT NOT NULL CHECK (ordinal >= 1),
  text TEXT NOT NULL,                       -- NFC-normalised Greek word
  strong_id TEXT,                            -- "G3107" etc., NULL when BIB doesn't align this word
  byte_start INT NOT NULL,                  -- offset within sentences.text_sblgnt (UTF-8 bytes)
  byte_end INT NOT NULL,                    -- exclusive
  CHECK (byte_end > byte_start),
  PRIMARY KEY (sentence_id, ordinal),
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id) ON DELETE CASCADE
);
CREATE INDEX idx_words_strong ON words (strong_id) WHERE strong_id IS NOT NULL;
```

The partial index on `strong_id` is the BIB-alignment lookup index (e.g. "every word in Matthew with Strong's `G3107`"); the partial form skips the unaligned-word rows so the index is small.

`ON DELETE CASCADE` from `sentences`: when an import drops + rebuilds source-text tables, dropping `sentences` would otherwise leave `words` rows orphaned. Cascade is the correct relationship for "rebuilt-from-fixtures" tables.

#### `byzantine_verses` (verse-keyed, no Byzantine sentence rows in v1)

```sql
CREATE TABLE byzantine_verses (
  chapter INT NOT NULL CHECK (chapter BETWEEN 1 AND 28),
  verse INT NOT NULL CHECK (verse >= 1),
  text TEXT NOT NULL,                       -- NFC-normalised
  byte_size INT NOT NULL,
  PRIMARY KEY (chapter, verse)
);
```

No FK to `sentences` — Byzantine verses are queried by `(chapter, verse)` against an SBLGNT sentence's verse range at read time. Architect's deferral to v2 means we add `byzantine_sentences` and `byzantine_words` as additive tables when needed; nothing in v1 depends on Byzantine sentence shape.

#### Translation tables (BSB / BLB / BIB / WEB)

**Decision: one shared `english_verses` table** keyed `(translation, chapter, verse)`, **plus a separate `bib_interlinear_words` table** because BIB's per-word alignment data is structurally different (Greek word + Strong's + per-word gloss) and squishing it into a verse-keyed text column would lose the Strong's-keyed structure that's the whole point of BIB.

Trade-offs considered:

- **One table per translation** (`bsb_verses`, `blb_verses`, …): rejected — translations are added/removed by changing `translation` enum values, not by schema migrations. WEB would have shipped as a parallel-reader-only table and the schema would diverge from BSB/BLB for no reason.
- **Shared `english_verses` table** (chosen): one schema, one set of indexes, one query path. The translation enum is a fixed v1 set (BSB, BLB, WEB); BIB is decomposed into `bib_interlinear_words` because its row shape *is* different.
- **Polymorphic JSON**: rejected — query cost on JSON path expressions for a hot-path (parallel reader) read is unacceptable.

```sql
CREATE TABLE english_verses (
  translation TEXT NOT NULL CHECK (translation IN ('BSB', 'BLB', 'WEB')),
  chapter INT NOT NULL CHECK (chapter BETWEEN 1 AND 28),
  verse INT NOT NULL CHECK (verse >= 1),
  text TEXT NOT NULL,                       -- NFC-normalised, line-stripped
  byte_size INT NOT NULL,
  PRIMARY KEY (translation, chapter, verse)
);
CREATE INDEX idx_english_verses_chapter ON english_verses (chapter, verse, translation);

CREATE TABLE bib_interlinear_words (
  chapter INT NOT NULL,
  verse INT NOT NULL,
  position INT NOT NULL,                    -- 1-indexed within verse (BIB row order)
  greek_form TEXT NOT NULL,                 -- as printed in BIB (NFC)
  strong_id TEXT NOT NULL,                  -- "G3107" — BIB always supplies one
  transliteration TEXT NOT NULL,
  english_gloss TEXT NOT NULL,
  inflected_meaning TEXT,
  PRIMARY KEY (chapter, verse, position)
);
CREATE INDEX idx_bib_strong ON bib_interlinear_words (strong_id);
CREATE INDEX idx_bib_greek ON bib_interlinear_words (chapter, verse, greek_form);
```

The aligner (`scripts/align.py`) reads `bib_interlinear_words` and matches `(chapter, verse, greek_form)` against `(words via sentences.start/end_chapter/verse)` to populate `words.strong_id`. The `idx_bib_greek` index supports that join.

`(chapter, verse, translation)` index on `english_verses` supports Designer's R1/R4 reference-rendering queries which fetch *all* translations for a sentence's verse range at once.

#### `red_letter_source_ranges` (immutable from fixture, v1: hand-curated)

```sql
CREATE TABLE red_letter_source_ranges (
  source_range_id INTEGER PRIMARY KEY,      -- ROWID alias, system-assigned
  start_sentence_id TEXT NOT NULL,
  start_word_offset INT NOT NULL CHECK (start_word_offset >= 1),  -- 1 = whole sentence
  end_sentence_id TEXT NOT NULL,
  end_word_offset INT NOT NULL,             -- inclusive, in end sentence's word ordinals
  origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('berean', 'manual')),
  note TEXT,                                -- optional curation note from the fixture
  imported_at TEXT NOT NULL,
  FOREIGN KEY (start_sentence_id) REFERENCES sentences(sentence_id),
  FOREIGN KEY (end_sentence_id) REFERENCES sentences(sentence_id)
);
CREATE INDEX idx_rls_start ON red_letter_source_ranges (start_sentence_id);
CREATE INDEX idx_rls_end ON red_letter_source_ranges (end_sentence_id);
```

No `ON DELETE CASCADE` to `sentences` — this is one of the load-bearing FK pairs that lets the importer's pre-flight orphan check fire (Architect §Importer). If sentences disappear from the new fixture set, the importer's `PRAGMA foreign_key_check` reports the violation *before* dropping the source table. Cascading would silently delete the red-letter source rows — exactly the bug we want surfaced as `OrphanSummary`. Same rule applies to `red_letter_overlays`, `claude_candidates`, `rankings`, `tie_break_decisions`, `hidden_combos` below.

This table is **rebuilt from fixture on every import** (truncate + insert). Rebuild semantics in §Migrations + Idempotent import below.

#### `red_letter_overlays` (append-only chain, mutable, OCC-protected)

```sql
CREATE TABLE red_letter_overlays (
  overlay_id INTEGER PRIMARY KEY,
  parent_overlay_id INTEGER,                -- NULL = first overlay in chain; FK to self
  source_range_id INTEGER,                  -- NULL when origin='manual' and no fixture source
  operation TEXT NOT NULL CHECK (operation IN ('split', 'merge', 'extend', 'retract', 'reject', 'create')),
  start_sentence_id TEXT,                   -- effective start after this op; NULL when rejected
  start_word_offset INT,
  end_sentence_id TEXT,
  end_word_offset INT,
  rejected INT NOT NULL DEFAULT 0 CHECK (rejected IN (0, 1)),
  origin TEXT NOT NULL CHECK (origin IN ('berean', 'manual')),
  created_at TEXT NOT NULL,
  version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
  FOREIGN KEY (parent_overlay_id) REFERENCES red_letter_overlays(overlay_id),
  FOREIGN KEY (source_range_id) REFERENCES red_letter_source_ranges(source_range_id),
  FOREIGN KEY (start_sentence_id) REFERENCES sentences(sentence_id),
  FOREIGN KEY (end_sentence_id) REFERENCES sentences(sentence_id),
  CHECK (
    (rejected = 1 AND start_sentence_id IS NULL AND end_sentence_id IS NULL)
    OR
    (rejected = 0 AND start_sentence_id IS NOT NULL AND end_sentence_id IS NOT NULL
     AND start_word_offset IS NOT NULL AND end_word_offset IS NOT NULL)
  )
);
CREATE INDEX idx_rlo_parent ON red_letter_overlays (parent_overlay_id);
CREATE INDEX idx_rlo_source ON red_letter_overlays (source_range_id);
CREATE INDEX idx_rlo_start ON red_letter_overlays (start_sentence_id, start_word_offset)
  WHERE rejected = 0;
```

**Why a flat chain, not a versioned head pointer:** Designer's "Original diff toggle" needs to walk back to the source range; the parent-chain shape makes that one recursive CTE. A "head pointer" model (one current row, history elsewhere) would force a second history table and break the diff toggle.

**`version` increment rule:** when a UI edit produces a new overlay row (split/merge/extend/retract/reject), the new row starts at `version=1`. The OCC `If-Match` on the *editor* applies to the **current head of the chain** — read path materialises the head (the leaf with no child, per the parent chain) and `If-Match` is the head's `version`. Writing a new operation `INSERT`s a new leaf row pointing at the prior head as its `parent_overlay_id`, with `version = parent.version + 1`. The transaction is `BEGIN IMMEDIATE; SELECT version FROM <head>; INSERT new leaf with version = old + 1 WHERE parent has no other child; COMMIT;`. The "no other child" guard is the OCC race protection.

**Effective set computation** (read-time, used by Red Letters view, BuildStaticCoverage denominator, and rank queue's "is red-letter" check):

```sql
-- Materialise: source ranges minus rejected overlays plus active overlays
WITH overlay_heads AS (
  -- An overlay row is the head of its chain iff no other overlay has it as parent_overlay_id
  SELECT o.* FROM red_letter_overlays o
  WHERE NOT EXISTS (
    SELECT 1 FROM red_letter_overlays c WHERE c.parent_overlay_id = o.overlay_id
  )
)
SELECT
  COALESCE(h.start_sentence_id, s.start_sentence_id) AS start_sentence_id,
  COALESCE(h.start_word_offset, s.start_word_offset) AS start_word_offset,
  COALESCE(h.end_sentence_id, s.end_sentence_id) AS end_sentence_id,
  COALESCE(h.end_word_offset, s.end_word_offset) AS end_word_offset,
  COALESCE(h.origin, s.origin) AS origin
FROM red_letter_source_ranges s
LEFT JOIN overlay_heads h ON h.source_range_id = s.source_range_id
WHERE COALESCE(h.rejected, 0) = 0
UNION ALL
-- Manual-origin overlay heads with no source range (newly-created ranges)
SELECT start_sentence_id, start_word_offset, end_sentence_id, end_word_offset, origin
FROM overlay_heads
WHERE source_range_id IS NULL AND rejected = 0;
```

Materialised once per request and cached in the service layer for the duration of the request (read-only, so cache invalidation is request-scoped). Reliability-grade caching (cross-request) is not in v1.

#### `style_prompts` (fixture metadata; bodies stay in `fixtures/prompts/*.md`)

```sql
CREATE TABLE style_prompts (
  prompt_version TEXT PRIMARY KEY,          -- "literal-v1", "dynamic-v1", "plainspoken-v1"
  name TEXT NOT NULL,                       -- "literal" (the family)
  description TEXT NOT NULL,
  requires_greek INT NOT NULL CHECK (requires_greek IN (0, 1)),
  compatible_source_sets TEXT NOT NULL,     -- JSON array of source_set_id values
  body_path TEXT NOT NULL,                  -- "fixtures/prompts/literal-v1.md"
  body_sha256 TEXT NOT NULL,                -- SHA-256 of the body (post-front-matter), hex
  imported_at TEXT NOT NULL
);
```

**Body content stored on disk, not in DB.** Reasons:
- The body is part of the supply chain (Security §Supply chain) — keeping it in `fixtures/prompts/` keeps it under git review.
- Storing it in SQLite would mean the DB becomes the prompt source of truth, breaking the "fixtures are durable, SQLite is rebuildable" model (Hard decision #7).
- The hash in this row is what the runner verifies before reading the file — defence-in-depth against a runtime swap of the on-disk file.

This table is rebuilt from fixture on every import (truncate + insert).

#### `claude_candidates` (immutable, surrogate PK, content-addressed sources)

```sql
CREATE TABLE claude_candidates (
  candidate_id INTEGER PRIMARY KEY,         -- ROWID alias
  sentence_id TEXT NOT NULL,
  style_prompt_version TEXT NOT NULL,
  source_set_id TEXT NOT NULL CHECK (source_set_id IN (
    'SBLGNT_ONLY', 'BYZ_ONLY', 'BOTH_GREEK',
    'GREEK_PLUS_BIB', 'GREEK_PLUS_BLB', 'GREEK_PLUS_BSB',
    'ENGLISH_ONLY_BSB'
  )),
  model TEXT NOT NULL,                      -- "claude-3-5-sonnet-20241022" etc.
  generated_at TEXT NOT NULL,               -- ISO-8601 UTC, the candidate's displayed identity
  candidate_text TEXT NOT NULL,             -- Claude's output, treated as untrusted
  source_snapshot_hash TEXT NOT NULL,
  hidden_bool INT NOT NULL DEFAULT 0 CHECK (hidden_bool IN (0, 1)),
  latency_ms INT,                           -- recorded by runner; nullable for back-fill safety
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id),
  FOREIGN KEY (style_prompt_version) REFERENCES style_prompts(prompt_version),
  FOREIGN KEY (source_snapshot_hash) REFERENCES source_snapshots(snapshot_hash)
);
CREATE INDEX idx_cc_sentence ON claude_candidates (sentence_id);
CREATE INDEX idx_cc_combo ON claude_candidates (sentence_id, style_prompt_version, source_set_id, model);
CREATE INDEX idx_cc_snapshot ON claude_candidates (source_snapshot_hash);
CREATE INDEX idx_cc_generated_at ON claude_candidates (generated_at);
```

**Indexes justified:**
- `idx_cc_sentence`: the rank UI fetches all candidates for one sentence — load-bearing.
- `idx_cc_combo`: the "has a candidate from this combo in the last hour" duplicate-batch warning (Designer Flow 4 step 4) and the rank-queue filter use this.
- `idx_cc_snapshot`: dedupe accounting and orphan-summary computation.
- `idx_cc_generated_at`: retention scans (see §Retention below).

`hidden_bool` is the per-row predicate for one-off hides (Hard decision #15); the `hidden_combos` table is the broader combo-level predicate.

`source_set_id` is enforced as a `CHECK` constraint (not a separate enum table) — the seven values are pinned in v1 contracts and a future `GREEK_PLUS_WEB` is a migration revision that updates the CHECK.

**No FK to `red_letter_source_ranges` / overlays** — generation may target any sentence, red-letter or not (Manager §In scope).

#### `source_snapshots` (content-addressed, append-on-hash)

```sql
CREATE TABLE source_snapshots (
  snapshot_hash TEXT PRIMARY KEY,           -- SHA-256 hex of canonicalised payload_json
  sentence_id TEXT NOT NULL,
  source_set_id TEXT NOT NULL,
  fixture_version TEXT NOT NULL,            -- the fixture_version.manifest_hash at snapshot time
  prompt_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,               -- canonicalised UTF-8 JSON (the bytes that hashed to snapshot_hash)
  byte_size INT NOT NULL,
  source_snapshot_canon_version TEXT NOT NULL,  -- "v1" — re-snapshots on rule change
  created_at TEXT NOT NULL,
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id)
);
CREATE INDEX idx_ss_sentence_combo ON source_snapshots (sentence_id, source_set_id, prompt_version);
CREATE INDEX idx_ss_fixture_version ON source_snapshots (fixture_version);
```

**Ingestion / dedup path** (single transaction, called from runner per Reliability §generation runner durability):

```sql
-- 1. Compute hash from canonicalised payload (Architect rule, in Python: NFC + json.dumps with the pinned kwargs)
-- 2. Upsert by hash:
INSERT OR IGNORE INTO source_snapshots (
  snapshot_hash, sentence_id, source_set_id, fixture_version, prompt_version,
  payload_json, byte_size, source_snapshot_canon_version, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
-- 3. The candidate INSERT references the hash via FK; OR IGNORE either inserted or the hash already exists
INSERT INTO claude_candidates (..., source_snapshot_hash, ...) VALUES (..., ?, ...);
```

`INSERT OR IGNORE` is the dedup mechanism — same hash means same canonicalised bytes means same content (collision-resistant under SHA-256). Two concurrent runners producing the same combo dedup to one row; the FK ensures the candidate references a live snapshot.

`source_snapshot_canon_version` is stored on the row (not just on `fixture_version`) so a future canonicalization rule change can re-snapshot only the rows that need it (`WHERE source_snapshot_canon_version != 'v2'`) without losing the old hash provenance.

#### `hidden_combos` (combo-level hide, OCC-protected)

```sql
CREATE TABLE hidden_combos (
  sentence_id TEXT NOT NULL,
  style_prompt_version TEXT NOT NULL,
  source_set_id TEXT NOT NULL,
  model TEXT NOT NULL,
  hidden_at TEXT NOT NULL,
  version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
  PRIMARY KEY (sentence_id, style_prompt_version, source_set_id, model),
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id)
);
CREATE INDEX idx_hc_sentence ON hidden_combos (sentence_id);
```

**`version` increment rule:** new row starts at `version=1`. Toggling hide → unhide → hide is two writes against the same PK; on hide, `INSERT OR REPLACE` increments `version` by reading the existing row's version inside `BEGIN IMMEDIATE`. On unhide, the row is `DELETE`d. **Re-hide after unhide starts at `version=1` again** (no row exists) — this is fine because OCC's `If-Match` is checked against the current state, and "no row" is a sentinel state the UI handles (304/404 path).

**Effective hide rule (read path), used by rank UI:**

```sql
SELECT cc.*, (cc.hidden_bool = 1 OR hc.sentence_id IS NOT NULL) AS effectively_hidden
FROM claude_candidates cc
LEFT JOIN hidden_combos hc ON
  hc.sentence_id = cc.sentence_id
  AND hc.style_prompt_version = cc.style_prompt_version
  AND hc.source_set_id = cc.source_set_id
  AND hc.model = cc.model
WHERE cc.sentence_id = ?;
```

#### `rankings` + `ranking_entries` (parent + child; Hard decision #17 — one shared `version`)

```sql
CREATE TABLE rankings (
  sentence_id TEXT PRIMARY KEY,
  notes TEXT,                               -- the free-text Designer Flow 5 #5 textarea
  status TEXT NOT NULL CHECK (status IN ('partial', 'ranked', 'skipped')) DEFAULT 'partial',
  version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
  updated_at TEXT NOT NULL,
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id)
);

CREATE TABLE ranking_entries (
  sentence_id TEXT NOT NULL,
  position INT NOT NULL CHECK (position >= 1),  -- the order Gavin saved (not the rank — multiple positions can share a rank)
  rank INT NOT NULL CHECK (rank >= 1),
  tied_with_above INT NOT NULL DEFAULT 0 CHECK (tied_with_above IN (0, 1)),
  candidate_kind TEXT NOT NULL CHECK (candidate_kind IN ('translation', 'claude')),
  -- discriminated union: exactly one of the next two paths is populated
  translation_name TEXT,                    -- 'BSB' | 'BLB' | 'BIB' | 'WEB' | 'SBLGNT' | 'BYZ'
  translation_verse_range TEXT,             -- "5:3-5:4"
  claude_candidate_id INTEGER,              -- FK to claude_candidates
  PRIMARY KEY (sentence_id, position),
  FOREIGN KEY (sentence_id) REFERENCES rankings(sentence_id) ON DELETE CASCADE,
  FOREIGN KEY (claude_candidate_id) REFERENCES claude_candidates(candidate_id),
  CHECK (
    (candidate_kind = 'translation' AND translation_name IS NOT NULL AND claude_candidate_id IS NULL)
    OR
    (candidate_kind = 'claude' AND claude_candidate_id IS NOT NULL AND translation_name IS NULL)
  )
);
CREATE INDEX idx_re_candidate ON ranking_entries (claude_candidate_id) WHERE claude_candidate_id IS NOT NULL;
```

`ON DELETE CASCADE` on `ranking_entries.sentence_id → rankings.sentence_id` is intentional — Reliability §OCC pinned the atomic save shape as `DELETE FROM ranking_entries WHERE sentence_id=?; INSERT INTO ranking_entries …` inside one `BEGIN IMMEDIATE`. The cascade is a defence-in-depth — if a future code path drops a `rankings` row directly the entries don't dangle.

**No `ON DELETE` on the candidate FK** — if a candidate disappears (importer pre-flight should have caught this), the orphan-resolution flow handles it. Cascade-deleting ranking entries on candidate disappearance would silently lose Gavin's authored ranking work, exactly the failure Manager flagged.

**Notes lives on `rankings`, not on `ranking_entries`** (Hard decision #17): the parent's `version` covers both the notes column and the entries list; a notes-only edit is `UPDATE rankings SET notes=?, version=version+1 WHERE sentence_id=? AND version=?` and conflicts against a concurrent rank-only edit through the same `version`.

**`status` derivation:** computed at write time inside the `BEGIN IMMEDIATE` (not at read time): `partial` if entries exist but every entry is rank ≥ 2 or `claude_candidate_id IS NULL` for some candidates the sentence has, `ranked` if at least one entry is at rank 1 with a non-hidden candidate, `skipped` if Gavin clicked Skip. The exact predicate sits in `api/rank/service.py` as a single function called by every save path.

#### `tie_break_decisions` (per-resolution row, OCC-protected)

```sql
CREATE TABLE tie_break_decisions (
  sentence_id TEXT NOT NULL,
  resolved_at TEXT NOT NULL,                -- ISO-8601 UTC, multiple decisions per sentence are allowed (re-tie-break)
  winner_kind TEXT NOT NULL CHECK (winner_kind IN ('translation', 'claude')),
  winner_translation_name TEXT,
  winner_translation_verse_range TEXT,
  winner_claude_candidate_id INTEGER,
  tied_against_json TEXT NOT NULL,          -- canonicalised JSON array of candidate_ref objects
  reason TEXT NOT NULL DEFAULT 'tie-broken-by-Gavin',
  version INT NOT NULL DEFAULT 1 CHECK (version >= 1),
  PRIMARY KEY (sentence_id, resolved_at),
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id),
  FOREIGN KEY (winner_claude_candidate_id) REFERENCES claude_candidates(candidate_id),
  CHECK (
    (winner_kind = 'translation' AND winner_translation_name IS NOT NULL AND winner_claude_candidate_id IS NULL)
    OR
    (winner_kind = 'claude' AND winner_claude_candidate_id IS NOT NULL AND winner_translation_name IS NULL)
  )
);
CREATE INDEX idx_tbd_sentence ON tie_break_decisions (sentence_id, resolved_at DESC);
```

The "current" tie-break for a sentence is `MAX(resolved_at)` — multiple decisions retained as history (Manager: every GSV sentence is traceable; a re-tie-break is an authored event worth preserving). GSV export reads only the latest.

`version` increments on each new row. `If-Match` against the latest's version protects against two tabs each opening the tie-break modal; the second commit gets `409`.

#### `generation_runs` (one row per run; the run-level envelope)

```sql
CREATE TABLE generation_runs (
  run_id TEXT PRIMARY KEY,                  -- UUID4 string for human-grep-ability and URL safety
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'interrupted')),
  scope_json TEXT NOT NULL,                 -- canonicalised RunScope as serialized at creation time
  style_prompt_version TEXT NOT NULL,
  source_set_id TEXT NOT NULL,
  model TEXT NOT NULL,
  estimated_cost_usd_x10000 INT NOT NULL,   -- cost in 0.0001 USD units (Architect: integers only)
  estimated_input_units INT NOT NULL,
  parent_run_id TEXT,                       -- set on Resume / Retry; FK to self
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  FOREIGN KEY (parent_run_id) REFERENCES generation_runs(run_id),
  FOREIGN KEY (style_prompt_version) REFERENCES style_prompts(prompt_version)
);
CREATE INDEX idx_gr_status ON generation_runs (status) WHERE status IN ('running', 'pending');
CREATE INDEX idx_gr_created_at ON generation_runs (created_at);
CREATE INDEX idx_gr_parent ON generation_runs (parent_run_id) WHERE parent_run_id IS NOT NULL;
```

**`estimated_cost_usd_x10000` is integer cents-of-cents**: Security §abuse pins the daily-cost cap aggregation as a SQL `SUM`, and Architect §source_snapshots pins "integers only" for canonicalised numeric fields. Storing cost as an integer in 0.0001 USD units (so $5.00 is `50000`) avoids float aggregation drift. Display layer divides by 10000 to render as USD.

The partial index `WHERE status IN ('running', 'pending')` is the load-bearing index for the startup recovery scan and the in-flight cap; healthy steady state has near-zero rows.

#### `generation_run_items` (one row per sentence within a run; SSE-replay grain)

```sql
CREATE TABLE generation_run_items (
  run_id TEXT NOT NULL,
  ordinal INT NOT NULL CHECK (ordinal >= 1),
  sentence_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'interrupted')),
  candidate_id INTEGER,                     -- non-null only when status='completed'
  error_code TEXT CHECK (error_code IN (
    'anthropic_api_error', 'anthropic_refusal', 'timeout', 'rate_limit', 'invalid_response', 'disk_full', 'internal'
  )),
  error_message TEXT,                       -- truncated to MAX_ERROR_MESSAGE_BYTES per Reliability
  started_at TEXT,
  completed_at TEXT,
  PRIMARY KEY (run_id, ordinal),
  FOREIGN KEY (run_id) REFERENCES generation_runs(run_id) ON DELETE CASCADE,
  FOREIGN KEY (sentence_id) REFERENCES sentences(sentence_id),
  FOREIGN KEY (candidate_id) REFERENCES claude_candidates(candidate_id),
  CHECK (
    (status = 'completed' AND candidate_id IS NOT NULL AND error_code IS NULL)
    OR
    (status = 'failed' AND candidate_id IS NULL AND error_code IS NOT NULL)
    OR
    (status IN ('pending', 'running', 'cancelled', 'interrupted') AND candidate_id IS NULL AND error_code IS NULL)
  )
);
CREATE INDEX idx_gri_sentence ON generation_run_items (sentence_id);
CREATE INDEX idx_gri_status ON generation_run_items (run_id, status);
```

**`ordinal` density (Architect pin):** dense `1..N`, no gaps; the `PRIMARY KEY (run_id, ordinal)` is the SSE-replay sort key (`ORDER BY ordinal`). Architect's contract pin is reflected in the `CHECK (ordinal >= 1)` plus the application-level invariant test (§Test coverage below).

**`ON DELETE CASCADE` from `generation_runs`**: dropping a run deletes its items. We don't drop runs in v1 (see retention), but the cascade is correct relationship discipline — items are subordinate to runs.

The CHECK constraint pinning `(status, candidate_id, error_code)` legality is the database-level enforcement of Reliability §Generation runner durability's status-transition rules. A buggy code path that tries to write `status='completed'` without a `candidate_id` fails at DB time, not silently corrupts the data.

**`error_code='disk_full'` is a first-class typed code** (added to the CHECK enum above) — Reliability §Failure modes pins the dispatcher's "3 consecutive `disk_full` items fail the run" rule, and that rule counts via `WHERE error_code='disk_full'` rather than string-equality on `error_message`. String-matching on `error_message` is fragile because (a) the message is truncated to `MAX_ERROR_MESSAGE_BYTES`, (b) message wording can drift across SQLite versions / Python adapter wrappers, and (c) `error_message` is a free-text human-readable field that may include the offending insert details. Promoting `disk_full` to the typed enum makes the runner's damage-bounding logic robust to all three.

`idx_gri_sentence` supports the orphan-summary "what runs reference this sentence" reverse query.

`idx_gri_status` supports the dispatcher's "next pending item" lookup and the Resume "unfinished slice" computation.

#### Optional: `gsv_exports` — **dropped from v1**

Considered tracking which exports have been generated (to surface "last exported at" in the GSV view). Dropped: the GSV view computes coverage live from rankings, and "when was the last `make export-gsv`" is a filesystem `stat` on `gsv.txt` / `gsv.json` / `gsv.md`, not a DB row. Adding a table here would duplicate truth.

### Foreign-key cascade summary (the load-bearing matrix)

| Parent table | Child table | ON DELETE |
|---|---|---|
| `sentences` | `words` | CASCADE |
| `sentences` | `red_letter_source_ranges` | NO ACTION (so importer pre-flight catches orphans) |
| `sentences` | `red_letter_overlays` | NO ACTION |
| `sentences` | `claude_candidates` | NO ACTION |
| `sentences` | `rankings` | NO ACTION |
| `sentences` | `tie_break_decisions` | NO ACTION |
| `sentences` | `hidden_combos` | NO ACTION |
| `sentences` | `source_snapshots` | NO ACTION |
| `sentences` | `generation_run_items` | NO ACTION |
| `rankings` | `ranking_entries` | CASCADE |
| `generation_runs` | `generation_run_items` | CASCADE |
| `generation_runs` | `generation_runs` (parent_run_id) | NO ACTION |
| `claude_candidates` | `ranking_entries` | NO ACTION |
| `claude_candidates` | `tie_break_decisions` | NO ACTION |
| `claude_candidates` | `generation_run_items` | NO ACTION |
| `source_snapshots` | `claude_candidates` | NO ACTION |
| `red_letter_source_ranges` | `red_letter_overlays` | NO ACTION |
| `style_prompts` | `claude_candidates` | NO ACTION |
| `style_prompts` | `generation_runs` | NO ACTION |
| `red_letter_overlays` | `red_letter_overlays` (parent_overlay_id) | NO ACTION |

The pattern: cascade where the child is mechanically subordinate (words to sentences, ranking entries to rankings, run items to runs); never cascade across the importer's authored-vs-fixture boundary. The importer's pre-flight check (§Idempotent import) is what surfaces orphans cleanly, not silent cascade.

### Migrations

(See §Migrations for tool choice and discipline; this subsection is the per-revision plan.)

- **`0001_init`** — creates every table above, every index, every CHECK constraint. `downgrade()` drops them all (destructive; deliberate).
- **Future migrations** documented here as they land (currently empty). v1 ships only `0001_init`.

**Forward-online safety**: `0001_init` is bootstrap-only — runs against an empty DB, no data motion. Subsequent migrations follow the rebuild-dance discipline above; the `make import` flow tolerates the migration running at any moment because the import transaction opens after the migration completes (Reliability §Schema migration during reimport).

**Rollback discipline**: `downgrade()` for any revision that touches an overlay table (`red_letter_overlays`, `claude_candidates`, `rankings`, `ranking_entries`, `tie_break_decisions`, `hidden_combos`, `generation_runs`, `generation_run_items`, `source_snapshots`) is **only valid against a pre-migration backup**. The `make restore` flow plus the per-import backup (Reliability §Backups) is the practical rollback path. Pure schema-down (no data) revisions have a real `downgrade()`.

### Queries (hot paths)

| Hot path | Query shape | Index used | Latency target (single-user) |
|---|---|---|---|
| Parallel reader: chapter view | `SELECT * FROM sentences WHERE chapter=? ORDER BY ordinal_in_chapter` | `idx_sentences_chapter` (PK-aligned) | <5 ms |
| Parallel reader: per-sentence reference text | `SELECT translation, verse, text FROM english_verses WHERE chapter=? AND verse BETWEEN ? AND ?` | `idx_english_verses_chapter` | <5 ms |
| Parallel reader: Byzantine reference | `SELECT verse, text FROM byzantine_verses WHERE chapter=? AND verse BETWEEN ? AND ?` | PK | <5 ms |
| Sentence detail drawer: all candidates | `SELECT cc.*, hc.sentence_id IS NOT NULL AS combo_hidden FROM claude_candidates cc LEFT JOIN hidden_combos hc ON … WHERE cc.sentence_id=?` | `idx_cc_sentence` + PK on `hidden_combos` | <10 ms |
| Rank queue: "has Claude candidates" filter | `SELECT s.sentence_id FROM sentences s WHERE EXISTS (SELECT 1 FROM claude_candidates cc WHERE cc.sentence_id = s.sentence_id) ORDER BY s.chapter, s.ordinal_in_chapter` | `idx_cc_sentence` (covering for EXISTS) | <50 ms over Matthew |
| Rank queue: red-letter intersection | `… AND s.sentence_id IN (effective-red-letter materialise CTE) …` | `idx_rls_start`, `idx_rlo_start` | <100 ms |
| Duplicate-batch warning | `SELECT COUNT(*) FROM claude_candidates WHERE sentence_id IN (…) AND style_prompt_version=? AND source_set_id=? AND model=? AND generated_at >= ?` | `idx_cc_combo` | <20 ms |
| Run dispatcher: next pending item | `SELECT ordinal, sentence_id FROM generation_run_items WHERE run_id=? AND status='pending' ORDER BY ordinal LIMIT 1` | `idx_gri_status` | <2 ms |
| SSE replay | `SELECT * FROM generation_run_items WHERE run_id=? AND status != 'pending' ORDER BY ordinal` | PK | <10 ms per run |
| Daily-cost-cap aggregate | `SELECT SUM(estimated_cost_usd_x10000) FROM generation_runs WHERE created_at >= ?` | `idx_gr_created_at` | <5 ms |
| Startup recovery scan | `SELECT run_id FROM generation_runs WHERE status IN ('running', 'pending')` | partial `idx_gr_status` | <2 ms |
| GSV export | `SELECT s.*, re.* FROM sentences s LEFT JOIN rankings r ON r.sentence_id=s.sentence_id LEFT JOIN ranking_entries re ON re.sentence_id=r.sentence_id AND re.rank=1 WHERE s.chapter=? ORDER BY s.ordinal_in_chapter, re.position` | PKs + `idx_re_candidate` | <100 ms full Matthew |
| BuildStaticCoverage denominator | See §BuildStaticCoverage computation below | — | <50 ms |
| OrphanSummary (importer pre-flight) | See §OrphanSummary computation below | — | <500 ms full Matthew |

**N+1 risk audit:**
- Parallel reader: per-row reference fetch could be N+1 if naive. Mitigation: chapter-view fetches **all** translations / Byzantine / sentences in three queries (one per source), zips in service layer.
- Rank UI per-card snapshot lookup: candidate row already has `source_snapshot_hash`; `payload_json` is fetched on demand only when the user expands "show resolved sources" — not in the default render.
- Run dispatcher: explicitly serial per-sentence by design (Reliability §concurrency cap); no batched fetch needed.

### BuildStaticCoverage computation (Architect pinned the contract; this is the SQL)

```sql
WITH effective_red_letter_sentences AS (
  -- sentences whose ordinal range intersects any non-rejected effective red-letter range
  SELECT DISTINCT s.sentence_id, s.chapter
  FROM sentences s
  JOIN (<effective set CTE from §red_letter_overlays>) er
    ON s.sentence_id BETWEEN er.start_sentence_id AND er.end_sentence_id
       -- NOTE: lexicographic compare over "mat-{chapter}-{ordinal}" needs zero-padding;
       -- service layer expands ranges to a list of sentence_ids and uses IN (?)
),
ranked_sentences AS (
  SELECT r.sentence_id
  FROM rankings r
  WHERE EXISTS (
    SELECT 1 FROM ranking_entries re
    JOIN claude_candidates cc ON cc.candidate_id = re.claude_candidate_id
    WHERE re.sentence_id = r.sentence_id
      AND re.rank = 1
      AND (
        re.candidate_kind = 'translation'  -- translations don't have a hidden flag
        OR (cc.hidden_bool = 0 AND NOT EXISTS (
          SELECT 1 FROM hidden_combos hc
          WHERE hc.sentence_id = cc.sentence_id
            AND hc.style_prompt_version = cc.style_prompt_version
            AND hc.source_set_id = cc.source_set_id
            AND hc.model = cc.model
        ))
      )
  )
)
SELECT
  (SELECT COUNT(*) FROM effective_red_letter_sentences) AS total,
  (SELECT COUNT(*) FROM effective_red_letter_sentences WHERE chapter = 5) AS ch5_total,
  (SELECT COUNT(*) FROM effective_red_letter_sentences er
     WHERE er.sentence_id IN (SELECT sentence_id FROM ranked_sentences)) AS ranked,
  (SELECT COUNT(*) FROM effective_red_letter_sentences er
     WHERE er.chapter = 5
       AND er.sentence_id IN (SELECT sentence_id FROM ranked_sentences)) AS ch5_ranked;
```

Note the lexicographic-compare caveat in the CTE: sentence IDs are `mat-{chapter}-{ordinal}` and the literal text-compare doesn't sort numerically (`mat-1-10` < `mat-1-2`). The service layer expands every red-letter range to a concrete list of sentence IDs ordered by `(chapter, ordinal_in_chapter)` joined against `sentences`, then passes that list as a parameterised `IN (?, ?, …)`. Don't try to do lexicographic ordering on the ID string itself.

### OrphanSummary computation (importer pre-flight)

The importer runs this **before** dropping source-text tables; if `total > 0` and `--force` is not set, returns `409 orphans_detected` with the summary and writes nothing.

**Algorithm:**

1. Stage the new fixtures into a temporary connection (or a memory DB) — call this `new_db`. Build the new `sentences` table from the new SBLGNT fixtures.
2. For each existing `claude_candidates` / `rankings` / `red_letter_overlays` row, check whether its `sentence_id` (or for overlays, both `start_sentence_id` and `end_sentence_id`) exists in `new_db.sentences`.
3. For each missing reference, classify the `OrphanReason`:
   - `sentence_id_remapped`: the sentence ID changed but a new sentence has identical SBLGNT text + matching verse range. Heuristic match: `SELECT new.sentence_id FROM new_db.sentences WHERE new.text_sblgnt = old.text_sblgnt AND new.start_chapter = old.start_chapter AND new.start_verse = old.start_verse LIMIT 1`. If exactly one match, that's `suggested_remap_sentence_id`.
   - `sentence_text_changed`: same `sentence_id`, different `text_sblgnt`. (Old ID still exists in new fixtures — this is a content edit, not a structural change. The orphan flow surfaces it because the candidate was generated against text that's no longer current.)
   - `sentence_removed`: no match by ID and no match by text+verse-range.
4. For each candidate orphan, attach `candidate_text_excerpt` (first 200 chars of `candidate_text`), `old_sentence_text_excerpt` (from current DB), `new_sentence_text_excerpt` (from `new_db`, only if `suggested_remap_sentence_id` is non-null).
5. For each ranking orphan, attach `ranked_candidate_count = COUNT(*) FROM ranking_entries WHERE sentence_id = ?`, `has_notes = (notes IS NOT NULL AND notes != '')`, `has_tie_break = EXISTS(SELECT 1 FROM tie_break_decisions WHERE sentence_id = ?)`, and the current `version`.
6. For each overlay orphan, report per-row (not per-chain) — the resolution UI works at the leaf-row grain.
7. Compute `total = len(candidates) + len(rankings) + len(overlays)`.
8. Return as `OrphanSummary` Pydantic model.

**Implementation note:** the heuristic match in step 3 (`sentence_id_remapped` detection) is run as a single SQL join, not a per-orphan probe — `LEFT JOIN new_db.sentences new ON new.text_sblgnt = old.text_sblgnt AND new.start_verse = old.start_verse` populates a temporary table `candidate_remap_suggestions` queried in Python.

The pre-flight runs against the **live** SQLite (read-only) and a **temporary in-memory new_db**. No mutations to the live DB until the import transaction opens.

### Idempotent import (`make import` SQL semantics)

**"Idempotent" definition (pinned):** running `make import` against the same `fixtures/manifest.json` twice produces byte-identical row content in the immutable-from-fixtures tables: `sentences`, `words`, `byzantine_verses`, `english_verses`, `bib_interlinear_words`, `red_letter_source_ranges`, `style_prompts`, `fixture_version`. The overlay tables (`red_letter_overlays`, `claude_candidates`, `rankings`, `ranking_entries`, `tie_break_decisions`, `hidden_combos`, `source_snapshots`, `generation_runs`, `generation_run_items`) are **untouched** by import — preserved across imports per Hard decision #7.

**Mechanism: TRUNCATE + INSERT (per-table-batched, single transaction):**

```sql
BEGIN IMMEDIATE;
DELETE FROM words;                              -- order matters: child first
DELETE FROM sentences;
DELETE FROM byzantine_verses;
DELETE FROM english_verses;
DELETE FROM bib_interlinear_words;
DELETE FROM red_letter_source_ranges;
DELETE FROM style_prompts;
-- bulk INSERT every fixture-derived row (parametrised, batched in 1000s)
INSERT INTO sentences (...) VALUES (...), (...), ...;
INSERT INTO words (...) VALUES (...), ...;
-- ...etc...
INSERT OR REPLACE INTO fixture_version (id, manifest_hash, ...) VALUES (1, ?, ...);
COMMIT;
```

**Why TRUNCATE+INSERT, not UPSERT-on-PK:**
- The PKs of fixture tables are derived from fixture content (sentence_id from chapter+ordinal_in_chapter; `(chapter, verse)` for verses); a fixture re-segmentation can change which sentence_ids exist. UPSERT-on-PK would leave stale rows with no equivalent in the new fixture.
- Determinism: `DELETE` then `INSERT` produces a known empty-then-populated state; UPSERT produces a state that depends on prior content.
- The orphan pre-flight already rules out the dangerous case (existing overlay tables referencing soon-to-be-deleted sentences); inside the import transaction, dropping the source-text rows is safe.

**Batching:** the `INSERT INTO sentences VALUES (...), (...)` form is bounded by SQLite's `SQLITE_MAX_COMPOUND_SELECT` (default 500); the importer batches in 500-row chunks. `executemany` is the binding-level pattern.

**Error handling:** any failure inside the transaction rolls everything back; SQLite is in pre-import state. The pre-import backup (`VACUUM INTO`, Reliability §Backups) is the second line of defence.

### Retention & PII

This tool stores **no PII** — Gavin is the only user, no accounts, no analytics, no third-party data subjects. Fixture text (SBLGNT, Byzantine, BSB, BLB, BIB, WEB) is public-domain or open-source. Notes (`rankings.notes`) are Gavin's private free-text but local-only, never leaving the device.

| Data | Retained for | Stored at | Anonymization |
|---|---|---|---|
| Fixture-derived rows (sentences, words, verses) | Forever (rebuild from fixtures any time) | `data/bible_study.db` | n/a — public-domain text |
| `claude_candidates` | **No automatic retention.** Manual prune only. | `data/bible_study.db` | n/a — local |
| `source_snapshots` | Bounded by candidate references. Garbage-collect orphan snapshots in a manual `make prune-snapshots` target. | `data/bible_study.db` | n/a |
| `rankings` / `ranking_entries` / `tie_break_decisions` | Forever | `data/bible_study.db` | n/a — Gavin's authored work |
| `red_letter_overlays` (full chain incl. rejected) | Forever | `data/bible_study.db` | n/a — Gavin's authored work |
| `generation_runs` / `generation_run_items` | **24 months** rolling, oldest-first eviction | `data/bible_study.db` | n/a — local |
| `data/backups/*.db` | `BACKUP_RETENTION_COUNT` (default 24) + most-recent-pre-import; hard-capped at `BACKUP_HARD_CAP` (default 32) on persistent integrity-check failure (Reliability §Backups) | `data/backups/` | n/a — local |
| `data/bible_study.pid` | Process lifetime: written at FastAPI startup, removed at clean shutdown (Reliability §Backups → Restore). Stale PID file with no live process is overridable on `make restore` via `FORCE=1`. | `data/` | n/a — pid only, no user data |
| `fixtures/prompts/*.md` | Forever (version-controlled, supply-chain) | `fixtures/` (git) | n/a |
| Logs (stdout) | Whatever Gavin's shell redirect retains — out of scope | stdout / user-controlled | Refusal text hashed (Security §logging) |

**`claude_candidates` retention rationale:** the table can grow indefinitely with re-generation of the same combo (Designer Flow 4 step 7). Reasons not to auto-prune:
- Gavin explicitly compares "the same combo, different days" (Manager: reproducibility is non-negotiable).
- Disk pressure for 28 chapters × ~10 candidates × ~10 KB candidate row = ~3 MB per book; even 100x growth is ignorable.

But infinite growth is not free — the rank UI's "n runs of this combo" group expands indefinitely. The pragmatic policy: **`make prune-candidates DAYS=N`** is a manual, opt-in CLI target that deletes `claude_candidates` rows where:

```sql
DELETE FROM claude_candidates cc
WHERE cc.generated_at < datetime('now', printf('-%d days', :days))
  -- 1. No ranking entry references the candidate
  AND NOT EXISTS (SELECT 1 FROM ranking_entries re WHERE re.claude_candidate_id = cc.candidate_id)
  -- 2. No tie-break decision names this candidate as a winner
  AND NOT EXISTS (SELECT 1 FROM tie_break_decisions tbd WHERE tbd.winner_claude_candidate_id = cc.candidate_id)
  -- 3. No tie-break decision names this candidate as a *loser* (tied_against_json scan)
  AND NOT EXISTS (
    SELECT 1 FROM tie_break_decisions tbd, json_each(tbd.tied_against_json) ja
    WHERE json_extract(ja.value, '$.kind') = 'claude'
      AND CAST(json_extract(ja.value, '$.candidate_id') AS INTEGER) = cc.candidate_id
  )
  -- 4. No in-flight or recently-terminal generation_run_item points at the candidate
  AND NOT EXISTS (
    SELECT 1 FROM generation_run_items gri
    WHERE gri.candidate_id = cc.candidate_id
      AND gri.status IN ('pending', 'running', 'completed')
  );
```

Runs in `BEGIN IMMEDIATE`; emits a log line per deletion. Default-disabled — Gavin runs it when his rank UI feels cluttered. The orphaned `source_snapshots` after a candidate prune are cleaned by the separate `make prune-snapshots` target.

**Why all four exclusions:** without #3 (`tie_break_decisions.tied_against_json` scan via SQLite's `json_each`), pruning a "loser" candidate in a tie-break would leave a dangling reference inside the `tied_against_json` array — the GSV export's `tie-broken` provenance would then surface candidate IDs that no longer exist. Without #4 (`generation_run_items.candidate_id`), an in-flight resume / retry could reference a candidate the prune just deleted. Both are silent-corruption modes; the SQL guards make the prune safe even if it runs concurrently with a generation run (which `BEGIN IMMEDIATE` serialises anyway, but defence-in-depth).

**`generation_runs` retention rationale:** runs are useful for:
- "What prompt did I run last week?" (recent history, ~weeks)
- Resume / Retry chains (need parent_run_id to walk back)
- Daily-cost-cap aggregation (24h rolling)

24 months is a generous outer bound that still keeps the table small (a heavy week is ~50 runs × 100 items = 5000 rows; 100 weeks = 500 K rows, well within SQLite's comfort zone). The eviction job is a CLI target `make prune-runs` (same opt-in shape as `prune-candidates`) plus a startup-time check that warns (not deletes) if rows older than the limit exist. Auto-deletion at startup was rejected — surprising the user with vanished history is the worst kind of footgun.

**`source_snapshots` retention:** governed by candidate references via FK. A separate `make prune-snapshots` target deletes rows with no inbound FK (`WHERE NOT EXISTS (SELECT 1 FROM claude_candidates WHERE source_snapshot_hash = source_snapshots.snapshot_hash)`). Same opt-in shape.

### Integrity (the invariants and where they're enforced)

| Invariant | Enforcement |
|---|---|
| `sentence_id` matches `mat-{chapter}-{ordinal_in_chapter}` | App-level (segmenter); CHECK ensures chapter ∈ [1,28], ordinal ≥ 1; `UNIQUE (chapter, ordinal_in_chapter)` enforces consistency |
| Verse range is well-formed (start ≤ end) | DB CHECK on `sentences` |
| `starts/ends_at_verse_boundary` ∈ {0, 1} | DB CHECK |
| Words within a sentence are dense `1..word_count` | App-level (segmenter); `CHECK (word_count >= 1)` + `(sentence_id, ordinal)` PK; integrity test asserts `MAX(ordinal) = word_count` per sentence |
| Word byte offsets are within `byte_size` of the sentence | App-level (segmenter); test asserts |
| Red-letter range bounds are valid (start ≤ end in canonical order) | App-level (range editor service); rejected at API time before insert |
| Red-letter overlay chain has no cycles | App-level (range editor service); test asserts a SQL recursive CTE walk converges |
| `claude_candidates.source_snapshot_hash` references a real snapshot | DB FK |
| `source_snapshot.snapshot_hash` matches SHA-256 of `payload_json` | App-level (Claude client at insert time); integrity test recomputes hashes and asserts no drift |
| `ranking_entries` discriminated union (one of translation_name vs claude_candidate_id) | DB CHECK |
| `tie_break_decisions` discriminated union | DB CHECK |
| `generation_run_items` (status, candidate_id, error_code) consistency | DB CHECK |
| `generation_run_items.ordinal` is dense `1..N` per run | App-level (runner at insert time); integrity test asserts `MAX(ordinal) = COUNT(*)` per run |
| `generation_runs.status` lifecycle transitions are legal (`pending → running → {completed,failed,cancelled,interrupted}`; `running → interrupted` only via startup recovery; no other transitions) | App-level (runner at every `UPDATE generation_runs SET status=?`); each `UPDATE` names the expected old status in the `WHERE` clause as a guard (e.g. `WHERE status IN ('pending','running')` for cancel; `WHERE status='running'` for finish). Reliability §Generation runner durability pins the per-transition guards; the integrity test `test_run_status_lifecycle_legal_transitions_only` (in `test_integrity.py`) drives every state pair and asserts illegal transitions are rejected (`UPDATE` affects 0 rows). DB-level CHECK is *not* sufficient because CHECK can only see the new value, not the old; a state-machine guard requires the named-old-status `WHERE` clause. |
| OCC `version` increments monotonically | App-level via `BEGIN IMMEDIATE` + `UPDATE … WHERE version=?` (Reliability §OCC) |
| No partial fixture import | Single transaction (Reliability §Importer atomicity) |
| Foreign keys enforced | `PRAGMA foreign_keys = ON` at every connection (test asserts) |

**No SQLite triggers in v1.** Considered a `BEFORE INSERT` trigger on `claude_candidates` to verify the snapshot hash; rejected because (a) the hash check requires Python's canonicalization rule, not SQL; (b) triggers are invisible in the schema dump and the lock-down list above is more legible.

### Test-only admin hook fixture isolation

Security §Test-only admin hooks pins the routes, schemas, and env-gate. **This subsection pins the fixture-isolation pattern** — how the test-only mutations are reverted so a crashed eval doesn't dirty the workspace, and how idempotent rebuild stays honest.

**The invariant:** after every eval seed completes (or crashes), `fixtures/` matches the on-disk hashes pinned in `fixtures/manifest.json`, and the overlay tables contain only rows authored by the production write path (no test-only rows leak into a subsequent `make import` or `bs/happy/` eval).

**Mechanism:**

- **Immutable manifest backup (process-startup snapshot):** when the FastAPI app starts in `env=development`, it computes the SHA-256 of every file named in `fixtures/manifest.json` and stores `(path, sha256, byte_count, content_bytes)` in an in-memory dictionary `_FIXTURE_BASELINE` (held on the app state, not on disk — restart re-reads). The `mutate-fixture-byte` route captures the byte it overwrites; `restore-fixture-byte` writes the captured byte back; `reset-fixture-state` walks `_FIXTURE_BASELINE` and re-writes every fixture whose current SHA-256 doesn't match the baseline. **Why an in-memory cache, not a `fixtures.bak/` directory:** disk-resident backups would themselves need cleanup and could drift from `manifest.json`; the in-memory cache is rebuilt at every start and is automatically correct.
- **Test-only overlay marker:** every overlay row inserted via `POST /api/v1/admin/test-only/seed-overlay` carries `origin='manual'` (the production-allowed origin) plus a sentinel pattern in `created_at` — an ISO-8601 timestamp where the millisecond component is exactly `999` (e.g. `2026-05-03T12:34:56.999Z`). Production write paths use `datetime.now(UTC).isoformat()` which has microsecond precision; the chance of a real overlay landing on `.999` exactly is 1-in-1000 and is the trade-off accepted for not adding a `test_only_marker INT` column to the production schema. `reset-fixture-state` deletes overlay rows matching the sentinel pattern only. The test-only marker convention is documented in the route handler so a future developer doesn't accidentally rely on `.999` being significant in production data.
- **Cleanup hook is mandatory in eval teardown:** every eval seed in Reliability §Eval coverage that uses test-only hooks (`bs/fixture/`, `bs/import/`) calls `POST /api/v1/admin/test-only/reset-fixture-state { confirm: true }` in its teardown step. The seed framework's "always-run-on-failure" hook calls it even when the seed body raises. Without the teardown a crashed eval leaves a flipped fixture byte and the next `make import` hits `fixture_hash_mismatch`.
- **Idempotent rebuild stays honest:** the `test_running_import_twice_produces_byte_identical_rows` test (Data §Idempotent import tests) is run as part of `make test` after every eval suite, so a missed cleanup that left a test-only overlay would surface as a hash drift (the second import's overlay-table hash would include the test-only row left over from a prior run).
- **Hard guard on the production import path:** `POST /api/v1/admin/reimport` is **not** affected by test-only state — overlays are preserved across imports per Hard decision #7, including any test-only-marker overlays that survive a crashed cleanup. The `reset-fixture-state` route is the only path that deletes those rows; an honest production reimport never silently drops them.

The contract surface is thin: the eval seed's preconditions and teardown are documented in Reliability §Eval coverage; the routes/schemas are documented in Security §Test-only admin hooks; the isolation pattern (this subsection) is what links them and what the integrity test guards.

### `data/backups/` directory creation

The backup directory `data/backups/` is created by the **scheduler bootstrap** (Reliability's territory — the `apscheduler` / `asyncio.create_task` startup path that schedules the periodic `VACUUM INTO`). On startup, the scheduler ensures `data/backups/` exists via `Path("data/backups").mkdir(parents=True, exist_ok=True)` **before** scheduling either the periodic backup task or the pre-import backup hook; otherwise the first `VACUUM INTO 'data/backups/...'` would fail with `unable to open database file` and the run-detail UI's first reimport would surface a confusing error.

Data's responsibility here is the durability contract — the directory is gitignored (`.gitignore` covers `data/`) and the snapshot files inside it are listed in the Retention table above. Reliability owns the *creation* path so that the scheduler-bootstrap and the backup-emitting code agree on the path being available before any backup is attempted.

### Data-engineering tests (engineer-builder ships with the slice)

Coordinated with engineer-reliability's test list (Reliability §Test coverage). My tests cover schema/migration/import/integrity gaps that Reliability's runner/SSE/OCC tests don't touch. **No duplication** — where Reliability already tests the OCC mechanic at the runner level, I test the schema-level CHECK constraints and FK pragma; where Reliability tests SSE replay, I test the underlying `generation_run_items` integrity.

**Schema and migration:**

- `api/tests/test_schema.py::test_alembic_upgrade_creates_all_tables` — run `0001_init` against an empty DB; assert every named table exists with the columns and CHECK constraints documented above (introspect via `PRAGMA table_info`).
- `api/tests/test_schema.py::test_alembic_downgrade_drops_all_tables` — upgrade then downgrade; assert tables don't exist; assert `alembic_version` is at the empty state.
- `api/tests/test_schema.py::test_foreign_keys_pragma_enabled` — open a connection, assert `PRAGMA foreign_keys` returns 1.
- `api/tests/test_schema.py::test_check_constraints_reject_invalid_inserts` — for every CHECK constraint above, attempt an `INSERT` that violates it and assert the right `IntegrityError`. (Generated by a parametrised fixture.)

**Idempotent import:**

- `api/tests/test_import_idempotent.py::test_running_import_twice_produces_byte_identical_rows` — run import, hash the contents of fixture-derived tables (e.g. `SELECT GROUP_CONCAT(text_sblgnt) FROM sentences ORDER BY sentence_id`), run again, assert hash unchanged.
- `api/tests/test_import_idempotent.py::test_overlay_tables_unchanged_across_import` — seed overlays, candidates, rankings, run import, assert rows survive byte-identical (compare row hashes pre/post). Complements Reliability's `test_import_preserves_overlay_tables` by hashing rather than counting.
- `api/tests/test_import_idempotent.py::test_truncate_inserts_match_fixture_byte_for_byte` — for every fixture file, recompute the expected row contents from the file directly (NFC-normalise) and assert SQLite contains them verbatim.
- `api/tests/test_import_idempotent.py::test_fixture_version_row_updated` — run import, assert `fixture_version` row exists with `manifest_hash` matching `SHA-256(fixtures/manifest.json)`.
- `api/tests/test_import_idempotent.py::test_fixture_checksum_mismatch_aborts_with_zero_writes` — flip a fixture byte, run import, assert `fixture_hash_mismatch` and zero rows in source-text tables (complements Security #11 which only counts).

**OCC concurrent-write conflict (schema-level, complements Reliability's runtime tests):**

- `api/tests/test_occ_schema.py::test_version_column_check_rejects_zero` — `INSERT INTO rankings (..., version) VALUES (..., 0)` must fail the `CHECK (version >= 1)`.
- `api/tests/test_occ_schema.py::test_overlay_chain_no_orphan_parent` — insert overlay with non-existent `parent_overlay_id`, assert FK error.
- `api/tests/test_occ_schema.py::test_overlay_rejected_requires_null_bounds` — try `(rejected=1, start_sentence_id='mat-5-3')`; assert CHECK fails.

**Content-addressed snapshot dedup:**

- `api/tests/test_snapshot_dedup.py::test_same_payload_dedupes_to_one_row` — canonicalise the same payload twice, INSERT OR IGNORE both, assert exactly one row in `source_snapshots`.
- `api/tests/test_snapshot_dedup.py::test_canonicalisation_byte_identical` — build a payload with unsorted keys, ASCII-escaped Greek, and non-NFC text; canonicalise; assert the result equals a hand-computed reference byte-string.
- `api/tests/test_snapshot_dedup.py::test_floats_in_payload_raise` — try to canonicalise a payload containing a float; assert `ValueError` (per `allow_nan=False` path; integers-only rule).
- `api/tests/test_snapshot_dedup.py::test_snapshot_hash_matches_sha256_of_payload_json` — for every row in `source_snapshots`, recompute SHA-256 of `payload_json`; assert match.

**OrphanSummary correctness:**

- `api/tests/test_orphans.py::test_orphan_summary_classifies_remapped_sentence` — current DB has candidate for `mat-5-3` with text `T`; new fixtures have `mat-5-3` with text `T'` AND `mat-5-4` with text `T` (i.e. the old sentence shifted ordinals). Assert orphan reason is `sentence_id_remapped` with `suggested_remap_sentence_id='mat-5-4'`.
- `api/tests/test_orphans.py::test_orphan_summary_classifies_text_changed` — same `sentence_id`, different `text_sblgnt`. Assert reason `sentence_text_changed`.
- `api/tests/test_orphans.py::test_orphan_summary_classifies_removed` — sentence_id missing from new and no text/verse match. Assert reason `sentence_removed`, suggested_remap is null.
- `api/tests/test_orphans.py::test_orphan_summary_total_counts_three_categories` — seed N candidates, M rankings, K overlays as orphans; assert `total = N + M + K`.
- `api/tests/test_orphans.py::test_orphan_summary_ranking_attaches_version_and_counts` — orphan ranking has 4 entries, has notes, has tie-break; assert summary's `ranked_candidate_count=4`, `has_notes=true`, `has_tie_break=true`, `version=<actual>`.
- `api/tests/test_orphans.py::test_pre_flight_runs_against_live_db_read_only` — open a side connection mid-pre-flight, assert no source-table mutations are visible.

**Rankings + notes shared-version semantics:**

- `api/tests/test_ranking_atomic.py::test_notes_only_edit_increments_version` — write a ranking, save notes via `UPDATE rankings SET notes=?, version=version+1`, assert version went 1→2 and entries are unchanged.
- `api/tests/test_ranking_atomic.py::test_concurrent_notes_and_rank_edit_one_409s` — complements Reliability's `test_ranking_atomic_save_rank_list_ties_notes_one_version` by asserting the **schema-level** behaviour (the CHECK constraints fire correctly when the conflict resolves; the rolled-back transaction left no partial state).
- `api/tests/test_ranking_atomic.py::test_ranking_entries_cascade_on_rankings_delete` — delete a `rankings` row directly, assert `ranking_entries` rows are gone.
- `api/tests/test_ranking_atomic.py::test_ranking_entry_discriminated_union_check` — try to insert with both `translation_name` and `claude_candidate_id` set; assert CHECK fails.

**Hidden_combos OR predicate:**

- `api/tests/test_hidden_combos.py::test_per_row_hidden_bool_hides_candidate` — set `claude_candidates.hidden_bool=1`, query effective-hide; assert true.
- `api/tests/test_hidden_combos.py::test_combo_predicate_hides_matching_candidate` — insert into `hidden_combos`, query effective-hide for a candidate matching the four-tuple; assert true.
- `api/tests/test_hidden_combos.py::test_combo_predicate_hides_future_regenerated_candidate` — hide a combo, then `INSERT` a new `claude_candidates` row with the same four-tuple, assert effectively-hidden.
- `api/tests/test_hidden_combos.py::test_unhide_then_rehide_starts_at_version_1` — insert (v=1), delete, insert (v=1 again); assert this is fine for the OCC contract (the sentinel "no row" state handles it).
- `api/tests/test_hidden_combos.py::test_has_claude_candidates_filter_ignores_hidden` — Designer Flow 5 #3: rank-queue filter counts hidden Claude candidates as present; assert the SQL EXISTS query in the queue endpoint joins `claude_candidates` directly without filtering on hide predicates.

**Fixture-checksum mismatch:**

- See `test_fixture_checksum_mismatch_aborts_with_zero_writes` above (complements Security #11 by asserting *zero rows*, not just non-zero exit).

**BuildStaticCoverage denominator:**

- `api/tests/test_coverage.py::test_denominator_is_effective_red_letter_set` — seed 5 source ranges, 1 rejected via overlay, 2 manual overlays; assert `total = 5 - 1 + 2 = 6`. (Exact arithmetic depends on sentence counts in the ranges; test uses fixtures.)
- `api/tests/test_coverage.py::test_ch5_total_filters_to_chapter_5` — seed ranges across multiple chapters; assert `ch5_total` is exactly chapter-5 effective red-letter sentences.
- `api/tests/test_coverage.py::test_ranked_excludes_hidden_top_candidates` — rank a sentence with the rank-1 candidate's combo hidden; assert the sentence does NOT count toward `ranked` (would render as `— unranked —` per Designer Flow 6 step 1).
- `api/tests/test_coverage.py::test_ranked_includes_translation_top` — rank with a translation card at rank 1 (translations have no hide predicate); assert it counts toward `ranked`.
- `api/tests/test_coverage.py::test_coverage_query_under_50ms_for_full_matthew` — populate full Matthew's worth of ranking data, time the query, assert <50 ms.

**Misc integrity:**

- `api/tests/test_integrity.py::test_word_ordinal_density_per_sentence` — for every sentence, assert `MAX(words.ordinal) = sentences.word_count`.
- `api/tests/test_integrity.py::test_run_item_ordinal_density_per_run` — for every run, assert `(MAX(ordinal), COUNT(*)) = (N, N)`.
- `api/tests/test_integrity.py::test_overlay_chain_acyclic` — recursive CTE walk; assert no cycles.
- `api/tests/test_integrity.py::test_estimated_cost_aggregate_uses_integer_arithmetic` — seed 1000 runs with `estimated_cost_usd_x10000=1`; assert `SUM` is exactly 1000 (no float drift).
- `api/tests/test_integrity.py::test_payload_json_is_valid_canonical_json` — for every `source_snapshots` row, parse `payload_json`, re-canonicalise, assert byte-identical to original.
- `api/tests/test_integrity.py::test_run_status_lifecycle_legal_transitions_only` — drive every `(old_status, new_status)` pair through the runner's update path; assert legal pairs (`pending→running`, `running→completed`, `running→failed`, `running→cancelled`, `pending→cancelled`, `running→interrupted` (startup-only)) succeed and every other pair leaves the row unchanged (`UPDATE` affects 0 rows because the named-old-status `WHERE` predicate misses). **Invariant: status lifecycle (Data §Integrity).**

**Eval-seed entry (one new seed, complements Reliability's seeds):**

- `~/github/pagehub-io/platform/evals/seeds/bible_study_idempotent_import.py` — request prefix `bs/import/`. **Precondition: the eval harness must be running against an API process started with `env=development`** — the test-only admin hooks (`POST /api/v1/admin/test-only/seed-overlay`, `POST /api/v1/admin/test-only/reset-fixture-state`) defined in Security §Test-only admin hooks are only mounted when `settings.env == "development"`; in `staging`/`production` they return 404 (or, more strictly, do not exist in the OpenAPI surface). The seed asserts the env-gate by hitting `GET /api/v1/admin/test-only/seed-overlay` (without a body) and expecting a method-not-allowed response in development; if it returns 404, the env-gate is closed and the seed aborts with a clear "harness must run against env=development" message. Steps: `POST /admin/reimport` (capture `manifest_hash`); `POST /admin/reimport` again with no fixture changes (assert response's `manifest_hash` identical, `sentences_built` identical); `POST /api/v1/admin/test-only/seed-overlay` to seed an overlay row (capture `overlay_id`); `POST /admin/reimport` a third time; assert overlay still present in `GET /api/v1/red-letters`. **Teardown step (mandatory)**: `POST /api/v1/admin/test-only/reset-fixture-state { confirm: true }` to delete any test-only overlay rows and restore any byte-mutated fixtures (per Data §Test-only admin hook fixture isolation). Captures: `manifest_hash`, `overlay_id`. **Covers: idempotent import + overlay preservation across imports.**

### Open data concerns (need user decision)

- **`prune-candidates` / `prune-runs` / `prune-snapshots` defaults** — proposing default-disabled (manual CLI). If Gavin wants automatic eviction at some interval, both knobs become env-overridable like `BACKUP_INTERVAL_SECONDS`. Confirm.
- **`generation_runs` retention window** — proposed 24 months. If shorter, the daily-cost-cap aggregate window (24h) is unaffected; the `parent_run_id` chain may walk into evicted history (resolution: store a denormalised snapshot of the parent's metadata on the child run at create time). Lighter retention is fine but I'd default conservative.
- **Lexicographic sentence_id sort hazard** — flagging that anywhere we treat `sentence_id` as a sortable string we'll get wrong order. The service layer normalises by ordering on `(chapter, ordinal_in_chapter)`. If a future query writer adds `ORDER BY sentence_id` it'll silently misorder; the integrity-test suite should grep for that anti-pattern. (Recommend adding to the linter / code-review checklist.)
- **`payload_json` storage size** — content-addressed dedup keeps the `source_snapshots` table small, but a single `payload_json` for `BOTH_GREEK + BIB` over a long Sermon-on-the-Mount sentence could push 100 KB. SQLite handles this fine; just flagging that `byte_size` is recorded on the row for visibility and a future `make audit-snapshots` could surface anomalies.

AGREE: yes

## Architect — Reconciliation

This is the second-round reconciliation after engineer-data flagged 2 IMPORTANT items and 3 NITs against the Phase 2b round 1 PLAN. Each engineer's section was edited directly; engineers retain authorship. One bullet per change:

### Resolved

- **Test-only admin hooks home** (data IMPORTANT 1): split across all three sections by responsibility. **Routes, request/response schemas, and the env-gate mechanism** (`settings.env == "development"` allowlist; routes not registered in staging/production) are now pinned in Security §Test-only admin hooks, including the four routes (`mutate-fixture-byte`, `restore-fixture-byte`, `seed-overlay`, `reset-fixture-state`) and their Pydantic shapes — env-gate is a security control, so it lives in the security section. **Fixture-isolation pattern** (in-memory `_FIXTURE_BASELINE` cache, `.999` millisecond test-only marker, idempotent rebuild integration with `test_running_import_twice_produces_byte_identical_rows`) is now pinned in Data §Test-only admin hook fixture isolation — fixture integrity is Data's invariant. **Eval invocation patterns** (preconditions, captures, mandatory teardown step) are now pinned per-seed in Reliability §Eval coverage (`bs/fixture/`) and Data §Eval-seed entry (`bs/import/`). All three sections cross-reference each other.
- **`disk_full` typed `error_code`** (data IMPORTANT 2): added `disk_full` to the `generation_run_items.error_code` CHECK enum in Data's schema. Reliability's "3 consecutive sentences fail with disk_full" rule was rewritten to count via typed `error_code='disk_full'` rather than string-matching `error_message="disk_full"` — string-matching on a free-text field that's also subject to `MAX_ERROR_MESSAGE_BYTES` truncation was the fragility the data engineer correctly flagged. The unit test `test_three_consecutive_disk_full_fails_run` was updated to assert the typed code. Data's schema now includes a paragraph explaining why `disk_full` is promoted to a first-class enum value.
- **`.gitignore` patterns for build artifacts** (security NIT): Security's data-exposure subsection now explicitly lists `dist/`, `dist.tmp/`, and `dist.bak/` as gitignored, matching the directories Reliability §Static-site atomic swap creates. Without `dist.tmp/` and `dist.bak/` in `.gitignore`, a mid-build crash could leave a partial bundle that gets reflexively `git add .`'d.
- **Env-gate language on `bs/import/` and `bs/fixture/` seeds** (security NIT): both eval seeds now explicitly state the precondition (`env=development`) and the env-gate-assertion step (`GET` against the test-only route to confirm it returns method-not-allowed in dev rather than 404 — 404 means the env-gate is closed and the seed aborts with a clear error). Both seeds also now have a mandatory teardown step calling `reset-fixture-state` so a crashed eval doesn't leave the workspace dirty.
- **`/admin/diag` `last_backup_path` redaction** (security NIT): renamed to `last_backup_filename` and pinned to be the basename only (e.g. `bible_study-2026-05-03T12-34-56Z.db`), never the absolute path. The diag response is the kind of surface that gets pasted into bug reports / screenshots, and exposing absolute paths would leak the user's home directory and project layout.
- **CSRF coverage parametrised over OpenAPI** (reliability NIT): added a new test `test_csrf_coverage_parametrised_over_openapi` to Security's test list. The test introspects `app.openapi()` at runtime and parametrises over every `POST`/`PUT`/`PATCH`/`DELETE` route, asserting `403 origin_required` and `403 x_requested_by_required` for each — adding a future write route automatically adds a CSRF test case. Test-only admin routes are excluded from the parametrisation (they intentionally bypass CSRF; the env-gate is the security control).
- **`make prune-candidates` SQL** (reliability NIT): the SQL was rewritten in Data §`claude_candidates` retention rationale to scan four exclusion conditions: ranking_entries reference, tie_break_decisions winner reference, **tie_break_decisions.tied_against_json scan via `json_each`** (the missing scan the engineer flagged), and **non-terminal `generation_run_items.candidate_id` reference** (the in-flight exclusion the engineer flagged). The SQL now uses `BEGIN IMMEDIATE` and is documented as defence-in-depth — pruning a "loser" candidate from a tie-break would otherwise leave a dangling reference inside `tied_against_json` and the GSV's `tie-broken` provenance would surface candidate IDs that no longer exist.
- **`red_letter_overlays` chain-leaf race test named explicitly** (reliability NIT): added `test_red_letter_overlay_chain_leaf_race_one_winner` to the OCC test list, distinct from the generic `test_overlay_occ`. The new test names the chain-shaped OCC mechanic explicitly: two transactions read the same head, both attempt to INSERT a new leaf with the same `parent_overlay_id`, the "no other child" predicate ensures exactly one INSERT succeeds and the other returns `409 stale_version`. The standard `WHERE version=?` guard on a single row is not sufficient for the overlay chain shape; the new test makes that load-bearing guard explicit.
- **Backup integrity hard cap** (reliability NIT): added `BACKUP_HARD_CAP` (default 32, env-overridable) to Reliability §Backups. On persistent integrity-check failure, the cleanup deletes the oldest snapshot regardless of integrity check outcome once disk count hits the cap — bounds disk growth in the pathological "integrity failing for a day" scenario. Cap is set above `BACKUP_RETENTION_COUNT` (32 > 24) so steady-state never engages it; a `event=backup.hard_cap_eviction` log line fires when the fail-safe engages so Gavin can see the symptom.
- **PID file in Retention table** (data NIT): added a row for `data/bible_study.pid` to Data's Retention table — written at FastAPI startup, removed at clean shutdown, overridable via `FORCE=1` on `make restore`.
- **`generation_runs.status` lifecycle in Integrity table** (data NIT): added a row to Data's Integrity table covering the legal transitions and the named-old-status `WHERE`-clause guards each `UPDATE` uses. Also added a corresponding integrity test `test_run_status_lifecycle_legal_transitions_only`. Notably called out that DB-level CHECK is *not* sufficient for state-machine enforcement (CHECK can only see the new value, not the old) — the named-old-status `WHERE` clause is the actual guard.
- **`data/backups/` directory creation pinned** (data NIT): pinned to the scheduler bootstrap (Reliability's territory) — it runs `Path("data/backups").mkdir(parents=True, exist_ok=True)` before scheduling either the periodic backup task or the pre-import hook. Reliability §Backups now references the directory creation explicitly; Data §`data/backups/` directory creation cross-references back. Without this, the first `VACUUM INTO 'data/backups/...'` after a fresh checkout fails with `unable to open database file`.

### Open (need user input)

- None. All conflicts and NITs from round 1 are resolved within the engineer-trio's existing scope. The four open concerns each engineer flagged earlier (cost-cap defaults, pre-commit hook scope, `<AuthOnly>` strip-out implementation, daily cap window, CSP header policy, prune defaults, retention window, lexicographic sentence_id sort hazard, payload_json storage size) remain open as written; no new architectural conflicts were surfaced in this round.

AGREE: yes
