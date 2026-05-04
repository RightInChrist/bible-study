# bible-study

Gavin's personal Bible study tool. Starting with the Gospel of Matthew.

## Jobs To Be Done

The primary user is Gavin (one user, one device for now — no multi-tenancy).

1. **Read Matthew with the Greek alongside open-source English translations.**
   Side-by-side view, sentence-aligned. Both Greek editions are kept in the system so they can also be compared side-by-side: **SBLGNT** (modern critical text, permissive license) and **Byzantine / Robinson-Pierpont** (majority text, public domain). English translations: the **Berean family** (CC0, April 2023) is the primary source — **BSB** (Berean Standard, readable modern), **BLB** (Berean Literal, wooden), and **BIB** (Berean Interlinear, Greek-English word-by-word with Strong's-keyed translation tables). After Berean: WEB, then KJV / ASV / BBE / YLT as appetite allows.

   The Berean Interlinear's Strong's-keyed alignment is a foundational asset for this project — it gives us word-level Greek↔English alignment without us having to build it.

2. **Focus on the red letters — the spoken words of Jesus in Matthew.**
   **Preferred source: Berean 2023 red-letter tagging**, if it ships in the public-domain data distribution (verify — see TODO). If present, ingest it as the baseline. If not, fall back to hand-curating ranges for Matthew (tractable for one book).

   Either way, the client UI must let Gavin **edit ranges directly** — split, merge, extend, retract, or reject a range — and persist corrections. Edits are stored as an **overlay** on top of the imported source so we never mutate the upstream fixture; the effective red-letter set at any moment is `Berean source ∪ Gavin's overlay`. Provenance per range records origin (Berean | manual) and any modification history.

3. **Generate Gavin's own English translations of red-letter passages, sentence by sentence, using Claude with multiple grounding combinations — primarily oriented toward cultural and historical context.**

   The point of generating new translations is not stylistic variety for its own sake. It is to address a recurring problem: standard English translations regularly produce phrases that read as confusing or self-contradictory to a highly analytical mind, because they translate Greek text that itself almost certainly translates an Aramaic or Hebrew original — and that original depended on cultural idioms, honor/shame conventions, Second Temple theological assumptions, and pragmatic context that no longer travel with the words. The goal is to recover what Jesus *meant* and what he *was doing* — speaking and acting in first-century Galilean Jewish culture under Roman occupation, with **Aramaic** as his most likely everyday language, **Hebrew Scripture** as his constant intertext, and **Greek** as the surviving manuscript layer that already reflects one round of translation.

   A "grounding combination" is *(style prompt) × (source set)*:
   - **Style prompts** (versioned artifacts in this repo). The headline category for v1 is **first-century-Jewish-context aware** — Aramaic/Hebrew underlying-language hypothesis, cultural idioms, honor/shame conventions, Second Temple theological framing, awareness of who Jesus is addressing (disciples, crowds, opponents, individuals), and what social/ritual act the speech is actually performing. Other style prompts — *literal/wooden*, *dynamic-equivalent*, *plainspoken modern*, *poetic* — exist as cross-checks or for specific passages, but the cultural-grounding lens is the primary one.
   - **Source sets**: SBLGNT only, Byzantine only, both Greek editions, Greek + BIB interlinear glosses (Strong's-anchored), Greek + BLB (literal) as a cross-check, Greek + BSB (modern) as a cross-check, English-only (e.g., BSB alone) flagged as such. The agent is told explicitly which sources it may consult.

   **Underlying-language hypothesis as a first-class output.** Where the Greek shows signs of being translated from an Aramaic or Hebrew original — Semitic syntax, parallelism, idiomatic dead-metaphors, Hebrew Scripture allusions, untranslatable wordplay, divine-passive constructions — a candidate may include the conjectured underlying phrase (in transliterated Aramaic/Hebrew where useful) plus a discussion of why that conjecture is plausible and what it changes. This is study material in its own right, not just a route to a "better" English. An analytical reader benefits from seeing the layer of cultural and linguistic translation that current English versions silently absorb.

   Each candidate is stored with its style prompt version, source set, model, and timestamp so results are reproducible.

4. **Rank-order candidate translations sentence by sentence.**
   For each sentence, Gavin compares: both Greek editions, all open-source English translations, and all Claude-generated candidates — then ranks them. Rankings are persisted.

5. **Compile the Gavin Standard Version (GSV).**
   The GSV for Matthew is the per-sentence top-ranked translation, stitched into a continuous text. Exportable as plain text plus a structured form preserving which source won each sentence and why. The same export pipeline feeds a **static site** generated from the database.

## Alignment

The atomic unit is the **sentence** (as defined by the Greek). Every sentence has:
- a stable sentence ID
- the Greek span in each edition (SBLGNT and Byzantine) — they may diverge; record both spans
- the verse range(s) the sentence covers in standard chapter:verse notation
- a **`starts_at_verse_boundary`** flag — true when the Greek sentence begins exactly at the start of a verse. We want to surface this in the UI because it's the easy/clean case; the rest are the interesting ones.
- per-translation spans for each English translation (since English versification follows verse boundaries, the mapping is sentence → set of verses → English text in that translation)

Mismatches between SBLGNT and Byzantine sentence boundaries are themselves data — record them, don't hide them.

## Scope guardrails

- **One book to start: Matthew.** Don't generalize the data model to all 66 books until Matthew works end-to-end.
- **Red letters first.** Non-red-letter passages can be displayed for context but ranking/GSV workflow targets red letters initially.
- **Personal tool, not a product.** No auth, no sharing, no SEO. Local-first.
- **Translation provenance is non-negotiable.** Every English sentence in the GSV must be traceable to either (a) a specific open-source translation + verse range, or (b) a specific style prompt version + source set + model + timestamp. No untraceable text.

## Generation mechanism

Translation candidates are produced by **Claude Code subagents running in a git worktree sandbox** — the same Claude Code subscription and credentials Gavin is already using interactively, not direct Anthropic API calls. The bible-study runner spawns a Claude Code worktree per generation, hands it the source-set bundle and the style prompt, captures the candidate (English translation + any underlying-language hypothesis + cultural notes), and stores the result with full provenance.

Implications:
- **No `ANTHROPIC_API_KEY` plumbing.** Credentials live in Claude Code, not in this repo. Anything in `SPEC.md` / `PLAN.md` / code that assumes `ANTHROPIC_API_KEY` or the `anthropic` Python SDK is the artifact of an earlier (now-superseded) plan and should be reworked.
- **No per-token dollar cost.** Usage is bounded by Gavin's existing Claude Code subscription / rate limits, not a USD-per-token meter. Cost-cap settings (`MAX_RUN_COST_USD`, `MAX_DAILY_COST_USD`) become "max worktrees per run", "max worktrees per day", or simply "max wall-clock minutes per run". Pick a unit that maps to actual subscription limits, not a billing fiction.
- **Model provenance** is the Claude Code agent identity at generation time (e.g., `claude-opus-4-7`), captured per candidate from the worktree's reported model string.
- **Concurrency** is bounded by what Claude Code can run safely in parallel on this machine (worktrees + processes), not API connection-pool size.
- **Worktree hygiene**: each generation runs in an isolated branch or worktree so concurrent runs don't trample each other; the runner cleans up worktrees after capturing output.

## Tech stack

- **Storage**: SQLite (single file, version-controlled location TBD — likely gitignored with a deterministic rebuild from fixtures). Source texts and any hand-curated data live as **fixture files** (JSON/YAML) in the repo and are loaded into SQLite by an idempotent import script.
- **Backend**: Python + FastAPI for the local web UI's API.
- **Frontend**: minimal local web UI (framework choice deferred — likely something static-export-friendly so the same components render the static site).
- **Static site**: the ranking/GSV state in SQLite is the source of truth; a build step renders a static site (read-only views: parallel reader, red-letter focus, GSV) for publishing.
- **Type discipline**: Python type hints everywhere; FastAPI routes use Pydantic `response_model` per global rules.

## Open questions

- UI shape for the ranking workflow and the red-letter-range editor — hardest UX, worth prototyping early.
- ~~Where exactly to source each Berean text (BSB / BLB / BIB) — the official `bereanbible.com` distributions vs. mirrors. Pin specific versions/checksums in fixtures.~~ Resolved in slice 2 — pinned to `bereanbible.com/bsb.txt`, `literalbible.com/blb.txt`, and `bereanbible.com/bsb_tables.tsv` (the BIB interlinear ships as part of the BSB Translation Tables TSV); see `fixtures/manifest.json` for SHA-256s.
- Sentence segmentation source per Greek edition — SBLGNT punctuation works directly; Byzantine needs a chosen segmenter (deferred to v2 — Byzantine is verse-keyed only in v1, see `TODO.md`).
