# TODO

Tech debt, deferrals, and future work. Items move out of this file when they land in code or get explicitly dropped. Don't let it become a dumping ground — if something hasn't been touched in a long time, it's probably not actually wanted.

## Follow-ups from the SPEC/PLAN cleanup pass

The cleanup pass (this slice) reworked `SPEC.md` and `PLAN.md` to describe the worktree-subagent mechanism instead of the legacy Anthropic-SDK shape. Two follow-up items remained, neither blocking:

- **Drop dead `error_code` values from the schema CHECK** (`anthropic_api_error`, `anthropic_refusal`, `rate_limit`). The worktree runner never emits them; PLAN now documents the live set as `claude_cli_unavailable | timeout | invalid_response | disk_full | internal`. Removing the dead values is a small forward-only Alembic revision (SQLite 3.35+ supports DROP-and-recreate via `batch_alter_table`). Defer to a slice that's already touching the schema so we don't migrate just for this.
- **`evals/specs/bible-study/run.md` still references `BIBLE_STUDY_FAKE_CLAUDE=1`.** The env-var toggle is gone — the fake spawner is now wired via the `WorktreeSpawner` Protocol DI in `api/tests/fakes.py` and the runner accepts a spawner argument directly. Sweep the eval spec when next editing it.

## Verification (do before building)

- [x] **Pin exact source files & checksums** for every ingested text. Slice 2 wired up SBLGNT (Faithlife/SBLGNT@a785c74), Byzantine (byztxt@97371d7), BSB / BLB (bereanbible.com / literalbible.com public-domain text downloads, retrieved 2026-05-03), BIB (`bsb_tables.tsv` interlinear, retrieved 2026-05-03), WEB (eng-web USFM 2026-04-23). Both upstream-file hashes and on-disk normalized-fixture hashes are pinned in `fixtures/manifest.json`.
- [ ] **Confirm Berean 2023 distributions ship red-letter tagging** in machine-readable form. Slice 2 confirmed the public-domain BSB/BLB text downloads do **not** ship Jesus-speech markup; the BSB tables TSV's `<p class=|red|>` paragraph-class hint is HTML-presentation-only and not at the per-clause boundary level we need. Falling back to hand-curated ranges per `CLAUDE.md` JTBD #2 (Sermon on the Mount + Matt 11:28–30 in v1; Gavin authors the rest through the UI in a later slice).
- [x] *(SBLGNT segmentation: live and asserted by `test_full_matthew_imports_28_chapters`. Byzantine sentence-level alignment remains deferred — see v2 list below.)*

## Known deferrals (decided, not yet built)

- [ ] **UI for the ranking workflow.** Hardest UX in the project. Prototype on a single chapter (Matthew 5 — Sermon on the Mount, lots of red letters) before generalizing.
- [ ] **UI for the red-letter range editor** — split, merge, extend, retract, reject. Must persist as an overlay, not a mutation of the imported fixture.
- [ ] **Hand-curated red-letter ranges for Matt 8–28 (excluding 5–7 and 11:28–30).** Slice 2 ingested full Matthew but only authored ranges for the Sermon on the Mount (Matt 5:3–7:27) and "Come to Me" (Matt 11:28–30). The rest of Jesus' speech in Matthew (e.g., Matt 8 commission, Matt 10 sending of the Twelve, Matt 13 parables, Matt 18 community discourse, Matt 23 woes, Matt 24–25 Olivet discourse, Matt 26 last supper, Matt 28 Great Commission) is deferred to the range-editor UI slice — Gavin will author them through the UI rather than maintaining a hand-edited JSON file. Until then, those passages display without red-letter flagging.
- [ ] **Static-site export pipeline.** Local web UI is the primary surface; the static site is generated from the same SQLite. Don't build the export until the local UI's read-only views are stable, or we'll churn the templates twice.
- [ ] **Sentence-alignment authoring tools.** Auto-alignment from BIB's Strong's keys gets us most of the way for the SBLGNT-family text; Byzantine alignment and any sentence-boundary adjustments will need a manual review surface.

## v2: Byzantine sentence-level alignment (deferred from v1)

In v1, Byzantine ships as **verse-keyed reference text only** — `SPEC.md` Manager success criteria narrow the JTBD #1 framing accordingly. Lifting the deferral in v2 requires:

- [ ] Pick a Byzantine edition / segmenter and document it (the original v1 verification item).
- [ ] Add `byzantine_sentences` rows + word tokenization for Byzantine.
- [ ] Replace the verse-range-overlap heuristic that drives the parallel reader's `↔` "boundaries diverge" glyph with a real sentence-boundary comparison.
- [ ] Decide whether ranking units stay SBLGNT-anchored or become per-edition.

## Tech debt placeholders

- *(resolved in slice 6)* ~~Importer's TRUNCATE+INSERT phase blows up on FK from `claude_candidates.style_prompt_version` → `style_prompts.version` when the prompt body changes.~~ Slice 6 fixed it via two complementary changes in `api/importer/runner.py`: (a) the import txn now does `PRAGMA defer_foreign_keys = ON` so DELETE-then-INSERT cycles inside one transaction are checked at COMMIT only — this resolves the broader FK collision when overlay tables reference soon-to-be-rebuilt source rows; (b) `style_prompts` is now a pure UPSERT on the PK (`ON CONFLICT(prompt_version) DO UPDATE ...`) rather than `INSERT OR REPLACE`, so a body edit updates the row in place without DELETE+INSERT. Regression test: `api/tests/test_importer.py::test_reimport_after_prompt_body_change_succeeds`.

- **GSV coverage SQL doesn't pick up overlay-driven red-letter membership.** `GET /api/v1/gsv/coverage` reports `total_red_letter_sentences=3 / ch5_total=0` while `GET /api/v1/sentences?chapter=5` correctly reports 68 red-letter in Mt 5. The coverage endpoint (`api/gsv/service.py`) computes counts directly from `red_letter_source_ranges` instead of going through the canonical effective-set CTE introduced in slice 7. Fix: route the coverage count through `effective_red_letter_set(conn, chapter)` (or its repository-wide equivalent) so it honors `red_letter_overlays.rejected=1` and manual-origin overlay heads. Affects only the GSV coverage display; ranking, parallel reader, and chapter summaries all use the correct CTE already.

## Future work (out of scope for v1)

- [ ] **Books beyond Matthew.** Don't generalize the data model until Matthew works end-to-end. When generalizing, the schema changes most likely to bite: per-edition versification differences (e.g., Psalms, Malachi), books that don't have red letters at all, and OT Greek (LXX) vs. NT Greek as separate editions.
- [ ] **Non-red-letter ranking.** The ranking workflow is initially scoped to red letters. Extending it to narrative passages is a UX scaling problem, not a data-model problem.
- [ ] **Additional English translations** beyond the Berean family + WEB: KJV, ASV, BBE, YLT, Douay-Rheims. Add only when there's a study reason, not for completeness.
- [ ] **Hebrew OT support.** Far future. Would require a different alignment dataset (no Strong's-keyed Berean equivalent for the Hebrew the same way BIB exists for the Greek NT — verify if/when relevant).
- [ ] **Multi-user / sharing.** Explicitly out of scope. If it ever comes up, the current "Gavin's overlay" model needs to become "per-user overlays" — note the design point now so we don't paint ourselves in.
- [ ] **Mobile / offline reader.** The static-site export covers most of this for free. A real RN/Expo client is only worth building if the static site falls short.
- [ ] **Audio.** Listening to Greek pronunciation alongside the text would be valuable for study but is a separate project.
