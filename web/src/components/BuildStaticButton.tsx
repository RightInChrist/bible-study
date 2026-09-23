/**
 * Build-static button for the GSV page (Designer Flow 6 step 5).
 *
 * Wraps `useBuildStatic`. In-flight: button disabled, "Building…" label.
 * On success: panel showing `files_written`, coverage, `took_ms`,
 * `dist_path`. On `400 build_guard_failed`: error block listing the
 * offending paths (the load-bearing security signal). On other errors:
 * generic error block.
 *
 * The whole component is wrapped in `<AuthOnly>` so the static build
 * tree-shakes it. Slice 5 left this disabled with a tooltip; Slice 6
 * makes it active.
 */
import { useState } from "react";

import type { BuildStaticResponse } from "../api/types";
import { ApiError } from "../api/client";
import { useBuildStatic } from "../api/hooks";
import { AuthOnly } from "./AuthOnly";

interface BuildGuardHits {
  hits: Record<string, string[]>;
}

function isBuildGuardFailure(err: unknown): err is ApiError {
  if (!(err instanceof ApiError)) return false;
  return err.status === 400 && err.body?.code === "build_guard_failed";
}

function BuildStaticButtonInner() {
  const buildStatic = useBuildStatic();
  const [success, setSuccess] = useState<BuildStaticResponse | null>(null);
  const [errorBlock, setErrorBlock] = useState<string | null>(null);
  const [guardHits, setGuardHits] = useState<BuildGuardHits | null>(null);

  const onClick = (): void => {
    setSuccess(null);
    setErrorBlock(null);
    setGuardHits(null);
    buildStatic.mutate(
      { include_unranked_placeholders: true },
      {
        onSuccess: (data) => setSuccess(data),
        onError: (err: Error) => {
          if (isBuildGuardFailure(err)) {
            const hits =
              ((err.body?.details ?? {}) as Record<string, unknown>).hits ?? {};
            setGuardHits({ hits: hits as Record<string, string[]> });
            return;
          }
          if (err instanceof ApiError && err.body) {
            setErrorBlock(`${err.body.code}: ${err.body.message}`);
            return;
          }
          setErrorBlock(err.message);
        },
      },
    );
  };

  return (
    <div className="gsv-page__build-static" data-testid="build-static-block">
      <button
        type="button"
        className="gsv-page__build-static-btn"
        onClick={onClick}
        disabled={buildStatic.isPending}
        data-testid="gsv-build-static"
      >
        {buildStatic.isPending ? "Building…" : "Build static site"}
      </button>

      {success !== null ? (
        <div className="gsv-build-success" data-testid="gsv-build-success">
          <p>
            Built <code>{success.dist_path}</code> — {success.files_written}{" "}
            files in {success.took_ms} ms.
          </p>
          <p>
            Coverage: {success.coverage.ranked} / {success.coverage.total}{" "}
            red-letter sentences ranked (Matthew 5: {success.coverage.ch5_ranked}{" "}
            / {success.coverage.ch5_total}).
          </p>
          <p>
            <a
              href={`file://${success.dist_path}/index.html`}
              target="_blank"
              rel="noreferrer"
              data-testid="gsv-build-dist-link"
            >
              Open dist/index.html
            </a>
          </p>
        </div>
      ) : null}

      {guardHits !== null ? (
        <div className="error-block" role="alert" data-testid="gsv-build-guard-error">
          <strong>build_guard_failed</strong>
          <p>
            Build aborted. The static tree contained forbidden content; the
            build was discarded and <code>dist/</code> was left untouched.
          </p>
          <ul>
            {Object.entries(guardHits.hits).map(([pattern, files]) => (
              <li key={pattern}>
                <code>{pattern}</code> in: {files.join(", ")}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {errorBlock !== null ? (
        <div className="error-block" role="alert" data-testid="gsv-build-error">
          {errorBlock}
        </div>
      ) : null}
    </div>
  );
}

export function BuildStaticButton() {
  return (
    <AuthOnly>
      <BuildStaticButtonInner />
    </AuthOnly>
  );
}
