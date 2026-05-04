# TODO

Tech debt, deferrals, and future work. Items move out of this file when they land in code or get explicitly dropped. Don't let it become a dumping ground — if something hasn't been touched in a long time, it's probably not actually wanted.

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

(Empty for now — repo has no code. Add items here as shortcuts get taken during implementation. Each entry should name the file/area, the shortcut, and the cost of leaving it.)

## Future work (out of scope for v1)

- [ ] **Books beyond Matthew.** Don't generalize the data model until Matthew works end-to-end. When generalizing, the schema changes most likely to bite: per-edition versification differences (e.g., Psalms, Malachi), books that don't have red letters at all, and OT Greek (LXX) vs. NT Greek as separate editions.
- [ ] **Non-red-letter ranking.** The ranking workflow is initially scoped to red letters. Extending it to narrative passages is a UX scaling problem, not a data-model problem.
- [ ] **Additional English translations** beyond the Berean family + WEB: KJV, ASV, BBE, YLT, Douay-Rheims. Add only when there's a study reason, not for completeness.
- [ ] **Hebrew OT support.** Far future. Would require a different alignment dataset (no Strong's-keyed Berean equivalent for the Hebrew the same way BIB exists for the Greek NT — verify if/when relevant).
- [ ] **Multi-user / sharing.** Explicitly out of scope. If it ever comes up, the current "Gavin's overlay" model needs to become "per-user overlays" — note the design point now so we don't paint ourselves in.
- [ ] **Mobile / offline reader.** The static-site export covers most of this for free. A real RN/Expo client is only worth building if the static site falls short.
- [ ] **Audio.** Listening to Greek pronunciation alongside the text would be valuable for study but is a separate project.
