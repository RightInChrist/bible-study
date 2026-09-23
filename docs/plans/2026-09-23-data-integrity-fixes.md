# Data-integrity fixes: one red-letter truth, honest provenance, backups, safe reimport

**Status:** draft for spec review · **Base:** `slice/3a-7-baseline` (PR #1) · **Author date:** 2026-09-23

## 1. Problem

The review of PR #1 (comment on `4c076fa`) found four groups of defects. Each one corrupts Gavin's study data or misreports it, and none is caught by the tests:

1. **Several different "is this red letter?" answers.**
   - The reader uses the canonical effective-set CTE.
   - The GSV, generation bundles and the `all_unranked_red_letter` run scope each keep their own stale copy. These copies ignore manual overlays and treat any `reject` on a range as permanent, even after a restore.
   - Result: Sermon on the Mount rankings never reach the GSV, and chapter summaries were generated with no sentence-level candidates.
   - Findings: C2, C3.
2. **Bundle provenance doesn't match what the agent saw.** The context window attaches BSB English to neighbouring sentences whatever the source set is. So candidates labelled `BOTH_GREEK` were generated with English in view. Finding: C4.
3. **No backups.** The `BACKUP_*` settings are never read, and `make clean` deletes the only database. That database holds work that can't be rebuilt from fixtures. Finding: C13.
4. **Reimport can re-key, lose or re-point work.** Findings C11–C14:
   - A shifted sentence ID is treated as "text changed", and "keep" leaves the work on the wrong Greek.
   - Every disposition path fails with an FK error, yet the dispositions have already committed.
   - Source-range IDs are reassigned on every import, so overlays re-point to different ranges.

## 2. Premises (executed on a copy of `data/bible_study.db` at `4c076fa`)

| # | Claim | Evidence |
|---|---|---|
| P1 | Three red-letter implementations disagree | `effective_red_letter_set` → **535** sentences; `api/claude/sources.py::_red_letter_sentence_ids` → **3**; `api/gsv/service.py::_red_letter_sentence_ids` → **3**. Matthew 5: effective **68**, GSV **0**. |
| P2 | Root cause: "any reject = rejected forever" | Source range 1 chain: `(3,'reject',rejected=1,parent=None)`, `(5,'create',rejected=0,parent=3)`. The restore is a `create` child of the reject. The stale queries test `NOT EXISTS (… operation='reject')` instead of the chain head. |
| P3 | GSV drops rankings in Matthew 5 | `PUT /sentences/mat-5-5/ranking` (WEB first) → 200. `GET /gsv/5?format=json` then still gives `provenance: {kind: translation, name: BSB}` and `coverage.total_red_letter_sentences: 0`. |
| P4 | Summaries were built on nothing | All 4 `chapter_summaries` snapshots contain `"red_letter_candidates":[]`. |
| P5 | BSB leaks into Greek-only bundles | 365/365 candidates have `source_set_id=BOTH_GREEK`, and 365/365 of their snapshots contain `bsb_text`. The leak is by design: the `AdjacentSentence` docstring says "a readable English (BSB) line for pericope orientation". |
| P6 | No backup code exists | `grep -rn "VACUUM INTO\|backup_interval_seconds" api` returns only the settings declaration (`api/settings.py:66`). |
| P7 | Source ranges have no stable identity | `fixtures/red_letters/matthew.json` entries have no key field. The importer runs `DELETE FROM red_letter_source_ranges` and re-inserts with new rowids (`api/importer/runner.py:412`, `:483`). |
| P8 | Referencing tables | From `pragma_foreign_key_list`. **Sentence references:** `words`, `red_letter_source_ranges`, `red_letter_overlays` (start/end), `source_snapshots`, `claude_candidates`, `hidden_combos`, `rankings`, `tie_break_decisions`, `generation_run_items`. **Source-range references:** `red_letter_overlays.source_range_id`. **Candidate references:** `ranking_entries`, `tie_break_decisions`, `generation_run_items`. |
| P9 | Orphan resolution never succeeds | *Reviewer-executed, not re-run here.* Delete, keep and remap each raise `IntegrityError: FOREIGN KEY constraint failed`, and dispositions commit before the import (`api/admin/service.py:395`, `:506`). |
| P10 | Chapter bundles will exceed the argv limit once P1 is fixed | *Reviewer-executed.* The chapter 5 input with the correct red-letter set is 337,866 bytes. `_invoke_claude` passes the prompt as one argv argument, which gives `[Errno 7] Argument list too long`. |
| P11 | `claude -p` accepts the prompt on stdin at this size | `python3 -c "print('…' + 'x'*200000)" \| claude -p --model haiku` → exit 0 and a model response. The ~200 KB prompt was read from stdin. |

## 3. Decisions (owner, 2026-09-23)

- **D1 — context window is Greek-only for Greek-only source sets.** English appears in adjacent-sentence context only when the source set already includes that English text.
- **D2 — the 365 existing candidates stay, and their provenance shows what they actually saw.** Nothing is relabelled and nothing is regenerated. "What it saw" is derived from the stored snapshot, not stored separately.
- **D3 — the 4 existing chapter summaries stay visible**, flagged "drew on 0 candidates". Gavin regenerates whichever he wants from the UI after the fix.

## 4. Design

### A. One red-letter lookup (fixes C2, C3, and C10 on the same read path)

- New module `api/red_letter/effective.py`. It depends only on `sqlite3`, so the runner can import it without the import cycle `sources.py` works around. It holds three things:
  - `_EFFECTIVE_SET_SQL`, moved verbatim from `api/red_letter/service.py`;
  - `effective_red_letter_set(conn) -> set[str]`;
  - `sentence_ids_between(conn, start_id, end_id)`.
- Every "is red letter" question uses it: `red_letter/service.py`, `sentences/service.py`, `gsv/service.py` (the chapter GSV and coverage), `claude/sources.py` (the context-window flag and the chapter bundle), and `runs/runner.py` (`all_unranked_red_letter` = effective set minus ranked sentences).
- **The three private copies are deleted.** This is the structural fix: there is no second implementation left to drift.
- **C10** is on the same read path: `_synthesise_source_only_chain` builds `Overlay(version=0)`, which the schema (`ge=1`) rejects. The synthetic chain gets `version=1`, and this PR covers chapters with an unedited source range.
- **Release-sequencing coupling (P10):** A enlarges chapter bundles past the argv limit. So the same PR moves prompt delivery in `api/claude/worktree.py` from argv to **stdin** (`claude -p` reads the prompt from stdin). A must not land without it.

**Invariant:** for every sentence, the reader's `is_red_letter`, the GSV's `is_red_letter`, the per-sentence bundle's context flag, chapter-bundle membership and run-scope membership all agree.

### B. Bundle provenance (C4; implements D1, D2)

- **D1 (future bundles).** `_fetch_adjacent_sentences` takes the source set and includes English only when that source set contains it:
  - BSB context for `GREEK_PLUS_BSB` and `ENGLISH_ONLY_BSB`;
  - BLB context for `GREEK_PLUS_BLB`;
  - none for `SBLGNT_ONLY`, `BYZ_ONLY`, `BOTH_GREEK` and `GREEK_PLUS_BIB`.

  `AdjacentSentence.bsb_text` becomes optional and is renamed `english_text`, with an `english_source` field.
- **Bump the snapshot canon version.** Today it is the literal `"v1"`, hardcoded twice in `api/runs/runner.py` (`:1142`, `:1202`). It becomes one constant, `SNAPSHOT_CANON_VERSION = "v2"`, in `api/claude/sources.py`. The bundle shape changes, and `source_snapshots.source_snapshot_canon_version` records which shape produced each hash. Old snapshots stay valid under `v1`.
- **D2 (existing and future candidates).** A pure function `sources_seen(snapshot_payload) -> list[str]` derives what the agent actually saw from the stored snapshot's keys, e.g. `["SBLGNT", "Byzantine", "BSB (context)"]`. It is exposed as:
  - `sources_seen` on `CandidateResponse`, shown next to the source-set label in the reader, rank cards and GSV footnotes;
  - `sources_seen` in the GSV JSON provenance for Claude winners.

  There's no migration. The truth is already stored, so deriving it can't drift.
- **D3:** `ChapterSummaryResponse` gains `candidates_drawn_on: int`, derived from the summary's snapshot. The UI shows "drew on 0 candidates — regenerate?" when it is 0.

### C. Backups (C13 part 1). Lands before D; D requires it.

- New `api/db/backup.py`, with `backup_now(conn_path, reason: Literal["periodic", "pre-reimport", "manual"]) -> Path`:
  - runs `VACUUM INTO data/backups/bible_study-<UTC yyyymmddThhmmssZ>-<reason>.db`;
  - fsyncs the file;
  - then runs `PRAGMA integrity_check` on the new file and deletes it if the check fails.
- **Periodic backups:** a FastAPI lifespan task runs every `BACKUP_INTERVAL_SECONDS`. It skips when the DB and WAL mtimes haven't changed since the last backup.
- **Retention:**
  - keep the newest `BACKUP_RETENTION_COUNT` periodic backups, plus the newest one per day for the last 14 days;
  - `pre-reimport` and `manual` backups are never pruned automatically;
  - `BACKUP_HARD_CAP` applies to periodic backups only.
- **Failure mode:** if a backup fails, it is logged and retried at the next interval. A failed **pre-reimport** backup aborts the reimport.
- **Makefile:**
  - `make backup` writes a manual backup.
  - `make restore FILE=…` refuses while the API is up (checks `/api/v1/health`), keeps the current DB as `pre-restore`, then copies the file in.
  - `make clean` no longer touches `data/*.db`.
  - `make nuke-db CONFIRM=1` backs up first, then deletes.
- Backups live on the same disk. That protects against logical damage, not against losing the disk. Off-machine copies are a non-goal (§7).

### D. Safe reimport (C11, C12, C13 part 2, C14). Depends on C.

**D-i. Stable source-range identity.**
- Migration `0004` adds `range_key TEXT` plus a unique index to `red_letter_source_ranges`.
- Fixture entries gain a required `key` (`"sermon-on-the-mount"`, `"come-to-me"`).
- The importer **UPSERTs by key** and keeps `source_range_id`. It deletes a range only when its key is gone, and that range's overlays become orphans (new reason `source_range_removed`).
- The first import after the migration adopts existing rows by exact bounds match (`start/end_sentence_id` plus word offsets). An unmatched row counts as removed and goes through the orphan flow.

**D-ii. Sentence identity check.** For every existing `sentence_id` that has dependent work, the old normalized SBLGNT text is compared with the new text for that ID:
- equal → same sentence;
- a whitespace/punctuation-only difference → same sentence, and it is recorded in the response as `cosmetic_changes`;
- otherwise, look for the old text among the **new** sentences:
  - an exact match elsewhere → reason `sentence_id_remapped`, with a `suggested_target`;
  - no match → `sentence_text_changed` or `sentence_removed`.

This runs for **every** changed ID, not only IDs that vanished. That is the C11 fix.

**D-iii. Dispositions are per orphaned sentence:**
- `remap(to=<new id>)`: allowed only to a live new ID;
- `delete`.

There is **no `keep`** when the Greek changed. Work stays attached only to the Greek it was made against, and the pre-reimport backup preserves everything.

- Remap updates `sentence_id` in every referencing table listed in P8, and rewrites overlay bounds.
- Delete removes rows in child-first order: `ranking_entries` and `tie_break_decisions` → `rankings` → `hidden_combos` → `generation_run_items` (`candidate_id` set to NULL; the run history keeps the row) → `claude_candidates` → `source_snapshots` → overlays.
- The pre-flight checks the full P8 list, so an unhandled reference surfaces as an orphan, never as an `IntegrityError`.

**D-iv. One transaction.** On one connection:
1. `BEGIN IMMEDIATE`, then `PRAGMA defer_foreign_keys=ON`.
2. Apply dispositions.
3. Run the import.
4. `PRAGMA foreign_key_check`, which must return 0 rows.
5. `COMMIT`.

Any exception → `ROLLBACK`, and nothing changes. The error comes back in the `ErrorResponse` envelope with the backup path; never a raw 500.

**D-v. Preconditions**, checked before the backup:
- no generation run is `running` → otherwise `409 runs_active`;
- the import lock is free (existing).

`force=true` without a disposition for every orphan → `422 dispositions_incomplete`. `force` no longer means "skip the orphan check".

**Reimport state machine:** `idle → backing_up → staging → (orphans? → 409 orphans_detected, back to idle) → importing(txn) → done | failed (rolled back; backup path reported) → idle`. Every state has an exit, and a crash in `importing` leaves SQLite's rollback journal/WAL to discard the uncommitted transaction.

## 5. Failure modes

| Failure | Behaviour |
|---|---|
| Backup disk full / VACUUM INTO fails | Periodic: log, retry next interval. Pre-reimport: abort, `503 backup_failed`, nothing changed. |
| Crash during import txn | Uncommitted txn discarded by SQLite on next open; backup exists. |
| FK violation discovered at `foreign_key_check` | Rollback, `409 orphans_detected` with the offending rows added to the summary (pre-flight bug, but never data loss). |
| Remap target collides with existing work on the target ID | Allowed; target keeps both (rankings: the remapped ranking wins only if the target has none, else the pair is reported as `remap_conflict` and the request 409s before any write). |
| Generation run starts mid-reimport | Import lock is checked by `POST /runs` (existing lock, now enforced at run creation → `409 import_in_progress`). |
| stdin prompt delivery fails (CLI rejects) | Item fails with `claude_cli_unavailable`; covered by test with the fake CLI. |

## 6. Tests and evals (each fails on `4c076fa`, passes after)

**Parity (A):** `test_red_letter_parity.py`. Parametrised over five DB states:
1. fresh import;
2. manual mark in chapter 8;
3. reject of range 1;
4. reject then restore of range 1;
5. unmark of one sentence.

For each state it asserts the invariant in §4A across the reader, GSV JSON, GSV coverage, the chapter bundle, the per-sentence bundle's context flags and `all_unranked_red_letter`.

**Other A tests:**
- `GET /red-letter/chapter/11/overlays` → 200 on a fresh import (C10).
- Chapter bundle over 200 KB delivered via stdin to the fake CLI.

**B:**
- `BOTH_GREEK` bundle for `mat-5-31` has no English anywhere in the context; the `GREEK_PLUS_BSB` bundle does.
- `sources_seen` for a pre-fix snapshot fixture (copied verbatim from a real snapshot) includes `"BSB (context)"`.
- A summary snapshot with empty candidates → `candidates_drawn_on == 0`.

**C:**
- A backup file opens and passes `integrity_check`, and row counts match.
- Retention keeps `pre-reimport` backups.
- A failed pre-reimport backup aborts the reimport.
- `make clean` leaves `data/*.db` in place.

**D:**
- A fixture with Matthew 13:1–2 merged:
  - is reported as `sentence_id_remapped` with the right target;
  - `remap` moves the candidates, snapshots, run items and ranking;
  - `delete` removes them with no `IntegrityError`.
- A range prepended to the red-letter fixture keeps overlay 3 on the Sermon on the Mount.
- Injecting an exception after the dispositions leaves the DB byte-identical and the ranking present.
- `force=true` without complete dispositions → 422.
- A reimport while a run is running → 409.

**Evals:** these extend the existing collections in `platform/evals/fixtures/bible.json`, the current home of the bible-study suite.
- `bible-study-import` gains `gsv-5-red-letter-parity`:
  1. unmark `mat-5-4`;
  2. restore it;
  3. assert `GET /gsv/5?format=json` → `coverage.total_red_letter_sentences == 68`;
  4. assert `GET /sentences?chapter=5` → 68 red.
- `bible-study-admin-reimport-and-build` asserts the reimport response carries a `backup_path`.

## 7. Non-goals

- The rank-page defects (C5–C7), ranking and tie-break validation (C8, C9), stale-run recovery (C15) and the static GSV page (C16). Each is a bug-fix PR through `/skill-pr-ship`, with no plan needed.
- Split / merge / extend / retract.
- Sandboxing the generation agent: tool restrictions and a bundle-only working directory. That's a separate plan.
- Off-machine backups.
- Moving the bible-study eval suite into this repo, and faking the Claude CLI for the run evals. Today the run collections spend real subscription usage. That's a known gap, noted for a follow-up.

## 8. Delivery

Each PR is stacked on `slice/3a-7-baseline` and goes through `/skill-pr-ship`:

| PR | Contents | Depends on |
|---|---|---|
| A | `effective.py`, all callers switched, private copies deleted, C10 fixed, prompt passed on stdin, parity tests, parity eval | — |
| B | context window per source set, `sources_seen`, `candidates_drawn_on`, snapshot canon bump | A (bundle code touched by both) |
| C | backups, lifespan task, Makefile targets | — |
| D | migration 0004, identity check, dispositions, single-transaction reimport, preconditions, reimport eval | C |

**Rollback:** each PR reverts cleanly. Migration 0004's downgrade drops `range_key` via `batch_alter_table`. The derived fields in B need no migration.
