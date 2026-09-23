# bible-study

Gavin's personal Bible study tool — Matthew, sentence-aligned. Local-only,
single-user. See `SPEC.md` and `PLAN.md` for the full design.

## Slice 2 status

Slice 1 shipped the read path end-to-end on a Beatitudes-only stub.
Slice 2 lifts that stub to **full Matthew (28 chapters)**:

- Pinned, hash-verified fixtures for the entire Gospel of Matthew across
  SBLGNT (Faithlife/SBLGNT GitHub mirror), Byzantine (byztxt
  csv-unicode/no-parsing — verse-keyed only in v1, lowercase unaccented),
  BSB / BLB (bereanbible.com / literalbible.com public-domain text
  downloads), BIB (Berean Interlinear, extracted from the BSB Translation
  Tables TSV), and WEB (eBible.org USFM).
- Hand-curated red-letter ranges for the **Sermon on the Mount
  (Matt 5:3–7:27)** and **Matt 11:28–30 ("Come to Me")**. The rest of
  Matthew's red-letter coverage is deferred to the range-editor UI
  slice — Gavin will author it through the UI, not by hand-editing JSON.
  See `TODO.md`.
- Alembic 0001_init migration creating every table named in `PLAN.md` §Data.
- Idempotent fixture importer (`make import`) — atomic-or-nothing, byte-
  identical re-runs.
- FastAPI app bound to `127.0.0.1` only, with security middleware (Host
  allowlist + Origin / X-Requested-By for writes).
- `GET /api/v1/health`, `GET /api/v1/admin/fixture-status`,
  `GET /api/v1/sentences?chapter=N`,
  `GET /api/v1/sentences/{sentence_id}/parallel`.
- pytest suite covering the slice's invariants.
- Eval seed (`bible_study_idempotent_import.py`) registered with the local
  evals harness at `http://localhost:4002`.

Deferred to follow-up slices: write endpoints (rankings / overlays /
generation runs), Claude client, SSE replay, static-site builder,
test-only admin hooks, frontend.

## Quick start

```bash
make install               # build .venv + install deps
make migrate               # apply alembic head (currently 0002_runs_no_dollar_cost)
make import                # hash-verify fixtures and load into SQLite
make dev                   # run the FastAPI server on http://127.0.0.1:8000
make test                  # run the pytest suite
```

### Claude Code prerequisite (Slice 3a-redo)

Translation generation spawns the `claude` CLI in a git worktree per
sentence (CLAUDE.md §Generation mechanism). For runs to work:

```bash
which claude               # must resolve (Claude Code must be installed)
claude --version           # must succeed (subscription must be logged in)
```

The bible-study process never sees an API key — it relies on whatever
Claude Code is already authenticated as on the local machine. The
runner sanity-checks `claude --version` at run-creation time and rejects
with `claude_cli_unavailable` if the CLI isn't reachable.

## Repository layout

```
api/                    FastAPI app (routes, services, schemas)
  admin/                  health + fixture-status routes
  db/                     connection + PRAGMA helpers
  importer/               manifest validation, segmenter, atomic importer
  migrations/             Alembic config + 0001_init
  sentences/              read-path routes, services, Pydantic schemas
  tests/                  pytest suite
fixtures/               version-pinned source texts + manifest.json
  _upstream/              raw upstream downloads (gitignored; the
                          manifest pins their SHA-256s so a fresh
                          download is byte-verified before the
                          normalizer runs)
scripts/                CLI entry points
  import_fixtures.py      make import → scripts.import_fixtures
  normalize_sblgnt.py     SBLGNT Matt.txt → fixtures/sblgnt/matthew.txt
  normalize_byzantine.py  Byzantine MAT.csv → fixtures/byzantine/matthew.txt
  normalize_berean_english.py  bsb.txt / blb.txt → fixtures/{bsb,blb}/matthew.txt
  normalize_web.py        WEB Matt USFM → fixtures/web/matthew.txt
  normalize_bib.py        bsb_tables.tsv → fixtures/bib/matthew.tsv
data/                   SQLite + WAL files (gitignored)
```

## Refreshing fixtures from upstream

The `fixtures/_upstream/` directory is gitignored. To rebuild it from
the manifest's pinned upstream sources:

```bash
mkdir -p fixtures/_upstream
curl -sL "https://raw.githubusercontent.com/Faithlife/SBLGNT/master/data/sblgnt/text/Matt.txt" \
  -o fixtures/_upstream/sblgnt-Matt.txt
curl -sL "https://raw.githubusercontent.com/byztxt/byzantine-majority-text/master/csv-unicode/strongs/no-parsing/MAT.csv" \
  -o fixtures/_upstream/byzantine-MAT.csv
curl -sL "https://bereanbible.com/bsb.txt"          -o fixtures/_upstream/bsb.txt
curl -sL "https://literalbible.com/blb.txt"         -o fixtures/_upstream/blb.txt
curl -sL "https://bereanbible.com/bsb_tables.tsv"   -o fixtures/_upstream/bsb_tables.tsv
curl -sL "https://ebible.org/Scriptures/eng-web_usfm.zip" -o /tmp/web.zip \
  && unzip -p /tmp/web.zip 70-MATeng-web.usfm > fixtures/_upstream/web-Matt.usfm

.venv/bin/python -m scripts.normalize_sblgnt
.venv/bin/python -m scripts.normalize_byzantine
.venv/bin/python -m scripts.normalize_berean_english --input fixtures/_upstream/bsb.txt --output fixtures/bsb/matthew.txt
.venv/bin/python -m scripts.normalize_berean_english --input fixtures/_upstream/blb.txt --output fixtures/blb/matthew.txt
.venv/bin/python -m scripts.normalize_web
.venv/bin/python -m scripts.normalize_bib
```

If any upstream file's SHA-256 has changed since the manifest was last
written, the importer aborts at preflight (`fixture_hash_mismatch`).
Update `fixtures/manifest.json` to re-pin the new upstream hashes only
after manually diffing the upstream changes.

## Conventions (from `~/.claude/CLAUDE.md`)

- Every FastAPI route has `response_model=...` and Pydantic request bodies.
- Schemas live in `<feature>/schemas.py` next to the routes.
- Type hints on every Python signature.
- Server refuses to start if `BIND_HOST` is not loopback, unless
  `BIND_HOST_ALLOW_NON_LOCAL=1` is set explicitly.

## Eval seed

```bash
# Start the API via the launcher (settings validator + opt-in env var fire here;
# uvicorn directly with --host 0.0.0.0 would silently bypass the loopback guard):
BIND_HOST=0.0.0.0 BIND_HOST_ALLOW_NON_LOCAL=1 \
  EXTRA_ALLOWED_HOSTS=host.docker.internal \
  ENV=development \
  .venv/bin/python -m scripts.serve

# Seed the requests + collection in the local evals service:
.venv/bin/python ~/github/pagehub-io/platform/evals/seeds/bible_study_idempotent_import.py \
  --bible-study-url http://host.docker.internal:8000

# Trigger the run (collection_id printed by the seed script):
curl -X POST http://localhost:4002/v1/runs \
  -H 'content-type: application/json' \
  -d '{"factory_key":"bible_study","collection_id":"<id>"}'
```

Slice 2 verified: 5/5 evals pass against full-Matthew data
(`bs-import-{01..05}` collection).
