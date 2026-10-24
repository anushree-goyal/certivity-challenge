import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { RegulationRow, RegulationsReport } from "../types";

export function regulationAnchor(regulation: string) {
  return "reg-" + regulation.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

const PDF_LABEL: Record<RegulationRow["pdf"], [string, string]> = {
  verified: ["green", "Downloaded"], missing: ["attention", "Missing"], "checksum mismatch": ["error", "Checksum mismatch"],
};

/** Library of every regulation in the participant package's index, with local download and corpus status. */
export function RegulationsPage({ focus }: { focus: string | null }) {
  const [report, setReport] = useState<RegulationsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "in" | "missing">("all");
  const focused = useRef<HTMLTableRowElement>(null);

  useEffect(() => { api.regulations().then(setReport).catch((e) => setError(String(e.message))); }, []);
  useEffect(() => {
    if (focus && report) {
      setSearch(""); setFilter("all");
      window.setTimeout(() => focused.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
    }
  }, [focus, report]);

  const rows = useMemo(() => (report?.regulations ?? []).filter((r) => {
    if (filter === "in" && !r.in_corpus) return false;
    if (filter === "missing" && r.in_corpus) return false;
    const q = search.trim().toLowerCase();
    return !q || `${r.regulation} ${r.title ?? ""}`.toLowerCase().includes(q);
  }), [report, search, filter]);

  if (error) return <div className="banner error">{error}</div>;
  if (!report) return <p className="muted">Loading regulations…</p>;
  const s = report.summary;

  return (
    <div>
      <div className="crumbs"><span>Regulations</span></div>
      <div className="head"><h1>Regulations</h1></div>
      <p className="reg-summary">
        <strong>{s.in_index}</strong> regulations in index · <strong>{s.downloaded}</strong> downloaded · <strong>{s.imported}</strong> imported
        {!s.corpus_ready && <span className="chip attention">Corpus not ready</span>}
      </p>
      <section className="card table-card">
        <div className="filters reg-filters">
          <input placeholder="Search regulation or title" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search regulations" />
          <div className="segmented" role="group" aria-label="Filter">
            {([["all", "All"], ["in", "In corpus"], ["missing", "Missing"]] as const).map(([v, label]) => (
              <button key={v} type="button" className={filter === v ? "on approved" : ""} aria-pressed={filter === v}
                      onClick={() => setFilter(v)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="table-scroll reg-scroll">
          <table>
            <thead>
              <tr><th>Regulation</th><th>Title</th><th>Base version</th><th>Latest version</th><th>PDF</th><th>In corpus</th><th>Pages</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isFocus = focus === r.regulation;
                const [cls, label] = PDF_LABEL[r.pdf];
                return (
                  <tr key={r.regulation} id={regulationAnchor(r.regulation)} ref={isFocus ? focused : undefined}
                      className={isFocus ? "reg-focus" : undefined}>
                    <td className="req-id">{r.regulation}</td>
                    <td>{r.title ?? "—"}
                      {r.source_url && <> <a className="small" href={r.source_url} target="_blank" rel="noreferrer">UNECE source ↗</a></>}</td>
                    <td className="mono">{r.base_version ?? "—"}</td>
                    <td className="mono">{r.latest_version ?? "—"}</td>
                    <td><span className={`chip ${cls}`}>{label}</span></td>
                    <td>{r.in_corpus ? <span className="chip green">Imported</span> : <span className="chip outline">No</span>}</td>
                    <td className="mono">{r.page_count ?? "—"}</td>
                    <td>{r.in_corpus && r.document_id
                      ? <a href={api.pdfUrl(r.document_id, 0, "real", null)} target="_blank" rel="noreferrer">Open PDF</a>
                      : <span className="muted small">—</span>}</td>
                  </tr>
                );
              })}
              {rows.length === 0 && <tr><td colSpan={8} className="muted">No regulations match.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="table-foot">{rows.length} of {report.regulations.length} shown · PDFs open from the local copy of the supplied package</div>
      </section>
    </div>
  );
}
