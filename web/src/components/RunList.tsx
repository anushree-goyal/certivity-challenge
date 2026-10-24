import type { RunSummary } from "../types";
import { OriginBadge, StateBadge } from "./badges";

export function RunList({ runs, selected, onSelect }: { runs: RunSummary[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="card runlist">
      <h2>Runs</h2>
      {runs.length === 0 && <p className="muted">No runs yet.</p>}
      <ul>
        {runs.map((r) => (
          <li key={r.run_id} className={r.run_id === selected ? "active" : ""} onClick={() => onSelect(r.run_id)}>
            <div className="runline"><strong>{r.case_id}</strong> <StateBadge state={r.state} /> <OriginBadge origin={r.origin} /></div>
            <div className="muted small">{r.run_id}{r.interrupted ? " · interrupted" : ""}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
