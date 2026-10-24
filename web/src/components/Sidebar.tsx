import type { Health, RunSummary } from "../types";
import { FlowButton } from "./ui/flow-button";

export function Sidebar({ runs, selected, health, healthError, onNew, onSelect, newActive }: {
  runs: RunSummary[]; selected: string | null; health: Health | null; healthError: string | null;
  onNew: () => void; onSelect: (id: string) => void; newActive: boolean;
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-name">
          <span className="brand-mark" aria-hidden>
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="#04140f" strokeWidth="2.2">
              <circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5 14 14" strokeLinecap="round" />
            </svg>
          </span>
          ProofTech
        </div>
        <div className="brand-sub">Engineering workspace</div>
      </div>

      <div className="side-section">
        <FlowButton text="New Analysis" onClick={onNew} block current={newActive} />
      </div>

      <div className="side-label" style={{ paddingLeft: "1.1rem" }}>Analyses</div>
      <div className="runs-wrap side-section" style={{ paddingTop: 0 }}>
        {runs.length === 0 && <p className="small" style={{ color: "#9fc3b2", padding: "0 .4rem" }}>No analyses yet.</p>}
        <ul className="runs">
          {runs.map((r) => (
            <li key={r.run_id} className={r.run_id === selected ? "active" : ""} onClick={() => onSelect(r.run_id)}>
              <div className="run-title">
                <span className={`dot ${r.state}`} title={r.state} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{prettyCase(r.case_id)}</span>
                {r.origin === "fixture" && <span className="side-tag fixture">fixture</span>}
                {r.origin === "cached" && <span className="side-tag cached">Cached result</span>}
              </div>
              <div className="run-meta">{shortTime(r.created_at)} · {r.state}{r.interrupted ? " · interrupted" : ""}</div>
            </li>
          ))}
        </ul>
      </div>

      <SystemStatus health={health} error={healthError} />
    </aside>
  );
}

function SystemStatus({ health, error }: { health: Health | null; error: string | null }) {
  if (error) return <div className="side-health"><div><span className="dot bad" /> API unreachable</div></div>;
  if (!health) return <div className="side-health"><div>Checking system…</div></div>;
  const rows: [boolean, string, string | undefined][] = [
    [health.participant_package.ready, `Regulation package: ${health.participant_package.ready ? "ready" : "missing"}`, undefined],
    [health.corpus.ready, health.corpus.ready ? `Corpus · ${health.corpus.base_documents ?? "?"} documents` : "Corpus not ready", health.corpus.error],
    [health.llm.ready, health.llm.ready ? `Model · ${health.llm.identity ?? "configured"}` : "Model not configured", health.llm.error],
    [health.real_pipeline_ready, health.real_pipeline_ready ? "Pipeline ready" : "Fixture mode only", undefined],
  ];
  return (
    <div className="side-health">
      {rows.map(([ok, label, title]) => <div key={label} title={title}><span className={`dot ${ok ? "ok" : "bad"}`} /> {label}</div>)}
    </div>
  );
}

export function prettyCase(caseId: string) {
  return caseId.replace(/^dev-/, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function shortTime(iso: string) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
