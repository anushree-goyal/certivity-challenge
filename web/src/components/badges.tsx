import type { Origin, RunState } from "../types";

export function StateBadge({ state }: { state: RunState }) {
  return <span className={`badge state-${state}`}>{state}</span>;
}

const ORIGIN_LABEL: Record<Origin, string> = { fresh: "fresh processing", cached: "cached result", fixture: "FIXTURE DATA" };

export function OriginBadge({ origin }: { origin: Origin }) {
  return <span className={`badge origin-${origin}`}>{ORIGIN_LABEL[origin] ?? origin}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}>{status}</span>;
}
