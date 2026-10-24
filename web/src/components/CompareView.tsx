import { useEffect, useState } from "react";
import { api } from "../api";
import type { CitationDiff, RunComparison, RunSummary } from "../types";
import { OriginBadge, StateBadge } from "./badges";
import { Value } from "./panels";

export function CompareView({ runId }: { runId: string }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [other, setOther] = useState("");
  const [result, setResult] = useState<RunComparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.runs().then((r) => setRuns(r.filter((x) => x.run_id !== runId && x.finished_at))).catch(() => undefined); }, [runId]);
  useEffect(() => {
    setResult(null); setError(null);
    if (other) api.compare(runId, other).then(setResult).catch((e) => setError(String(e.message)));
  }, [runId, other]);

  return (
    <section className="card">
      <label>Compare this run (A) with (B)
        <select value={other} onChange={(e) => setOther(e.target.value)}>
          <option value="">— choose a finished run —</option>
          {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.case_id} · {r.state} · {r.origin} · {r.run_id}</option>)}
        </select>
      </label>
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <p className="muted small">{result.note}</p>
          <table>
            <thead><tr><th></th><th>A</th><th>B</th></tr></thead>
            <tbody>
              <tr><td>Run</td>
                {[result.a, result.b].map((s) => (
                  <td key={s.run.run_id}>{s.run.run_id} <StateBadge state={s.run.state} /> <OriginBadge origin={s.run.origin} /></td>
                ))}</tr>
              <tr><td>Requirements</td><td>{result.a.requirements}</td><td>{result.b.requirements}</td></tr>
              <tr><td>Valid submission</td><td>{String(!!result.a.run.submission_valid)}</td><td>{String(!!result.b.run.submission_valid)}</td></tr>
              <tr><td>Local metrics</td><td><Value value={result.a.evaluation} /></td><td><Value value={result.b.evaluation} /></td></tr>
            </tbody>
          </table>
          <DiffBlock title="Regulations" diff={result.regulations} />
          <DiffBlock title="Cited pages (regulation version page)" diff={result.pages} />
          <DiffBlock title="Cited paragraphs" diff={result.paragraphs} />
        </>
      )}
    </section>
  );
}

function DiffBlock({ title, diff }: { title: string; diff: CitationDiff }) {
  return (
    <>
      <h3>{title}: {diff.both} shared</h3>
      <div className="split">
        <div><strong className="small">Only in A ({diff.only_a.length})</strong><Value value={diff.only_a} /></div>
        <div><strong className="small">Only in B ({diff.only_b.length})</strong><Value value={diff.only_b} /></div>
      </div>
    </>
  );
}
