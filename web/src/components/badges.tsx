import type { EngineerReview, Origin, Requirement, RunState } from "../types";

// Wording rule: pipeline acceptance is "Source-checked", never "Verified". Only an engineer approves.

const STATE: Record<RunState, [string, string]> = {
  completed: ["green", "Completed"], partial: ["attention", "Partial"], failed: ["error", "Failed"],
  running: ["outline", "Running"], queued: ["outline", "Queued"],
};

export function StateBadge({ state }: { state: RunState }) {
  const [cls, label] = STATE[state] ?? ["outline", state];
  return <span className={`chip ${cls}`}>{label}</span>;
}

const ORIGIN: Record<Origin, [string, string]> = {
  fresh: ["outline", "Fresh processing"], cached: ["neutral", "Cached result"], fixture: ["attention", "FIXTURE DATA"],
};

export function OriginBadge({ origin }: { origin: Origin }) {
  const [cls, label] = ORIGIN[origin] ?? ["outline", origin];
  return <span className={`chip ${cls}`}>{label}</span>;
}

const GREEN = new Set(["applicable", "processed", "resolved", "accepted"]);
const ATTENTION = new Set(["uncertain", "pending", "ambiguous", "missing"]);
const NEUTRAL = new Set(["excluded", "duplicate", "outside-corpus"]);
const ERROR = new Set(["failed", "rejected"]);

export function StatusBadge({ status }: { status: string }) {
  const cls = GREEN.has(status) ? "green" : ATTENTION.has(status) ? "attention" : NEUTRAL.has(status) ? "neutral"
    : ERROR.has(status) ? "error" : "outline";
  return <span className={`chip ${cls}`}>{status}</span>;
}

export function PipelineChip({ req, reviewComplete = true }: { req: Requirement; reviewComplete?: boolean }) {
  if (req.status === "accepted" && !reviewComplete) return <span className="chip outline" title="Exported, but the automated evidence review did not run for this analysis">Not reviewed</span>;
  if (req.status === "accepted") return <span className="chip green" title="Accepted by the automated evidence review">Source-checked</span>;
  if (req.status === "duplicate") return <span className="chip neutral">Duplicate{req.duplicate_of ? ` of ${req.duplicate_of}` : ""}</span>;
  if (req.status === "rejected") return <span className="chip outline" title={req.rejection_reason ?? undefined}>Rejected</span>;
  if (req.status === "excluded") return <span className="chip neutral" title={req.exclusion_reason ?? undefined}>Not exported</span>;
  return <span className="chip attention">Needs review</span>;
}

export function EngineerChip({ review }: { review?: EngineerReview }) {
  if (review?.status === "approved") return <span className="chip green-solid">Approved</span>;
  if (review?.status === "flagged") return <span className="chip attention">Flagged</span>;
  return <span className="chip outline">Unreviewed</span>;
}

/** A requirement needs an engineer's attention when review raised issues, it is unresolved, or someone flagged it. */
export function needsReview(req: Requirement, review?: EngineerReview): boolean {
  return review?.status === "flagged" || req.status === "pending" || req.findings.length > 0 || req.unresolved_references.length > 0;
}
