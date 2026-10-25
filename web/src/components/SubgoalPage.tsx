import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { usePoller } from "../usePoller";
import type { SubgoalCase, SubgoalChange, SubgoalResult } from "../types";
import { Value } from "./panels";

const CHANGE: Record<SubgoalChange["change"], [string, string]> = {
  modified: ["attention", "Modified"], removed: ["error", "Removed"], added: ["green", "Added"],
};

/** Sub-goal: which requirements derived from an old regulation version change in the new one, and why. */
export function SubgoalPage() {
  const [cases, setCases] = useState<SubgoalCase[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<SubgoalResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"" | SubgoalChange["change"]>("");
  const [search, setSearch] = useState("");

  const loadCases = useCallback(() => api.subgoalCases().then((all) => {
    setCases(all);
    setSelected((s) => s ?? all[0]?.case ?? null);
  }).catch((e) => setError(String(e.message))), []);
  const loadResult = useCallback(() => {
    if (!selected) return;
    api.subgoalResult(selected).then((r) => { setResult(r); setError(null); }).catch(() => setResult(null));
  }, [selected]);

  useEffect(() => { loadCases(); }, [loadCases]);
  useEffect(() => { setResult(null); loadResult(); }, [loadResult]);

  const current = cases.find((c) => c.case === selected) ?? null;
  const running = current?.job.state === "running";
  // While a run is going, refresh every 2 s; when it finishes, load its result.
  usePoller(() => api.subgoalCases().then((all) => {
    setCases(all);
    if (all.find((c) => c.case === selected)?.job.state !== "running") loadResult();
  }).catch(() => undefined), 2000, running);

  const start = () => {
    if (!selected) return;
    setError(null);
    api.runSubgoal(selected).then(() => loadCases()).catch((e) => setError(String(e.message)));
  };

  const rows = useMemo(() => (result?.changes ?? []).filter((c) => {
    if (filter && c.change !== filter) return false;
    const q = search.trim().toLowerCase();
    const old = c.requirement_id ? result?.old_requirements[c.requirement_id]?.text ?? "" : "";
    return !q || `${c.requirement_id ?? ""} ${c.new_text ?? ""} ${old} ${c.reason}`.toLowerCase().includes(q);
  }), [result, filter, search]);

  const counts = { modified: 0, removed: 0, added: 0 };
  result?.changes.forEach((c) => { counts[c.change] += 1; });

  return (
    <div>
      <div className="crumbs"><span>Sub-goal</span>{current && <span>{current.case_id}</span>}</div>
      <div className="head"><h1>Requirement changes between versions</h1></div>
      <p className="hint">For requirements derived from an older version of a UN Regulation: which must change in the new
        version, and what in the regulation caused it. Only paragraphs whose text changed are sent to the model.</p>

      <section className="card">
        <div className="row-flex">
          <label style={{ flex: 1 }}>Case
            <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
              {cases.map((c) => (
                <option key={c.case} value={c.case}>
                  {c.held_out ? "Held-out: " : ""}{c.case_id} · {c.regulation} · {c.requirements_old} old requirements
                </option>
              ))}
            </select>
          </label>
          <span className="spacer" />
          <button type="button" onClick={start} disabled={!current || !current.ready || running} aria-busy={running}>
            {running ? "Running…" : result ? "Run again" : "Run"}
          </button>
          {result && result.validation.valid && (
            <a className="button" href={api.subgoalSubmissionUrl(result.case)} download>Export changes JSON</a>
          )}
        </div>
        {current && !current.ready && <div className="banner">The old/new PDFs are not downloaded yet: run <code>certivity fetch</code>.</div>}
        {running && <p className="muted small">{current?.job.message ?? "Running"}… about a minute.</p>}
        {current?.job.state === "failed" && <div className="banner error">{current.job.error}</div>}
        {error && <div className="banner error">{error}</div>}
        {current?.held_out && <p className="hint">Held-out: only your last valid upload counts.</p>}
      </section>

      {result && (
        <>
          <div className="stats">
            <Stat label="Modified" value={counts.modified} sub={`of ${result.requirements_old} old requirements`} />
            <Stat label="Removed" value={counts.removed} sub="no longer in the new version" />
            <Stat label="Added" value={counts.added} sub="from new paragraphs" />
            <Stat label="Official validator" value={result.validation.valid ? "Valid" : "Invalid"}
                  sub={`${result.regulation} ${result.old_version} → ${result.new_version}`} />
          </div>
          {!result.validation.valid && (
            <ul className="plain">{result.validation.errors.map((e, i) => <li key={i} className="error"><code>{e.path}</code> {e.message}</li>)}</ul>
          )}
          {result.evaluation && (
            <section className="card">
              <h3>Against the worked example's expected answer</h3>
              <Value value={result.evaluation} />
            </section>
          )}

          <section className="card table-card">
            <div className="filters">
              <input placeholder="Search id, text or reason" value={search} onChange={(e) => setSearch(e.target.value)} />
              <div className="segmented" role="group" aria-label="Change type">
                {([["", "All"], ["modified", "Modified"], ["removed", "Removed"], ["added", "Added"]] as const).map(([v, label]) => (
                  <button key={v} type="button" className={filter === v ? "on approved" : ""} aria-pressed={filter === v}
                          onClick={() => setFilter(v)}>{label}</button>
                ))}
              </div>
            </div>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Requirement</th><th>Change</th><th>Old text</th><th>New text</th><th>Reason</th><th>New source</th></tr></thead>
                <tbody>
                  {rows.map((c, i) => {
                    const [cls, label] = CHANGE[c.change];
                    const old = c.requirement_id ? result.old_requirements[c.requirement_id] : undefined;
                    return (
                      <tr key={`${c.requirement_id}-${i}`}>
                        <td className="req-id">{c.requirement_id ?? "new"}</td>
                        <td><span className={`chip ${cls}`}>{label}</span></td>
                        <td className="small">{old?.text ?? <span className="muted">—</span>}</td>
                        <td className="small">{c.new_text ?? <span className="muted">—</span>}</td>
                        <td className="small">{c.reason}</td>
                        <td className="cite">{c.source ? <>p. {c.source.page}<br />¶ {c.source.paragraph}</> : "—"}</td>
                      </tr>
                    );
                  })}
                  {rows.length === 0 && <tr><td colSpan={6} className="muted">No changes match.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="table-foot">{rows.length} of {result.changes.length} changes · run {result.finished_at}</div>
          </section>
        </>
      )}
      {!result && current && !running && current.ready && (
        <section className="card muted">Not run yet for {current.case_id}. Press Run.</section>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: number | string; sub: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{sub}</div>
    </div>
  );
}
