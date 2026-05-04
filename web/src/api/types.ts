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
  | { kind: "all_unranked_red_letter"; as_of?: string | null };

export interface CreateRunRequest {
  scope: RunScope;
  style_prompt_version: string;
  source_set_id: SourceSetId;
  model: string;
}

export interface RunResponse {
  run_id: string;
  status: RunStatus;
  style_prompt_version: string;
  source_set_id: SourceSetId;
  model: string;
  items_count: number;
  items_completed: number;
  items_failed: number;
  items_pending: number;
  items_running: number;
  items_cancelled: number;
  items_interrupted: number;
  estimated_cost_usd: number;
  estimated_cost_usd_band_pct: number;
  sentence_ids: string[];
  parent_run_id?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
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
