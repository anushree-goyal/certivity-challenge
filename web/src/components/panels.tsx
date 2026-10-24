import { api } from "../api";
import type { RunView } from "../types";
import { StatusBadge } from "./badges";

/** Renders any contract value readably; the nested records' exact fields are owned by contracts.py. */
export function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">none</span>;
    if (value.every((v) => typeof v !== "object" || v === null)) return <>{value.map(String).join(", ")}</>;
    return <ul className="plain">{value.map((v, i) => <li key={i}><Value value={v} /></li>)}</ul>;
  }
  if (typeof value === "object") {
    return (
      <dl className="kv">
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <div key={k} className="kvrow"><dt>{k.replace(/_/g, " ")}</dt><dd><Value value={v} /></dd></div>
        ))}
      </dl>
    );
  }
  return <>{String(value)}</>;
}

export function ProfileView({ view, runId }: { view: RunView; runId: string }) {
  const profile = view.product_profile ?? {};
  const { unknowns, ...facts } = profile as Record<string, unknown>;
  return (
    <div className="split">
      <section className="card">
        <h3>Product facts</h3>
        <Value value={facts} />
        <h3>Unknowns</h3>
        <Value value={unknowns} />
      </section>
      <section className="card">
        <h3>Input</h3>
        {view.run.spec_file && <a href={api.inputUrl(runId, view.run.spec_file)} target="_blank" rel="noreferrer">Open specification</a>}
        {(view.run.drawing_files ?? []).map((d) => (
          <img key={d} className="drawing" alt={`Exploded view ${d}`} src={api.inputUrl(runId, d)} />
        ))}
        <p className="muted small">Input hash {view.run.input_hash}</p>
      </section>
    </div>
  );
}

export function ApplicabilityView({ view }: { view: RunView }) {
  const order = ["applicable", "uncertain", "excluded"];
  const sorted = [...view.applicability_decisions].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return (
    <section className="card">
      <table>
        <thead><tr><th>Regulation</th><th>Version</th><th>Decision</th><th>Explanation</th><th>Conditions</th></tr></thead>
        <tbody>
          {sorted.map((d, i) => (
            <tr key={i}>
              <td>{d.regulation}</td><td>{d.version_label ?? "—"}</td><td><StatusBadge status={d.status} /></td>
              <td>{d.explanation}</td><td><Value value={d.conditions} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function CoverageView({ view }: { view: RunView }) {
  const { counts, items } = view.coverage;
  return (
    <section className="card">
      <p>{Object.entries(counts).map(([k, v]) => <span key={k} className="countchip"><StatusBadge status={k} /> {v}</span>)}</p>
      <table>
        <thead><tr><th>Document</th><th>Section</th><th>Status</th><th>Explanation</th><th>Passages</th></tr></thead>
        <tbody>
          {items.map((c, i) => (
            <tr key={i}>
              <td>{c.document_id}</td><td>{c.section_id}</td><td><StatusBadge status={c.status} /></td>
              <td>{c.explanation}</td><td>{c.inspected_passage_ids?.length ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function FindingsView({ view }: { view: RunView }) {
  return (
    <div className="split">
      <section className="card">
        <h3>Review findings</h3>
        {view.findings.length === 0 && <p className="muted">No findings.</p>}
        {view.findings.map((f, i) => (
          <div key={i} className="finding">
            <strong>{f.category}</strong> <span className="muted small">{f.draft_id ?? f.section_id}</span>
            <p>{f.explanation}</p>
          </div>
        ))}
      </section>
      <section className="card">
        <h3>Remaining issues</h3>
        <Value value={view.remaining_issues} />
        <h3>Unresolved references</h3>
        <Value value={view.unresolved_references} />
        <h3>Export: blocking issues</h3>
        <Value value={view.export_blocking_issues} />
        <h3>Export: excluded drafts</h3>
        <Value value={view.export_excluded} />
        <h3>Pipeline warnings</h3>
        <Value value={view.pipeline_warnings} />
      </section>
    </div>
  );
}

export function ValidationView({ view }: { view: RunView }) {
  const v = view.validation;
  return (
    <div className="split">
      <section className="card">
        <h3>Official validator</h3>
        {!v ? <p className="muted">Not run.</p> : v.valid ? <p className="ok">Valid: passes participant tools/validate.py.</p> : (
          <ul className="plain">{v.errors.map((e, i) => <li key={i} className="error"><code>{e.path}</code> {e.message}</li>)}</ul>
        )}
        <h3>Usage</h3>
        <UsageSummary usage={view.usage} startedAt={view.run.started_at} finishedAt={view.run.finished_at} />
      </section>
      <section className="card">
        <h3>Local metrics (regulation / page level only)</h3>
        {view.evaluation ? <Value value={view.evaluation} /> : <p className="muted">Not measured for this run.</p>}
        <h3>Platform metrics</h3>
        {view.platform_metrics ? <Value value={view.platform_metrics} /> : <p className="muted">Not entered. Semantic requirement scores come only from the organizer platform.</p>}
      </section>
    </div>
  );
}

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? `${h} h ${m % 60} min` : `${m} min ${s % 60} s`;
}

/** Model usage. Total model time is summed across calls (which run in parallel), so it is not elapsed time;
 *  elapsed time comes from the run's own start and finish timestamps. Cache hits add no model time. */
function UsageSummary({ usage, startedAt, finishedAt }: {
  usage: Record<string, unknown> | null; startedAt?: string | null; finishedAt?: string | null;
}) {
  if (!usage) return <p className="muted">No usage recorded.</p>;
  const u = usage as { calls?: number; cached_calls?: number; input_tokens?: number; output_tokens?: number;
    latency_ms_total?: number; by_model?: Record<string, number> };
  const elapsed = startedAt && finishedAt ? Date.parse(finishedAt) - Date.parse(startedAt) : null;
  const n = (v?: number) => (typeof v === "number" ? v.toLocaleString() : "—");
  return (
    <dl className="kv">
      <dt>Model calls</dt><dd>{n(u.calls)}{u.cached_calls ? ` (${n(u.cached_calls)} from cache)` : ""}</dd>
      <dt>Tokens</dt><dd>{n(u.input_tokens)} in · {n(u.output_tokens)} out</dd>
      {typeof u.latency_ms_total === "number" && u.latency_ms_total > 0 && (<>
        <dt>Total model time</dt>
        <dd>{formatDuration(u.latency_ms_total)}
          <div className="muted small">Summed across calls running in parallel, so it can exceed the elapsed time.
            {u.cached_calls ? " Cache hits add no model time." : ""}</div></dd>
      </>)}
      {elapsed !== null && elapsed >= 0 && (<><dt>Elapsed (wall clock)</dt><dd>{formatDuration(elapsed)}</dd></>)}
      {u.by_model && <><dt>Models</dt><dd>{Object.entries(u.by_model).map(([m, c]) => `${m} × ${c}`).join(", ")}</dd></>}
    </dl>
  );
}
