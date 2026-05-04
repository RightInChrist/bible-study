/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite config for the Bible-study SPA.
 *
 * Two build modes share one component tree (Architect §Web UI Components):
 *   - `dev/local mode` (default): proxies `/api` to the FastAPI server on
 *     127.0.0.1:8000. Talks read+write.
 *   - `static mode` (`VITE_STATIC=true`): authoring affordances are
 *     stripped via `<AuthOnly>` returning null and the `__STATIC__`
 *     define-constant. The actual static export pipeline ships in a later
 *     slice; this slice puts the wrapper + define in place so a future
 *     `npm run build:static` can produce an authoring-free bundle.
 *
 * Hard decision #14: authoring code is stripped at Vite build time, not
 * merely hidden. The combination of `__STATIC__` + the `<AuthOnly>`
 * pattern is what makes that strip-out a real tree-shake.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const isStatic = env.VITE_STATIC === "true";
  const apiProxyTarget = env.VITE_API_PROXY ?? "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    define: {
      __STATIC__: JSON.stringify(isStatic),
    },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: false,
        },
      },
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./vitest.setup.ts"],
      include: ["__tests__/**/*.test.{ts,tsx}", "src/**/*.test.{ts,tsx}"],
    },
  };
});
