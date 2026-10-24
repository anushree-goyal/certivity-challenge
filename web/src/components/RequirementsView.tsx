import { useMemo, useState } from "react";
import { api } from "../api";
import type { Requirement, RunView } from "../types";
import { StatusBadge } from "./badges";
import { Value } from "./panels";

export function RequirementsView({ view }: { view: RunView }) {
  const [regulation, setRegulation] = useState("");
  const [status, setStatus] = useState("accepted");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const regulations = useMemo(
    () => Array.from(new Set(view.requirements.map((r) => r.source.regulation ?? "unknown"))).sort(),
    [view.requirements],
  );
  const filtered = view.requirements.filter((r) =>
    (!regulation || (r.source.regulation ?? "unknown") === regulation) &&
    (!status || r.status === status) &&
    (!search || `${r.text} ${r.source.paragraph ?? ""}`.toLowerCase().includes(search.toLowerCase())));
  const groups = useMemo(() => {
    const g = new Map<string, Requirement[]>();
    filtered.forEach((r) => {
      const key = `${r.source.regulation ?? "unknown"} ${r.source.version ?? ""}`.trim();
      g.set(key, [...(g.get(key) ?? []), r]);
    });
    return Array.from(g.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);
  const selected = view.requirements.find((r) => r.draft_id === selectedId) ?? filtered[0] ?? null;

  return (
    <div className="split">
      <section className="card reqlist">
        <div className="filters">
          <select value={regulation} onChange={(e) => setRegulation(e.target.value)}>
            <option value="">All regulations</option>
            {regulations.map((r) => <option key={r}>{r}</option>)}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="accepted">Accepted</option>
            <option value="duplicate">Duplicates</option>
            <option value="rejected">Rejected</option>
            <option value="pending">Pending</option>
          </select>
          <input placeholder="Search text or paragraph" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <p className="muted small">{filtered.length} of {view.requirements.length} drafts</p>
        {groups.map(([group, reqs]) => (
          <div key={group}>
            <h4 className="group">{group} <span className="muted">({reqs.length})</span></h4>
            <ul>
              {reqs.map((r) => (
                <li key={r.draft_id} className={selected?.draft_id === r.draft_id ? "active" : ""} onClick={() => setSelectedId(r.draft_id)}>
                  <div className="runline">
                    <strong>{r.requirement_id ?? r.draft_id}</strong>
                    <span className="muted small">¶ {r.source.paragraph ?? "—"} · p. {r.source.page ?? "?"}</span>
                    {r.status !== "accepted" && <StatusBadge status={r.status} />}
                    {r.findings.length > 0 && <span className="badge status-pending">{r.findings.length} issue(s)</span>}
                  </div>
                  <div className="reqtext">{r.text}</div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
      {selected ? <SourcePanel req={selected} view={view} /> : <section className="card muted">No requirement selected.</section>}
    </div>
  );
}

function SourcePanel({ req, view }: { req: Requirement; view: RunView }) {
  const mode = view.run.mode;
  const snapshot = view.run.corpus_snapshot_id;
  const link = req.source_link;
  const doc = link ? view.documents[link.document_id] : undefined;
  const supporting = req.supporting_passage_ids.map((id) => view.passages[id]).filter(Boolean);

  return (
    <section className="card source">
      <h3>{req.requirement_id ?? req.draft_id} <StatusBadge status={req.status} /></h3>
      <p className="reqtext big">{req.text}</p>
      <dl className="kv">
        <dt>Citation</dt>
        <dd>{req.source.regulation} {req.source.version}, page {req.source.page ?? "?"}{req.source.paragraph ? `, ¶ ${req.source.paragraph}` : ""}</dd>
        {req.applicability_condition && <><dt>Condition</dt><dd>{req.applicability_condition}</dd></>}
        {req.applicability_reason && <><dt>Why it applies</dt><dd>{req.applicability_reason}</dd></>}
        {req.duplicate_of && <><dt>Duplicate of</dt><dd>{req.duplicate_of}</dd></>}
        {req.rejection_reason && <><dt>Rejected</dt><dd>{req.rejection_reason}</dd></>}
        {req.unresolved_references.length > 0 && <><dt>Unresolved refs</dt><dd><Value value={req.unresolved_references} /></dd></>}
      </dl>
      {req.findings.map((f, i) => (
        <p key={i} className="warn"><strong>{f.category}:</strong> {f.explanation}</p>
      ))}

      <h4>Evidence</h4>
      {supporting.length === 0 && <p className="muted">No passage text captured for this draft.</p>}
      {supporting.map((p) => (
        <blockquote key={p.passage_id} className={p.passage_id === req.primary_passage_id ? "primary" : ""}>
          <div className="muted small">
            {p.passage_id === req.primary_passage_id ? "Primary · " : ""}
            ¶ {p.paragraph_label ?? "—"} · printed p. {p.printed_start_page ?? "?"} · PDF page {p.pdf_page_indices?.map((i) => i + 1).join(", ")}
            {p.heading_ancestry?.length ? ` · ${p.heading_ancestry.join(" › ")}` : ""}
            {p.parsing_flags?.length ? ` · flags: ${p.parsing_flags.join(", ")}` : ""}
          </div>
          {p.text}
        </blockquote>
      ))}

      {link ? (
        <>
          <div className="runline">
            <h4>Source page</h4>
            <span className="muted small">
              {doc?.regulation} {doc?.version_label} · printed page {link.printed_page ?? "?"} = PDF page {link.pdf_page_index + 1}
            </span>
            <span className="spacer" />
            {mode !== "fixture" && (
              <a href={api.pdfUrl(link.document_id, link.pdf_page_index, mode, snapshot)} target="_blank" rel="noreferrer">
                Open supplied PDF at this page ↗
              </a>
            )}
          </div>
          <img className="page" alt={`Source page ${link.pdf_page_index + 1}`}
               src={api.pageUrl(link.document_id, link.pdf_page_index, mode, snapshot)} />
        </>
      ) : <p className="muted">No source page mapped for the primary passage.</p>}
    </section>
  );
}
