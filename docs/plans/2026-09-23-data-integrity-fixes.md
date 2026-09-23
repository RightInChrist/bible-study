# Data-integrity fixes: one red-letter truth, honest provenance, backups, safe reimport

**Status:** draft for spec review, round 2 (revised for review `9b572bdd`) · **Base:** `slice/3a-7-baseline` (PR #1) · **Author date:** 2026-09-23

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
   - `make import` skips every check and re-points work silently (P12).

## 2. Premises (executed on copies of `data/bible_study.db` at `4c076fa`)

Every row was executed on a scratch copy unless it says otherwise. The real DB and working tree were not touched.

| # | Claim | Evidence |
|---|---|---|
| P1 | Three red-letter implementations disagree | `effective_red_letter_set` → **535** sentences; `api/claude/sources.py::_red_letter_sentence_ids` → **3**; `api/gsv/service.py::_red_letter_sentence_ids` → **3**. Matthew 5: effective **68**, GSV **0**. |
| P2 | Root cause: "any reject = rejected forever" | Source range 1 chain: `(3,'reject',rejected=1,parent=None)`, `(5,'create',rejected=0,parent=3)`. The restore is a `create` child of the reject. The stale queries test `NOT EXISTS (… operation='reject')` instead of the chain head. |
| P3 | GSV drops rankings in Matthew 5 | `PUT /sentences/mat-5-5/ranking` (WEB first) → 200. `GET /gsv/5?format=json` then still gives `provenance: {kind: translation, name: BSB}` and `coverage.total_red_letter_sentences: 0`. |
| P4 | Summaries were built on nothing | All 4 `chapter_summaries` snapshots contain `"red_letter_candidates":[]`. |
| P5 | BSB leaks into Greek-only bundles | 365/365 candidates have `source_set_id=BOTH_GREEK`, and 365/365 of their snapshots contain `bsb_text`. The leak is by design: the `AdjacentSentence` docstring says "a readable English (BSB) line for pericope orientation". |
| P6 | No backup code exists | `grep -rn "VACUUM INTO\|backup_interval_seconds" api` returns only the settings declaration (`api/settings.py:66`). |
| P7 | Source ranges have no stable identity | `fixtures/red_letters/matthew.json` entries have no key field. The importer runs `DELETE FROM red_letter_source_ranges` and re-inserts with new rowids (`api/importer/runner.py:412`, `:483`). |
| P8 | Referencing tables | From `pragma_foreign_key_list`. **Sentence references:** `words`, `red_letter_source_ranges` (start/end), `red_letter_overlays` (start/end), `source_snapshots`, `claude_candidates`, `hidden_combos`, `rankings`, `tie_break_decisions`, `generation_run_items`. **Source-range references:** `red_letter_overlays.source_range_id`. **Overlay references:** `red_letter_overlays.parent_overlay_id`. **Candidate references:** `ranking_entries`, `tie_break_decisions`, `generation_run_items`. **Snapshot references:** `claude_candidates.source_snapshot_hash`, `chapter_summaries.source_snapshot_hash`. **Ranking references:** `ranking_entries.sentence_id → rankings`. |
| P9 | Orphan resolution is broken either way | *Reviewer-executed (review `9b572bdd`) on a DB with 13:1–2 merged.* `force` + delete → `IntegrityError: FOREIGN KEY constraint failed`. `force` + keep → **succeeds** and leaves the 35 chapter-13 candidates on different Greek (C11). Dispositions commit in a separate transaction before the import (`api/admin/service.py:506-523`, read). |
| P10 | Chapter bundles exceed the argv limit once P1 is fixed | With the effective set patched in, chapter 5's canonical bundle is **294,856** bytes (68 candidates) and chapter 6's is **221,578**. `subprocess.run(['/bin/true', 'x'*140000])` → `OSError(7, 'Argument list too long')`; 130,000 bytes works. `_invoke_claude` passes the prompt as one argv argument. |
| P11 | `claude -p` accepts the prompt on stdin at this size | *Author-executed.* `python3 -c "print('…' + 'x'*200000)" \| claude -p --model haiku` → exit 0 and a model response. The reviewer re-ran the mechanism with a trivial prompt (`PONG`). |
| P12 | `make import` bypasses every reimport safeguard | Fixture with 13:1's final `·` changed to `,` (manifest hash updated), then `python -m scripts.import_fixtures` on a DB copy → exit 0, `import.committed … sentences 1681`. 35/35 chapter-13 candidates now sit on different Greek (candidate 77: `Ἰδοὺ ἐξῆλθεν ὁ σπείρων…` → `καὶ ἐν τῷ σπείρειν αὐτὸν…`), and `foreign_key_check` is empty. |
| P13 | Constraints that shape delete and remap | (a) `UPDATE generation_run_items SET candidate_id=NULL` on a completed per-sentence item → `CHECK constraint failed`. All 365 candidates have one. (b) Sequential `UPDATE rankings SET sentence_id=…` across shifted IDs → `UNIQUE constraint failed: rankings.sentence_id`. PK and UNIQUE checks are not deferrable. (c) The `red_letter_overlays` CHECK rejects a non-rejected row with NULL bounds. (d) The 4 summary snapshots are `CHAPTER_BUNDLE` rows anchored on `mat-5-1`, `mat-6-1` and `mat-7-1`. (e) Every non-rejected overlay has whole-sentence offsets (`start=1`, `end=word_count`). |
| P14 | Text lookup is ambiguous; positional alignment isn't | 38 SBLGNT sentence texts occur more than once, and 23 candidates sit on them (`mat-13-9` = `mat-11-24` = `mat-13-66`). `difflib.SequenceMatcher(autojunk=False)` over the whole book's normalized texts (1682 old → 1681 new, **48 ms**) returns exactly one non-equal block, `replace [mat-13-1, mat-13-2] → [mat-13-1]`. It maps `mat-13-9 → mat-13-8` (not 13-65 or 11-24), and 88 IDs shift. |
| P15 | The D transaction works on real data | DB copy with rankings on `mat-13-2/3/4`, and a delete disposition on `mat-13-4`: candidate 77 with its completed run item, a claude ranking entry, a tie-break and a hidden combo. Steps: delete child-first, two-phase remap of the other shifted IDs, replace sentences/words/source ranges. Result: `foreign_key_check` 0, `COMMIT` ok, 364 candidates, 4 summaries, effective set 535 → 535. **Gotcha:** a bare `INSERT INTO sentences SELECT * FROM staged.sentences` makes `COMMIT` fail with `FOREIGN KEY constraint failed` while `foreign_key_check` is empty. SQLite's transfer optimisation skips the deferred-FK bookkeeping; `… WHERE 1` commits. |
| P16 | Overlays mask source-bound changes; deleting an overlay changes the red-letter set | `UPDATE red_letter_source_ranges SET end_sentence_id='mat-7-38'` on range 1 → effective set 535 → 535, and `mat-7-37` stays not red: the head's bounds win in the CTE. Deleting leaf overlay 5 → 535 → **374** (the Sermon on the Mount is rejected again). |
| P17 | A file-copy restore replays a stale WAL | Simulation: a backup with 1 row, then 1997 more rows committed with checkpointing off, then the process is killed (33 KB `-wal`). Copying the backup over the `.db` → opening shows **1998** rows. `VACUUM INTO` pre-restore, then delete `-wal`/`-shm`, then copy → 1 row, `integrity_check` ok, and pre-restore holds 1998. |
| P18 | Snapshots carry empty English keys | All 365 candidate snapshots have `bsb_verses`, `blb_verses` and `bib_interlinear` present and **empty**. All 365 have neighbour `bsb_text`, and in **227** a neighbour's verse span covers the focal verse. `payload.sblgnt` equals the current `text_sblgnt` for 365/365. |
| P19 | No stale-run recovery at startup | `grep -rn "lifespan\|on_event" api` (non-test) → nothing. `scripts/serve.py` runs uvicorn with `reload=True`. |
| P20 | A backup can be taken inside the reimport's write lock | Connection A holds `BEGIN IMMEDIATE` with an uncommitted write, and connection B runs `VACUUM INTO` → ok, `integrity_check` ok, and the backup does **not** contain A's uncommitted write. |

## 3. Decisions (owner)

- **D1 (2026-09-23) — context window is Greek-only for Greek-only source sets.** English appears in adjacent-sentence context only when the source set already includes that English text.
- **D2 (2026-09-23) — the 365 existing candidates stay, and their provenance shows what they actually saw.** Nothing is relabelled and nothing is regenerated. "What it saw" is derived from the stored snapshot, not stored separately.
- **D3 (2026-09-23) — the 4 existing chapter summaries stay visible**, flagged "drew on 0 candidates". Gavin regenerates whichever he wants from the UI after the fix.
- **D4 (2026-09-23) — Greek punctuation-only changes are cosmetic.** The work stays attached automatically. Every punctuation change is listed in the reimport result's `cosmetic_changes` and surfaced in the orphan UI and in GSV provenance. (Owner decision on review `9b572bdd`, option b.)

## 4. Design

### A. One red-letter lookup (fixes C2, C3, and C10 on the same read path)

- New module `api/red_letter/effective.py`. It depends only on `sqlite3`, so the runner can import it without the import cycle `sources.py` works around. It holds three things:
  - `_EFFECTIVE_SET_SQL`, moved verbatim from `api/red_letter/service.py`;
  - `effective_red_letter_set(conn) -> set[str]`;
  - `sentence_ids_between(conn, start_id, end_id)`.
- Every "is red letter" question uses it: `red_letter/service.py`, `sentences/service.py`, `gsv/service.py` (the chapter GSV and coverage), `claude/sources.py` (the context-window flag and the chapter bundle), and `runs/runner.py` (`all_unranked_red_letter` = effective set minus ranked sentences).
- **The three private copies are deleted.** This is the structural fix: there is no second implementation left to drift.
- **C10** is on the same read path. `_synthesise_source_only_chain` builds `Overlay(version=0)`, the documented "no edits yet" sentinel that the UI keys on (`overlay_id === 0`, `If-Match: 0`). The response schema's `version` field (`ge=1`) rejects it. The fix is `version: int = Field(ge=0)`, documented as 0 only for the synthetic `overlay_id=0` chain. The DB CHECK (`version >= 1`) is unchanged.
- **Release-sequencing coupling (P10):** A enlarges chapter bundles past the argv limit. So the same PR moves prompt delivery in `api/claude/worktree.py` from argv to **stdin** (`claude -p` reads the prompt from stdin). A must not land without it. If stdin delivery fails, the item fails with `error_code='internal'` and the CLI's stderr in `error_message`, as other CLI failures do today. There's no new code; the `generation_run_items` CHECK has no slot for one.
- **Chapter-bundle budget.** With the correct set, bundles grow with every grounding combination Gavin generates. At about 3.7 KB per candidate (chapter 5 median), a second combination per sentence roughly doubles chapter 5. So `build_chapter_bundle` selects candidates in two passes:
  1. **Baseline:** one candidate per red-letter sentence, in chapter order. It is the top-ranked candidate, or the most recent non-hidden one when the sentence is unranked.
  2. **Fill:** further non-hidden candidates, round-robin in chapter order (each sentence's next most recent), while the canonical bundle stays ≤ `CHAPTER_BUNDLE_MAX_BYTES` (new setting, default **360,000**). It stops at the first candidate that would cross the cap.

  If the baseline alone exceeds the cap, run creation, which already builds the bundle to validate it (`runner.py:396`), returns `422 chapter_bundle_too_large` with `{bytes, cap}` before any spawn. If a rebuilt bundle crosses the cap at dispatch, the item fails `internal` with the same message. `candidate_ids_consulted` records exactly what went in.

  *Reasoned, not executed:* the default assumes about 3–3.6 bytes per token (the reviewer's estimate for Greek-heavy JSON), so 360 KB is about 100–120k input tokens. Checking it would spend subscription usage on a 300 KB prompt. If the assumption is wrong, the CLI's rejection surfaces as `internal` with its stderr, and the cap is a setting.
- **`all_unranked_red_letter` after A:** the scope becomes the 535-sentence effective set minus ranked sentences (0 rankings on the real DB). That is over `MAX_WORKTREES_PER_RUN=200`, so run creation returns the existing `400 max_worktrees_per_run_exceeded`. No UI or eval uses this scope, so it's left as is. The error is explicit and correct.

**Invariant:** for every sentence, the reader's `is_red_letter`, the GSV's `is_red_letter`, the per-sentence bundle's context flag, chapter-bundle membership and run-scope membership all agree.

### B. Bundle provenance (C4; implements D1, D2)

- **D1 (future bundles).** `_fetch_adjacent_sentences` takes the source set and includes English only when that source set contains it:
  - BSB context for `GREEK_PLUS_BSB` and `ENGLISH_ONLY_BSB`;
  - BLB context for `GREEK_PLUS_BLB`;
  - none for `SBLGNT_ONLY`, `BYZ_ONLY`, `BOTH_GREEK` and `GREEK_PLUS_BIB`.

  `AdjacentSentence.bsb_text` becomes optional and is renamed `english_text`, with an `english_source` field. **Greek side:** context sentences are SBLGNT sentences. So `validate_compatibility` rejects a context-window prompt (`wants_context_window: true`) with a source set that excludes SBLGNT (`BYZ_ONLY`, `ENGLISH_ONLY_BSB`) with `400`. The problem is latent (no such prompt exists today), and rejecting it stops SBLGNT from leaking into those sets.
- **Bump the snapshot canon version.** Today it is the literal `"v1"`, hardcoded twice in `api/runs/runner.py` (`:1142`, `:1202`). It becomes one constant, `SNAPSHOT_CANON_VERSION = "v2"`, in `api/claude/sources.py`. That constant is **the** authority for `source_snapshots.source_snapshot_canon_version`: it describes the bundle shape the code produces. The manifest's `source_snapshot_canon_version` is **not** bumped, because a bump would change the manifest hash and show a stale pill. The manifest value is still recorded in `fixture_version` at import, but nothing reads it for snapshots. Old snapshots stay valid under `v1`.
- **D2 (existing and future candidates).** A pure function `sources_seen(snapshot_payload) -> list[str]` derives what the agent actually saw. Each rule tests **non-empty values**, never key presence (P18):

  | Label | Condition |
  |---|---|
  | `SBLGNT` | `sblgnt` non-empty |
  | `Byzantine` | `byzantine_verses` non-empty |
  | `BSB (focal verse)` / `BLB (focal verse)` | `bsb_verses` / `blb_verses` non-empty |
  | `BIB interlinear` | `bib_interlinear` non-empty |
  | `<E> (focal verse, via context)` | a context neighbour has English text, and its `chapter:start_verse–end_verse` overlaps the snapshot's own `verse_range` |
  | `<E> (neighbour context)` | a context neighbour has English text, and its span does not overlap the focal verse |

  Here `<E>` is `BSB` for v1 snapshots (`bsb_text`) and `english_source` for v2. The output order is fixed as in the table. On the real DB the result is `["SBLGNT", "Byzantine", "BSB (focal verse, via context)", "BSB (neighbour context)"]` for 227 candidates (e.g. candidate 77) and `["SBLGNT", "Byzantine", "BSB (neighbour context)"]` for 138 (e.g. candidate 1). It is exposed as:
  - `sources_seen` on `CandidateResponse`, shown next to the source-set label in the reader, rank cards and GSV footnotes;
  - `sources_seen` in the GSV JSON provenance for Claude winners.

  There's no migration. The truth is already stored, so deriving it can't drift.
- **D3:** `ChapterSummaryResponse` gains `candidates_drawn_on: int`, derived from the summary's snapshot. The UI shows "drew on 0 candidates — regenerate?" when it is 0.

### C. Backups (C13 part 1). Lands before D; D requires it.

- New `api/db/backup.py`, with `backup_now(db_path, reason: Literal["periodic", "pre-reimport", "manual"]) -> Path`:
  - writes into `<db_path.parent>/backups/`, derived from the DB path, so test and eval DBs never share a directory or retention with the real DB;
  - names the file `<db stem>-<UTC yyyymmddThhmmssZ>-<reason>-<6 random hex>.db`, so two backups in the same second don't collide (`VACUUM INTO` refuses an existing file);
  - runs `VACUUM INTO`, fsyncs the file, then runs `PRAGMA integrity_check` on it and deletes it if the check fails.
- **Periodic backups:** a FastAPI lifespan task runs every `BACKUP_INTERVAL_SECONDS`. It skips when the DB and WAL mtimes haven't changed since the last backup.
- **Retention (periodic only):**
  - keep the newest `BACKUP_RETENTION_COUNT` (24), plus the newest one per day for the last 14 days;
  - `BACKUP_HARD_CAP` (32) wins over both: if the union exceeds it, the oldest go first;
  - `manual` backups are never pruned automatically. `pre-reimport` backups keep the newest `BACKUP_PRE_REIMPORT_KEEP` (new setting, default 20), so eval reimports against the dev DB can't grow the directory without bound.
  - A pre-reimport backup is written only when a reimport is about to write (D-iv), so a `409` attempt leaves no file. It is skipped when the DB has no dependent work, because everything there rebuilds from fixtures; `backup_path` is then null.
- **Failure mode:** if a periodic backup fails, it is logged and retried at the next interval. A failed **pre-reimport** backup aborts the reimport.
- **Makefile:**
  - `make backup` writes a manual backup.
  - `make restore FILE=…` works in this order (P17):
    1. refuse while the API is up (health check on `127.0.0.1:$BIND_PORT/api/v1/health`, with the port read from settings);
    2. write `pre-restore` with `VACUUM INTO` from the live DB, which captures WAL-committed data a file copy would miss;
    3. delete the live `-wal` and `-shm`;
    4. copy `FILE` in;
    5. run `integrity_check`. If it fails, repeat steps 3–4 with `pre-restore`.
  - `make clean` no longer touches `data/*.db`.
  - `make nuke-db CONFIRM=1` backs up first, then deletes.
- Backups live on the same disk. That protects against logical damage, not against losing the disk. Off-machine copies are a non-goal (§7).

### D. Safe reimport (C11, C12, C13 part 2, C14). Depends on C and on C15.

**D-i. Stable source-range identity.**
- Migration `0004` adds `range_key TEXT` (unique index) and `retired_at TEXT` to `red_letter_source_ranges`. The migration **backfills `range_key` itself**. It matches each existing row's `note` against the keyed fixture's entries: 2 rows today, `sermon-on-the-mount` and `come-to-me`, and both real notes match unique fixture notes (checked on the copy). A row it can't match gets `legacy-<source_range_id>`, which the next import treats as a removed key (below). There is no first-import adoption logic.
  - `NOT NULL` is not added, because SQLite would need a rebuild of a table that overlays reference. The importer enforces the key: fixture entries gain a **required** `key`.
- The importer **UPSERTs by key** and keeps `source_range_id`. It no longer runs `DELETE FROM red_letter_source_ranges`.
- **Key gone from the fixture:**
  - no overlay chain → the row is deleted;
  - with a chain → orphan `source_range_removed`. A range row with a chain is **never deleted**.
- **Bounds changed for a key.** The new bounds are compared with the old bounds **after mapping them through the D-ii alignment**, so an ID shift alone is not a change.
  - chain head non-rejected → orphan `source_range_changed`, because the head's bounds would silently win (P16);
  - no chain → the UPSERT applies and the effective set follows;
  - rejected head → the change is listed in the response. A later restore uses the chain's last bounds, as today.
- **Range dispositions,** keyed by `range_key`. None of them deletes an overlay row (P16):
  - `source_range_removed`:
    - `reject`: append a reject leaf unless the head is already rejected, with origin copied from the head. The range row stays, with `retired_at` set, so the chain's FKs and history survive.
    - `reattach(to_range_key)`: the chain's rows take the target's `source_range_id`. This is allowed only when the target has no chain of its own.
  - `source_range_changed`:
    - `follow_source`: append a `create` leaf with the new source bounds, origin copied.
    - `keep_overlay`: nothing is written, and the head's bounds keep winning.

**D-ii. Sentence identity by alignment.** Nothing looks for matching text elsewhere in the book (P14):
1. Staging builds the new sentence list in memory (`_stage_new_sentences`, existing).
2. The old list (live DB) and the new list are aligned over the **whole book** with `difflib.SequenceMatcher(a=old, b=new, autojunk=False)`. Texts are normalized first: NFC, Unicode category `P*` removed, whitespace collapsed. So by D4, punctuation never breaks an alignment.

Each old sentence is classified by its block:

| Block | Classification | `suggested_target` |
|---|---|---|
| `equal`, same ID, raw text equal | unchanged | — |
| `equal`, same ID, raw text differs | **cosmetic change** (D4); work stays attached | — |
| `equal`, different ID | `sentence_id_remapped` (the Greek is identical; any punctuation difference is also recorded as a cosmetic change) | the aligned ID |
| `replace` 1→1 | `sentence_text_changed` | the aligned ID |
| `replace` n→1 (merge) | `sentence_resegmented` | the one new ID |
| `replace` 1→n or n→m | `sentence_resegmented` | none; `alternatives` = the block's new IDs |
| `delete` | `sentence_removed` | none |

- Only old sentences with **dependent work** become orphans. Dependent work means candidates, a ranking, tie-breaks, hidden combos, per-sentence run items, or non-`CHAPTER_BUNDLE` snapshots. Every orphan carries its aligned block (`aligned_old`, `aligned_new`) so the UI can show the Greek on both sides.
- **Bounds are mapped automatically, never orphaned.** This applies to every `red_letter_overlays` row and to retired source ranges (live ranges are re-derived from the fixture). A sentence ID is a positional label, so rewriting it through the alignment keeps each row on the same Greek, including historical rows. No row is deleted.
  - `equal` → the aligned ID, offsets unchanged.
  - `replace` → a start bound goes to the block's first new sentence at offset 1. An end bound goes to the block's last new sentence at offset `word_count`. All live offsets are whole-sentence (P13e).
  - `delete` → a start bound goes to the first new sentence after the block, and an end bound to the last new sentence before it. If a row's range becomes empty, the reimport refuses with `409 overlay_range_emptied`. *Reasoned, not executed:* re-segmentation redistributes the same words, so between two `equal` anchors both sides of a block are non-empty (always `replace`, never `delete`). A `delete` needs Greek text removed from the SBLGNT fixture, which no planned change does.

  Every rewritten row is listed in the response as `overlay_bounds_rewritten`.
- **Cosmetic changes (D4).** Migration `0004` adds `sentence_text_changes(sentence_id → sentences, old_text, new_text, fixture_version, recorded_at)`. The reimport appends one row per cosmetic change on a sentence with dependent work, and remaps move these rows too. They are surfaced in three places:
  - `cosmetic_changes` in the reimport response and in the `409` details;
  - an informational "Greek punctuation changes" section on the orphan page, with no action required;
  - `provenance.greek_changes` on the GSV JSON sentence, with a note on the GSV page.

**D-iii. Dispositions.**
- **Sentence dispositions**, keyed by old `sentence_id`:
  - `remap(to=<live new id>)`;
  - `delete`.

  There is **no `keep`** when the Greek changed. Work stays attached only to the Greek it was made against, and the pre-reimport backup preserves everything.
- **Remap is one permutation.** Every reference in the remap set first moves to a temporary ID `~remap~<old>` and then to its target, so no order can trip a PK or UNIQUE check (P13b, P15).
  - Conflicts are computed against the **post-remap** state, before any write. Two rankings landing on one target, a ranking landing on a target that keeps its own, or a colliding tie-break → `409 remap_conflict` listing the pairs. Gavin resolves it by deleting one side.
  - `hidden_combos` collisions are deduplicated, keeping the earliest `hidden_at`, because both rows say the same thing.
  - A remap moves `rankings`, `ranking_entries`, `tie_break_decisions`, `hidden_combos`, `claude_candidates`, per-sentence `generation_run_items`, non-`CHAPTER_BUNDLE` `source_snapshots`, and `sentence_text_changes`.
- **`CHAPTER_BUNDLE` snapshots are outside sentence dispositions.** They're anchored on `mat-{ch}-1` (P13d), which every import recreates, and the anchor makes no claim about Greek content.
- **Delete, child-first:**
  1. `ranking_entries` and `tie_break_decisions`: the sentence's own rows, plus any that reference its candidates;
  2. `rankings`;
  3. `hidden_combos`;
  4. `generation_run_items` of the sentence or its candidates. These rows are **deleted, not NULLed**, because the CHECK forbids a completed item without a candidate (P13a). The run's status is unchanged and its item list shrinks. The backup keeps the history.
  5. `claude_candidates`;
  6. the sentence's non-`CHAPTER_BUNDLE` snapshots that no remaining candidate references;
  7. `sentence_text_changes`.

  Overlays are never deleted.
- The pre-flight covers the full P8 list, so an unhandled reference surfaces as an orphan, never as an `IntegrityError`.

**Contract (replaces the per-item keys `candidate:<id>` / `ranking:<sid>` / `overlay:<id>` and `keep`):**

```python
class SentenceDisposition(BaseModel):          # extra="forbid"
    action: Literal["remap", "delete"]
    to: str | None = None                       # required iff action == "remap"

class RangeDisposition(BaseModel):
    action: Literal["reject", "reattach", "follow_source", "keep_overlay"]
    to_range_key: str | None = None             # required iff action == "reattach"

class ReimportRequest(BaseModel):
    force: bool = False
    orphan_fingerprint: str | None = None       # from the 409; required with force
    sentence_dispositions: dict[str, SentenceDisposition] = {}
    range_dispositions: dict[str, RangeDisposition] = {}
```

A request in the old shape gets `422` (`extra="forbid"`).

- **`409 orphans_detected`:** `details.orphan_summary` contains:
  - `orphan_sentences[]`: `sentence_id_old`, `reason`, `aligned_old`, `aligned_new`, `old_text`, `new_texts`, `suggested_target`, `alternatives`, and `dependents`. `dependents` lists the candidates (id, prompt, source set, model, date, excerpt), the ranking (status, entry count, tie-break), and the hidden-combo and run-item counts.
  - `orphan_ranges[]`: `range_key`, `reason`, `old_bounds`, `new_bounds`, `head_overlay_id`, `head_rejected`.
  - `cosmetic_changes[]`, a preview of `overlay_bounds_rewritten[]`, `fingerprint` (a hash of the orphan set), and `total`.
- **`ReimportResponse` adds:** `backup_path`, `cosmetic_changes`, `overlay_bounds_rewritten`, `remapped` and `deleted` counts, and `red_letter_effective_before` / `red_letter_effective_after`.
- **UI (in PR D).** `OrphanResolutionPage` is rewritten:
  - one row per orphaned sentence (old Greek vs the aligned new Greek, plus dependents) and one per orphaned range;
  - **no default choice** (today every row starts on `keep`, `OrphanResolutionPage.tsx:55-57`). The suggested target is shown, and one click picks it;
  - an explicit "accept all suggested remaps" button;
  - **Apply** enabled only when every orphan has a choice;
  - the cosmetic-changes section.

  When a successful reimport returns `cosmetic_changes`, the status pill routes to that section.

**D-iv. One transaction.**
1. **Lock.** Take the import lock: an `fcntl.flock` on `<db_path>.import.lock`, non-blocking, which replaces the in-process `asyncio.Lock` so the API and `make import` exclude each other. If it's held → `409 import_in_progress`. `POST /runs` checks the same lock.
2. **Preconditions** (D-v).
3. **Stage and compute.** Stage the new fixtures in memory and compute orphans plus the fingerprint. If orphans exist, nothing is written (no backup either) unless the request clears three checks:
   - no `force` → `409 orphans_detected`;
   - a fingerprint that doesn't match → `409 orphans_changed`;
   - a missing disposition → `422 dispositions_incomplete`.
4. **Open the transaction.** Connection A: `BEGIN IMMEDIATE`, then `PRAGMA defer_foreign_keys=ON`.
5. **Back up** (only when the DB has dependent work). Connection B runs `backup_now(reason="pre-reimport")`. Because A holds the write lock, the file is the exact pre-image (P20). If it fails → `ROLLBACK`, `503 backup_failed`.
6. **Recheck.** Recompute the orphan set on A. A different fingerprint → `ROLLBACK`, `409 orphans_changed` with the fresh summary. This closes the gap between the 409 and Apply.
7. **Apply.** Apply the sentence dispositions (deletes, then the two-phase remap), then the bound mapping, then the range dispositions.
8. **Import.** Run the import on A: `import_fixtures` takes the caller's connection and no longer opens its own transaction. It inserts through `executemany` as today, never with a bare `INSERT … SELECT *` from a staged table (P15).
9. **Check.** `PRAGMA foreign_key_check` must return 0 rows, or → `ROLLBACK`, `409` with the rows.
10. `COMMIT`.

Any exception → `ROLLBACK`, and nothing changes. The error comes back in the `ErrorResponse` envelope with the backup path; never a raw 500.

**D-v. Preconditions**, checked before staging:
- no generation run is `running` → otherwise `409 runs_active`. This depends on C15 (stale-run recovery at startup) landing first (§8). Without it, a restart mid-run leaves a run `running` forever and blocks reimport permanently (P19).
- the import lock is free.

`force=true` without a disposition for every orphan → `422 dispositions_incomplete`. `force` no longer means "skip the orphan check".

**`make import` (fixes P12).** `scripts/import_fixtures.py` calls the same service function as `POST /admin/reimport`, with `force=false`:
- on a DB with no dependent work (a fresh DB, or test setup), it imports as before;
- on orphans, it prints the orphan summary (counts per reason, then the first 20 orphans), writes nothing, and exits `2` with "resolve at /admin/orphans".

There is no CLI disposition path and no bypass flag. The raw `import_fixtures()` refuses with `FixtureImportError("dependent_work_present")` when it runs outside the reimport service's transaction on a DB that has dependent work.

**Reimport state machine:** `idle → staging → (orphans unresolved? → 409/422, back to idle) → txn[backing_up → rechecking → applying → importing → checking] → done | failed (rolled back; backup path reported) → idle`. Every state has an exit. A crash inside the transaction leaves the uncommitted WAL frames for SQLite to discard on the next open.

## 5. Failure modes

| Failure | Behaviour |
|---|---|
| Backup disk full / VACUUM INTO fails | Periodic: log, retry next interval. Pre-reimport: `ROLLBACK`, `503 backup_failed`, nothing changed. |
| Crash during import txn | SQLite discards the uncommitted txn on next open; the pre-reimport backup exists. |
| FK violation discovered at `foreign_key_check` | Rollback, `409 orphans_detected` with the offending rows added to the summary. This would be a pre-flight bug, but it never loses data. |
| Remap target collides with work on the target ID | Computed against the post-remap state before any write → `409 remap_conflict` listing the pairs. `hidden_combos` duplicates are merged. |
| Work added between the 409 and Apply | Fingerprint recheck inside the txn → `409 orphans_changed` with the fresh summary. |
| An overlay row's range is emptied by a pure text deletion | `409 overlay_range_emptied`. Reasoned unreachable by re-segmentation (D-ii). |
| `make import` against dependent work with orphans | Exit `2`, summary printed, nothing written, no backup file. |
| Generation run starts mid-reimport | `POST /runs` finds the file lock held → `409 import_in_progress`. |
| Run left `running` by a restart | C15 marks it `interrupted` at startup (dependency of D). |
| stdin prompt delivery fails (CLI rejects) | Item fails with `error_code='internal'` and the CLI's stderr. Covered by a test with the fake CLI. |
| Chapter bundle over `CHAPTER_BUNDLE_MAX_BYTES` | Baseline over the cap → `422 chapter_bundle_too_large` at run creation. Over the cap at dispatch → item fails `internal` with the size and cap. |
| `make restore` with a hot WAL | Handled by the restore order in §4C (P17). |

## 6. Tests and evals (each fails on `4c076fa`, passes after)

**Parity (A):** `test_red_letter_parity.py`. Parametrised over five DB states:
1. fresh import;
2. manual mark in chapter 8;
3. reject of range 1;
4. reject then restore of range 1;
5. unmark of one sentence.

For each state it asserts the invariant in §4A across the reader, GSV JSON, GSV coverage, the chapter bundle, the per-sentence bundle's context flags and `all_unranked_red_letter`.

**Other A tests:**
- `GET /red-letter/chapter/11/overlays` → 200 on a fresh import, with head `version == 0` (C10).
- A chapter bundle over 200 KB is delivered via stdin to the fake CLI.
- Bundle budget:
  - with a small `CHAPTER_BUNDLE_MAX_BYTES`, the fill stops at the cap and `candidate_ids_consulted` equals the included set;
  - a baseline over the cap → `422 chapter_bundle_too_large`, and nothing is spawned.

**B:**
- The `BOTH_GREEK` bundle for `mat-5-31` has no English anywhere in the context; the `GREEK_PLUS_BSB` bundle does.
- A context-window prompt with `BYZ_ONLY` → 400.
- `sources_seen` over verbatim copies of two real pre-fix snapshots:
  - candidate 77 → exactly `["SBLGNT", "Byzantine", "BSB (focal verse, via context)", "BSB (neighbour context)"]`;
  - candidate 1 → exactly `["SBLGNT", "Byzantine", "BSB (neighbour context)"]`.
- A summary snapshot with empty candidates → `candidates_drawn_on == 0`.

**C:**
- A backup file opens and passes `integrity_check`, and row counts match.
- The backup directory is derived from the DB path.
- Two backups in the same second both succeed.
- Retention never exceeds `BACKUP_HARD_CAP` periodic files, keeps the newest `BACKUP_PRE_REIMPORT_KEEP` pre-reimport files, and never prunes `manual`.
- A failed pre-reimport backup aborts the reimport.
- A `409` reimport writes no backup, and neither does a reimport on a DB with no dependent work.
- `make clean` leaves `data/*.db` in place.
- Restore with a hot WAL present: the P17 simulation restores the backup's rows, and `pre-restore` holds the live rows.

**D:** all on the P12 fixture (Matthew 13:1–2 merged).
- Alignment:
  - `mat-13-1` and `mat-13-2` are `sentence_resegmented` with the suggested target `mat-13-1`;
  - the 88 shifted IDs are `sentence_id_remapped`;
  - **duplicate text:** `mat-13-9 → mat-13-8`, never `mat-13-65` or `mat-11-24`.
- Remap with rankings seeded on the **adjacent** IDs `mat-13-2`, `mat-13-3` and `mat-13-4` → no UNIQUE error, and the rankings land on `mat-13-1/2/3`.
- `delete` of a candidate with a **completed run item**, a claude ranking entry, a tie-break and a hidden combo → no `IntegrityError`, and the run items are gone.
- A seeded `CHAPTER_BUNDLE` snapshot on the **chapter-first** sentence `mat-13-1` and its summary survive.
- The **chained** overlay 7 (`mat-13-3..79`) becomes `mat-13-2..78`. The chain of range 1 (3 → 5) is untouched.
- `red_letter_effective_before == after == 535`.
- Rankings on both `mat-13-1` and `mat-13-2` with both remapped to `mat-13-1` → `409 remap_conflict`, and nothing is written.
- A `.`→`;` change on a ranked sentence → no orphan. It appears in `cosmetic_changes`, in a `sentence_text_changes` row, and in GSV `provenance.greek_changes` (D4).
- Ranges:
  - A range prepended to the red-letter fixture keeps overlay 3 on the Sermon on the Mount.
  - Range 1's end moved to 7:28 in the fixture → `source_range_changed`. `follow_source` makes `mat-7-37` red; `keep_overlay` doesn't (P16).
  - A removed key with a chain → `source_range_removed`. `reject` appends a leaf, sets `retired_at` and deletes no overlay row.
  - A removed key with no chain → the row is deleted.
- `make import` on the P12 fixture with dependent work → exit `2`, the DB is byte-identical, and no backup file exists.
- A candidate inserted between the `409` and Apply → `409 orphans_changed`.
- Injecting an exception after the dispositions leaves the DB byte-identical and the ranking present.
- `force=true` without complete dispositions → `422`. An old-shape request (`dispositions: {"candidate:1": "keep"}`) → `422`.
- A reimport while a run is running → `409 runs_active`.
- **Web:**
  - `OrphanResolutionPage` starts with no choice selected, and Apply is disabled until every orphan has one;
  - "accept all suggested remaps" fills only the orphans that have a suggestion;
  - the cosmetic section renders.

**Evals:** these extend the existing collections in `pagehub-io/platform` `evals/fixtures/bible.json`, the current home of the bible-study suite, through a **companion platform PR opened alongside PR A** (the reimport eval rides with PR D).
- `bible-study-import` gains `gsv-5-red-letter-parity`. It is **read-only**: it asserts `GET /gsv/5?format=json` → `coverage.total_red_letter_sentences == 68` and that `GET /sentences?chapter=5` has 68 red. It writes no overlays, so it can't leave the dev DB's Sermon on the Mount rejected. The pytest parity test covers the unmark and restore transitions.
- `bible-study-admin-reimport-and-build` asserts the reimport response carries a non-null `backup_path`. The dev DB has dependent work, so a backup is always taken there.

## 7. Non-goals

- The rank-page defects (C5–C7), ranking and tie-break validation (C8, C9) and the static GSV page (C16). Each is a bug-fix PR through `/skill-pr-ship`, with no plan needed. **C15 (stale-run recovery) is the same kind of PR, but D depends on it (§8).**
- Split / merge / extend / retract.
- A CLI disposition path for `make import`. Dispositions are resolved in the UI.
- Reimports that delete Greek text outright and empty an overlay's range (`overlay_range_emptied`). They are refused; handling them needs its own plan.
- Sandboxing the generation agent: tool restrictions and a bundle-only working directory. That's a separate plan.
- Off-machine backups.
- Moving the bible-study eval suite into this repo, and faking the Claude CLI for the run evals. Today the run collections spend real subscription usage. That's a known gap, noted for a follow-up.

## 8. Delivery

Each PR is stacked on `slice/3a-7-baseline` and goes through `/skill-pr-ship`:

| PR | Contents | Depends on |
|---|---|---|
| A | `effective.py`, all callers switched, private copies deleted, C10 fixed, prompt passed on stdin, chapter-bundle budget, parity tests; companion platform PR with the read-only parity eval | — |
| B | context window per source set, `sources_seen`, `candidates_drawn_on`, snapshot canon constant | A (bundle code touched by both) |
| C | backups, lifespan task, retention, Makefile targets (`backup`, `restore`, `nuke-db`, `clean`) | — |
| C15 | stale-run recovery at startup: `running`/`pending` runs and items become `interrupted`, in the lifespan C adds | C |
| D | migration 0004, alignment identity check, dispositions and new contract, bound mapping, single-transaction reimport, file lock, preconditions, `make import` through the service, `OrphanResolutionPage` rewrite; companion platform PR with the reimport eval | C, C15 |

**Rollback:** each PR reverts cleanly. Migration 0004's downgrade drops `range_key` and `retired_at` via `batch_alter_table`, and drops `sentence_text_changes`. The derived fields in B need no migration. The backups written by C stay on disk.
