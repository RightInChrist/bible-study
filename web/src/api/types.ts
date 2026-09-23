/**
 * TypeScript mirrors of the Pydantic response models served by `api/`.
 * Kept in lock-step with `api/sentences/schemas.py` and
 * `api/admin/schemas.py`. Architect §HTTP API: the Pydantic models are
 * the contract; these are the read-side projection of that contract for
 * the React UI.
 *
 * If a backend schema changes, update here in the same slice — the
 * type-checker becomes the alarm bell.
 */

export type TranslationName = "BSB" | "BLB" | "WEB";

export interface SentenceListItem {
  sentence_id: string;
  chapter: number;
  ordinal_in_chapter: number;
  start_verse: number;
  end_verse: number;
  starts_at_verse_boundary: boolean;
  ends_at_verse_boundary: boolean;
  text_preview: string;
  word_count: number;
  is_red_letter: boolean;
}

export interface SentenceListResponse {
  chapter: number;
  sentences: SentenceListItem[];
}

export interface ByzantineVerse {
  chapter: number;
  verse: number;
  text: string;
}

export interface TranslationVerse {
  chapter: number;
  verse: number;
  text: string;
}

export interface TranslationColumn {
  translation: TranslationName;
  verses: TranslationVerse[];
}

export interface BibInterlinearWord {
  chapter: number;
  verse: number;
  position: number;
  greek_form: string;
  strong_id: string;
  transliteration: string;
  english_gloss: string;
  inflected_meaning?: string | null;
}

export interface SentenceParallelResponse {
  sentence_id: string;
  chapter: number;
  ordinal_in_chapter: number;
  start_chapter: number;
  start_verse: number;
  end_chapter: number;
  end_verse: number;
  starts_at_verse_boundary: boolean;
  ends_at_verse_boundary: boolean;
  word_count: number;
  text_sblgnt: string;
  byzantine: ByzantineVerse[];
  english: TranslationColumn[];
  bib_interlinear: BibInterlinearWord[];
  is_red_letter: boolean;
  red_letter_provenance?: RedLetterProvenance | null;
}

// ---------------------------------------------------------------------------
// Red-letter overlays (Slice 7) — mirrors api/red_letter/schemas.py
// ---------------------------------------------------------------------------

export type OverlayOperation =
  | "create"
  | "split"
  | "merge"
  | "extend"
  | "retract"
  | "reject";

export type OverlayOrigin = "berean" | "manual";
export type RedLetterProvenanceOrigin = "berean" | "manual" | "rejected";

export interface Overlay {
  overlay_id: number;
  parent_overlay_id?: number | null;
  source_range_id?: number | null;
  operation: OverlayOperation;
  start_sentence_id?: string | null;
  start_word_offset?: number | null;
  end_sentence_id?: string | null;
  end_word_offset?: number | null;
  rejected: boolean;
  origin: OverlayOrigin;
  created_at: string;
  version: number;
}

export interface OverlayChain {
  source_range_id?: number | null;
  head: Overlay;
  history: Overlay[];
}

export interface OverlayChainResponse {
  chain: OverlayChain;
}

export interface ChapterOverlaysResponse {
  chapter: number;
  chains: OverlayChain[];
  effective_sentence_ids: string[];
}

export interface MarkRedLetterRequest {
  chapter: number;
  start_verse: number;
  end_verse: number;
}

export interface UnmarkRedLetterRequest {
  scope: "source_range" | "manual_overlay";
  target_id: number;
}

export interface RestoreRedLetterRequest {
  scope: "source_range" | "manual_overlay";
  target_id: number;
}

export interface MarkRedLetterResponse {
  overlay: Overlay;
  affected_sentence_ids: string[];
}

export interface UnmarkRedLetterResponse {
  overlay: Overlay;
  affected_sentence_ids: string[];
}

export interface RedLetterProvenance {
  origin: RedLetterProvenanceOrigin;
  source_range_id?: number | null;
  head_overlay_id?: number | null;
}

export interface FixtureStatusResponse {
  disk_manifest_hash: string;
  db_fixture_version: string | null;
  stale: boolean;
  last_imported_at: string | null;
}

export interface ErrorResponse {
  code: string;
  message: string;
  details: Record<string, unknown> | null;
}

// ---------------------------------------------------------------------------
// Generation runs (Slice 3a) — mirrors api/runs/schemas.py
// ---------------------------------------------------------------------------

export type SourceSetId =
  | "SBLGNT_ONLY"
  | "BYZ_ONLY"
  | "BOTH_GREEK"
  | "GREEK_PLUS_BIB"
  | "GREEK_PLUS_BLB"
  | "GREEK_PLUS_BSB"
  | "ENGLISH_ONLY_BSB";

export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type ItemStatus = RunStatus;

export type RunScope =
  | { kind: "one_sentence"; sentence_id: string }
  | { kind: "verse_range"; chapter: number; start_verse: number; end_verse: number }
  | { kind: "whole_chapter"; chapter: number }
  | { kind: "all_unranked_red_letter"; as_of?: string | null }
  | { kind: "chapter_summary"; chapter: number };

export type RequestSourceSetId = SourceSetId | "CHAPTER_BUNDLE";

export interface CreateRunRequest {
  scope: RunScope;
  style_prompt_version: string;
  source_set_id: RequestSourceSetId;
  model: string;
  effort?: string;
}

export interface RunResponse {
  run_id: string;
  status: RunStatus;
  style_prompt_version: string;
  source_set_id: RequestSourceSetId;
  model: string;
  items_count: number;
  items_completed: number;
  items_failed: number;
  items_pending: number;
  items_running: number;
  items_cancelled: number;
  items_interrupted: number;
  estimated_worktree_count: number;
  sentence_ids: string[];
  chapter?: number | null;
  parent_run_id?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export type StylePromptVersion =
  | "literal-v1"
  | "dynamic-v1"
  | "plainspoken-v1"
  | "first-century-jewish-v1"
  | "chapter-summary-v1";

export interface FirstCenturyJewishCandidate {
  english: string;
  underlying_hypothesis?: string;
  cultural_notes?: string;
  intertexts?: Array<{
    reference?: string;
    type?: string;
    note?: string;
  }>;
  audience?: string;
  pragmatic_act?: string;
  confidence?: string;
}

export interface CandidateResponse {
  candidate_id: number;
  sentence_id: string;
  style_prompt_version: string;
  source_set_id: SourceSetId;
  model: string;
  generated_at: string;
  candidate_text: string;
  source_snapshot_hash: string;
  hidden_bool: boolean;
  latency_ms?: number | null;
}

export interface SentenceCandidatesResponse {
  sentence_id: string;
  candidates: CandidateResponse[];
}

// ---------------------------------------------------------------------------
// Rankings (Slice 4) — mirrors api/rankings/schemas.py
// ---------------------------------------------------------------------------

export type RankingTranslationName =
  | "SBLGNT"
  | "BYZ"
  | "BSB"
  | "BLB"
  | "WEB";

export interface TranslationCandidateRef {
  kind: "translation";
  name: RankingTranslationName;
  verse_range: string;
}

export interface ClaudeCandidateRef {
  kind: "claude";
  candidate_id: number;
}

export type CandidateRef = TranslationCandidateRef | ClaudeCandidateRef;

export interface RankingEntry {
  position: number;
  rank: number;
  tied_with_above: boolean;
  candidate_ref: CandidateRef;
}

export interface HiddenCombo {
  sentence_id: string;
  style_prompt_version: string;
  source_set_id: string;
  model: string;
  hidden_at: string;
  version: number;
}

export interface TranslationCandidateCard {
  kind: "translation";
  name: RankingTranslationName;
  verse_range: string;
  text: string;
}

export interface ClaudeCandidateCard {
  kind: "claude";
  candidate: CandidateResponse;
}

export type AvailableCandidate =
  | TranslationCandidateCard
  | ClaudeCandidateCard;

export interface RankingResponse {
  sentence_id: string;
  version: number;
  notes?: string | null;
  entries: RankingEntry[];
  available_candidates: AvailableCandidate[];
  hidden_combos: HiddenCombo[];
}

export interface RankingWriteRequest {
  entries: RankingEntry[];
  notes: string | null;
}

export interface HideComboRequest {
  style_prompt_version: string;
  source_set_id: string;
  model: string;
}

export interface TieBreakRequest {
  winner: CandidateRef;
  tied_against: CandidateRef[];
}

export interface TieBreakResponse {
  sentence_id: string;
  resolved_at: string;
  winner: CandidateRef;
  tied_against: CandidateRef[];
  reason: string;
  version: number;
}

// ---------------------------------------------------------------------------
// GSV (Slice 5) — mirrors api/gsv/schemas.py
// ---------------------------------------------------------------------------

export interface TranslationWinner {
  kind: "translation";
  name: RankingTranslationName;
  verse_range: string;
}

export interface ClaudeWinner {
  kind: "claude";
  candidate_id: number;
  style_prompt_version: string;
  source_set_id: SourceSetId;
  model: string;
  generated_at: string;
}

export type SingleWinner = TranslationWinner | ClaudeWinner;

export interface GsvTranslationProvenance {
  kind: "translation";
  name: RankingTranslationName;
  verse_range: string;
}

export interface GsvClaudeProvenance {
  kind: "claude";
  candidate_id: number;
  style_prompt_version: string;
  source_set_id: SourceSetId;
  model: string;
  generated_at: string;
}

export interface GsvTieProvenance {
  kind: "tie";
  entries: SingleWinner[];
}

export interface GsvTieBrokenProvenance {
  kind: "tie-broken";
  winner: SingleWinner;
  tied_against: SingleWinner[];
  resolved_at: string;
}

export interface GsvUnrankedProvenance {
  kind: "unranked";
}

export type GsvProvenance =
  | GsvTranslationProvenance
  | GsvClaudeProvenance
  | GsvTieProvenance
  | GsvTieBrokenProvenance
  | GsvUnrankedProvenance;

export interface GsvSentence {
  sentence_id: string;
  chapter: number;
  ordinal_in_chapter: number;
  verse_range: string;
  is_red_letter: boolean;
  text: string;
  provenance: GsvProvenance;
}

export interface GsvCoverage {
  chapter: number;
  total_sentences: number;
  total_red_letter_sentences: number;
  ranked_red_letter_sentences: number;
  unresolved_ties: number;
}

export interface GsvChapterResponse {
  chapter: number;
  coverage: GsvCoverage;
  sentences: GsvSentence[];
}

export interface GsvPerChapterCoverage {
  chapter: number;
  total_sentences: number;
  total_red_letter_sentences: number;
  ranked_red_letter_sentences: number;
  unresolved_ties: number;
}

export interface GsvCoverageResponse {
  ranked_sentences: number;
  total_red_letter_sentences: number;
  ch5_ranked: number;
  ch5_total: number;
  per_chapter: GsvPerChapterCoverage[];
}

export type GsvFormat = "json" | "text" | "markdown";

// ---------------------------------------------------------------------------
// Admin (Slice 6) — mirrors api/admin/schemas.py
// ---------------------------------------------------------------------------

export type OrphanReason =
  | "sentence_id_remapped"
  | "sentence_text_changed"
  | "sentence_removed";

export type OrphanDisposition = "delete" | "keep" | "remap";

export interface OrphanCandidate {
  candidate_id: number;
  sentence_id_old: string;
  sentence_id_new: string | null;
  reason: OrphanReason;
  style_prompt_version: string;
  source_set_id: SourceSetId;
  model: string;
  generated_at: string;
  candidate_text_excerpt: string;
  old_sentence_text_excerpt: string;
  new_sentence_text_excerpt: string | null;
  suggested_remap_sentence_id: string | null;
}

export interface OrphanRanking {
  sentence_id_old: string;
  sentence_id_new: string | null;
  reason: OrphanReason;
  ranked_candidate_count: number;
  has_notes: boolean;
  has_tie_break: boolean;
  version: number;
  suggested_remap_sentence_id: string | null;
}

export interface OrphanOverlay {
  overlay_id: number;
  range_start_sentence_id_old: string;
  range_end_sentence_id_old: string;
  reason: OrphanReason;
  origin: "berean" | "manual";
  rejected: boolean;
  suggested_remap_start_sentence_id: string | null;
  suggested_remap_end_sentence_id: string | null;
}

export interface OrphanSummary {
  affected_candidates: OrphanCandidate[];
  affected_rankings: OrphanRanking[];
  affected_overlays: OrphanOverlay[];
  total: number;
}

export interface ReimportRequest {
  force: boolean;
  dispositions?: Record<string, OrphanDisposition> | null;
}

export interface ReimportResponse {
  fixture_version: string;
  files_imported: number;
  sentences_built: number;
  words_built: number;
  byzantine_verses: number;
  english_verses: number;
  bib_interlinear_words: number;
  red_letter_source_ranges: number;
  style_prompts: number;
  elapsed_ms: number;
}

export interface BuildStaticRequest {
  include_unranked_placeholders: boolean;
}

export interface BuildStaticCoverage {
  ranked: number;
  total: number;
  ch5_ranked: number;
  ch5_total: number;
}

export interface BuildStaticResponse {
  dist_path: string;
  files_written: number;
  coverage: BuildStaticCoverage;
  took_ms: number;
}

// ---------------------------------------------------------------------------
// Chapter summaries (Slice 8) — mirrors api/chapters/schemas.py
// ---------------------------------------------------------------------------

export interface ChapterSummaryIntertext {
  reference?: string | null;
  type?: string | null;
  note?: string | null;
}

export interface ChapterSummaryKeySentence {
  sentence_id?: string | null;
  verse_range?: string | null;
  why_pivotal?: string | null;
}

export interface ChapterSummaryStructured {
  summary?: string | null;
  narrative_arc?: string | null;
  audience_dynamics?: string | null;
  cultural_throughline?: string | null;
  rhetorical_strategy?: string | null;
  key_intertexts?: ChapterSummaryIntertext[];
  pragmatic_arc?: string | null;
  key_sentences?: ChapterSummaryKeySentence[];
  open_questions?: string | null;
  candidate_ids_consulted?: number[];
}

export interface ChapterSummaryResponse {
  summary_id: number;
  chapter: number;
  prompt_version: string;
  source_set_id: string;
  model: string;
  source_snapshot_hash: string;
  summary: ChapterSummaryStructured;
  raw_summary_text: string;
  generated_at: string;
  run_id?: string | null;
  candidate_ids_consulted: number[];
  hidden_bool: boolean;
}

export interface ChapterSummaryListResponse {
  chapter: number;
  summaries: ChapterSummaryResponse[];
}

export interface CreateChapterSummaryRunRequest {
  scope: { kind: "chapter_summary"; chapter: number };
  style_prompt_version: "chapter-summary-v1";
  source_set_id: "CHAPTER_BUNDLE";
  model: string;
  effort?: string;
}
