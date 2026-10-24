// Mirrors the RunView assembled in src/certivity/api/pipeline.py (build_run_view).
// Nested records (profile, decisions, findings, passages) follow certivity.contracts; replace these
// hand-written types with ones generated from the backend schema once contracts are published.

export type RunState = "queued" | "running" | "completed" | "partial" | "failed";
export type Origin = "fresh" | "cached" | "fixture";

export interface RunError { stage?: string; message: string }
export interface Progress { stage?: string; message?: string; completed?: number | null; total?: number | null }
export interface RunEvent extends Progress { timestamp?: string; level?: string; [k: string]: unknown }

export interface RunSummary {
  run_id: string; state: RunState; stage: string; origin: Origin; mode: string;
  team_id: string; case_id: string; created_at: string; finished_at: string | null;
  submission_valid?: boolean | null; cached_from?: string | null; interrupted?: boolean;
}

export interface RunStatus extends RunSummary {
  progress?: Progress; warnings: string[]; errors: RunError[]; finished: boolean;
  revision_rounds?: number; corpus_snapshot_id?: string | null; events: RunEvent[]; started_at?: string | null;
  cancel_requested?: boolean; cancelled?: boolean;
}

export interface CitationDiff { both: number; only_a: string[]; only_b: string[] }
export interface ComparisonSide {
  run: Pick<RunSummary, "run_id" | "state" | "origin" | "case_id" | "team_id" | "created_at" | "submission_valid">;
  requirements: number; evaluation: Record<string, unknown> | null;
}
export interface RunComparison {
  a: ComparisonSide; b: ComparisonSide;
  regulations: CitationDiff; pages: CitationDiff; paragraphs: CitationDiff; note: string;
}

export interface Source { regulation: string | null; version: string | null; page: number | null; paragraph?: string | null }

export interface Finding {
  draft_id?: string | null; section_id?: string | null; category?: string; explanation?: string;
  supporting_passage_ids?: string[]; [k: string]: unknown;
}

export interface Requirement {
  draft_id: string; requirement_id: string | null;
  status: "accepted" | "duplicate" | "rejected" | "excluded" | "pending";
  text: string; source: Source;
  applicability_condition?: string | null; applicability_reason?: string | null;
  primary_passage_id?: string | null; supporting_passage_ids: string[];
  unresolved_references: unknown[]; duplicate_of?: string | null; rejection_reason?: string | null;
  exclusion_reason?: string | null;
  findings: Finding[];
  source_link: { document_id: string; pdf_page_index: number; printed_page: number | null } | null;
}

export interface Passage {
  passage_id: string; document_id: string; section_id?: string; text: string;
  paragraph_label?: string | null; heading_ancestry?: string[]; printed_start_page?: number | null;
  page_span?: number[]; pdf_page_indices?: number[]; parsing_flags?: string[];
}

export interface DocumentRecord {
  document_id: string; regulation: string; title?: string; role?: string;
  version_label: string; filename?: string; page_count?: number;
}

export interface Decision {
  regulation: string; version_label?: string | null; document_ids?: string[];
  status: "applicable" | "uncertain" | "excluded" | string;
  scope_evidence?: unknown[]; conditions?: unknown[]; explanation?: string;
}

export interface CoverageItem {
  document_id?: string; section_id?: string; status: string; explanation?: string; inspected_passage_ids?: string[];
}

export interface ValidationReport { valid: boolean; errors: { path: string; message: string }[]; [k: string]: unknown }

export interface RunView {
  run: RunStatus & { input_hash?: string; llm_identity?: string; spec_file?: string | null; drawing_files?: string[] };
  product_profile: Record<string, unknown> | null;
  applicability_decisions: Decision[];
  requirements: Requirement[];
  passages: Record<string, Passage>;
  documents: Record<string, DocumentRecord>;
  coverage: { items: CoverageItem[]; counts: Record<string, number> };
  findings: Finding[];
  remaining_issues: unknown[];
  review_complete: boolean;
  export_excluded: unknown[];
  export_blocking_issues: unknown[];
  unresolved_references: unknown[];
  pipeline_warnings: string[];
  view_warnings: string[];
  usage: Record<string, unknown> | null;
  validation: ValidationReport | null;
  evaluation: Record<string, unknown> | null;
  platform_metrics: Record<string, unknown> | null;
  engineer_reviews: Record<string, EngineerReview>;
  submission: { available: boolean; requirement_count: number };
}

export type EngineerReviewStatus = "unreviewed" | "approved" | "flagged";
export interface EngineerReview { status: EngineerReviewStatus; note: string; updated_at: string }

export interface CaseInfo {
  case_id: string; type: "main" | "subgoal"; held_out: boolean;
  spec_file: string | null; spec_files: string[]; drawings: string[]; has_spec: boolean;
}

export interface RegulationRow {
  regulation: string; title: string | null; base_version: string | null; latest_version: string | null;
  pdf: "verified" | "missing" | "checksum mismatch"; downloaded: boolean; verified: boolean; in_corpus: boolean;
  page_count: number | null; document_id: string | null; source_url: string | null;
  files: { name: string; present: boolean; verified: boolean }[];
}
export interface RegulationsReport {
  summary: { in_index: number; downloaded: number; imported: number; corpus_ready: boolean };
  regulations: RegulationRow[];
}
export interface CorpusFreshness {
  needs_import: boolean; reasons: string[]; not_imported: string[]; no_longer_listed: string[];
  missing_pdfs: string[]; checksum_mismatch: string[]; index_entries: number; verified_pdfs: number;
}
export interface ReimportStatus { state: "idle" | "running" | "done" | "failed"; started_at?: string; finished_at?: string; snapshot_id?: string | null; error?: string | null }

export interface Health {
  app: { ok: boolean; version: string; active_run: string | null; queued_runs: number };
  pipeline_mode: string;
  participant_package: { ready: boolean; cases: string[] };
  modules: Record<string, { ready: boolean; error?: string; required?: boolean }>;
  corpus: { ready: boolean; snapshot_id?: string | null; base_documents?: number; error?: string };
  llm: { ready: boolean; identity?: string | null; error?: string };
  real_pipeline_ready: boolean;
  corpus_freshness?: CorpusFreshness;
  corpus_reimport?: ReimportStatus;
}
