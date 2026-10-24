import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { EngineerReview, EngineerReviewStatus, Requirement, RunView } from "../types";
import { EngineerChip, PipelineChip, needsReview } from "./badges";
import { Value } from "./panels";

type OnReview = (draftId: string, review: EngineerReview | null) => void;

/** Printed page label from the corpus source record, else the page in the official submission. Never derived. */
function printedPage(r: Requirement): string | number | null {
  return r.source_link?.printed_page ?? r.source.page ?? null;
}

/** The id used in permalinks: the exported requirement ID when there is one, else the draft ID. */
const linkId = (r: Requirement) => r.requirement_id ?? r.draft_id;

export function requirementPermalink(runId: string, r: Requirement) {
  return `${location.origin}${location.pathname}?run=${encodeURIComponent(runId)}&req=${encodeURIComponent(linkId(r))}`;
}

export function RequirementsView({ view, runId, onReview }: { view: RunView; runId: string; onReview: OnReview }) {
  // A permalink (?run=<id>&req=<requirement or draft id>) opens with that requirement selected.
  const linked = useMemo(() => {
    const q = new URLSearchParams(location.search);
    const req = q.get("run") === runId ? q.get("req") : null;
    return req ? view.requirements.find((r) => r.requirement_id === req || r.draft_id === req) ?? null : null;
  }, [runId, view.requirements]);
  const [regulation, setRegulation] = useState("");
  const [pipeline, setPipeline] = useState(() => (linked && !linked.requirement_id ? "" : "submission"));
  const [engineer, setEngineer] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(linked?.draft_id ?? null);
  const [focusSource, setFocusSource] = useState(0);  // bumped to scroll the detail panel to the cited page
  const reviews = view.engineer_reviews;

  const select = (draftId: string, toSource = false) => {
    setSelectedId(draftId);
    const r = view.requirements.find((x) => x.draft_id === draftId);
    if (r) history.replaceState(null, "", `?run=${encodeURIComponent(runId)}&req=${encodeURIComponent(linkId(r))}`);
    if (toSource) setFocusSource((n) => n + 1);
  };
  useEffect(() => {
    if (!linked) return;
    const t = window.setTimeout(() => {
      document.querySelector(`tr[data-draft="${CSS.escape(linked.draft_id)}"]`)?.scrollIntoView({ block: "center" });
    }, 80);
    return () => window.clearTimeout(t);
  }, [linked]);

  const regulations = useMemo(
    () => Array.from(new Set(view.requirements.map((r) => r.source.regulation ?? "Unknown"))).sort(),
    [view.requirements],
  );
  const filtered = view.requirements.filter((r) => {
    const review = reviews[r.draft_id];
    if (regulation && (r.source.regulation ?? "Unknown") !== regulation) return false;
    if (pipeline === "submission" && !r.requirement_id) return false;
    if (pipeline === "attention" && !needsReview(r, review)) return false;
    if (pipeline === "excluded" && r.requirement_id) return false;
    if (engineer && (review?.status ?? "unreviewed") !== engineer) return false;
    const q = search.trim().toLowerCase();
    return !q || `${r.requirement_id ?? ""} ${r.text} ${r.source.paragraph ?? ""}`.toLowerCase().includes(q);
  });
  const groups = useMemo(() => {
    const g = new Map<string, Requirement[]>();
    filtered.forEach((r) => {
      const key = `${r.source.regulation ?? "Unknown"} · ${r.source.version ?? "?"}`;
      g.set(key, [...(g.get(key) ?? []), r]);
    });
    return Array.from(g.entries()).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
  }, [filtered]);
  const selected = view.requirements.find((r) => r.draft_id === selectedId) ?? filtered[0] ?? null;

  return (
    <div className="workspace">
      <section className="card table-card">
        <div className="filters">
          <input placeholder="Search ID, text or paragraph" value={search} onChange={(e) => setSearch(e.target.value)} />
          <select value={regulation} onChange={(e) => setRegulation(e.target.value)} aria-label="Regulation">
            <option value="">All regulations</option>
            {regulations.map((r) => <option key={r}>{r}</option>)}
          </select>
          <select value={pipeline} onChange={(e) => setPipeline(e.target.value)} aria-label="Pipeline status">
            <option value="submission">In submission</option>
            <option value="attention">Needs review</option>
            <option value="excluded">Not in submission</option>
            <option value="">All drafts</option>
          </select>
          <select value={engineer} onChange={(e) => setEngineer(e.target.value)} aria-label="Engineer review">
            <option value="">Any review</option>
            <option value="unreviewed">Unreviewed</option>
            <option value="approved">Approved</option>
            <option value="flagged">Flagged</option>
          </select>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>ID</th><th>Requirement</th><th>Citation</th><th>Pipeline</th><th>Engineer</th></tr>
            </thead>
            <tbody>
              {groups.map(([group, reqs]) => (
                <GroupRows key={group} group={group} reqs={reqs} reviews={reviews} reviewComplete={view.review_complete}
                           selectedId={selected?.draft_id} onSelect={select} />
              ))}
              {filtered.length === 0 && <tr><td colSpan={5} className="muted">No requirements match these filters.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="table-foot">{filtered.length} of {view.requirements.length} drafts shown</div>
      </section>
      {selected
        ? <SourcePanel key={selected.draft_id} req={selected} view={view} runId={runId} onReview={onReview} focusSource={focusSource} />
        : <section className="card muted">Select a requirement to see its source evidence.</section>}
    </div>
  );
}

function GroupRows({ group, reqs, reviews, reviewComplete, selectedId, onSelect }: {
  group: string; reqs: Requirement[]; reviews: Record<string, EngineerReview>; reviewComplete: boolean;
  selectedId?: string; onSelect: (id: string, toSource?: boolean) => void;
}) {
  return (
    <>
      <tr className="group"><td colSpan={5}>{group} <span className="muted">({reqs.length})</span></td></tr>
      {reqs.map((r) => {
        const review = reviews[r.draft_id];
        const cls = ["req", r.draft_id === selectedId && "selected", needsReview(r, review) && "attention"].filter(Boolean).join(" ");
        return (
          <tr key={r.draft_id} data-draft={r.draft_id} className={cls} onClick={() => onSelect(r.draft_id)} tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(r.draft_id); } }}>
            <td className="req-id">{r.requirement_id ?? r.draft_id}</td>
            <td><div className="req-text">{r.text}</div></td>
            <td className="cite">
              <button type="button" className="cite-link" title="Jump to the cited source"
                      onClick={(e) => { e.stopPropagation(); onSelect(r.draft_id, true); }}>
                p. {printedPage(r) ?? "?"}<br />¶ {r.source.paragraph ?? "—"}
              </button>
            </td>
            <td>
              <PipelineChip req={r} reviewComplete={reviewComplete} />
              {r.findings.length > 0 && <div style={{ marginTop: 3 }}><span className="chip attention">{r.findings.length} issue{r.findings.length > 1 ? "s" : ""}</span></div>}
            </td>
            <td><EngineerChip review={review} /></td>
          </tr>
        );
      })}
    </>
  );
}

/** A few words of the passage (after its paragraph label) for the PDF viewer's search hint. */
function searchHint(text: string | undefined) {
  if (!text) return undefined;
  const words = text.replace(/^\s*(annex\s+\S+\s+)?[\d.]+\s*/i, "").split(/\s+/).filter(Boolean).slice(0, 6);
  return words.length ? words.join(" ") : undefined;
}

function SourcePanel({ req, view, runId, onReview, focusSource }: {
  req: Requirement; view: RunView; runId: string; onReview: OnReview; focusSource: number;
}) {
  const mode = view.run.mode;
  const snapshot = view.run.corpus_snapshot_id;
  const link = req.source_link;
  const doc = link ? view.documents[link.document_id] : undefined;
  const supporting = req.supporting_passage_ids.map((id) => view.passages[id]).filter(Boolean);
  const primary = req.primary_passage_id ? view.passages[req.primary_passage_id] : undefined;
  const pageFrame = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (focusSource > 0) pageFrame.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusSource]);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(requirementPermalink(runId, req));
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
    window.setTimeout(() => setCopied("idle"), 2000);
  };

  return (
    <section className="card source">
      <div className="row-flex">
        <h2 className="mono">{req.requirement_id ?? req.draft_id}</h2>
        <PipelineChip req={req} reviewComplete={view.review_complete} />
        <EngineerChip review={view.engineer_reviews[req.draft_id]} />
        <span className="spacer" />
        <button type="button" className="ghost copy-link" onClick={copyLink} title={requirementPermalink(runId, req)}>
          {copied === "copied" ? "Link copied" : copied === "failed" ? "Copy failed" : "Copy link"}
        </button>
      </div>
      <p className="source-text">{req.text}</p>

      <div className="meta">
        <div><span>Regulation</span><strong>{req.source.regulation ?? "?"}</strong></div>
        <div><span>Version</span><strong>{req.source.version ?? "?"}{doc?.role === "base" ? " (base)" : ""}</strong></div>
        <div><span>Printed page</span><strong>{printedPage(req) ?? "—"}</strong></div>
        <div title={link ? `Physical page in the supplied PDF (zero-based index ${link.pdf_page_index})` : undefined}>
          <span>PDF page</span><strong>{link ? link.pdf_page_index + 1 : "—"}</strong>
        </div>
        <div><span>Paragraph</span><strong>{req.source.paragraph ?? "—"}</strong></div>
      </div>

      <dl className="kv">
        {req.applicability_condition && <><dt>Condition</dt><dd>{req.applicability_condition}</dd></>}
        {req.applicability_reason && <><dt>Why it applies</dt><dd>{req.applicability_reason}</dd></>}
        {req.duplicate_of && <><dt>Duplicate of</dt><dd className="mono">{req.duplicate_of}</dd></>}
        {req.rejection_reason && <><dt>Rejected</dt><dd>{req.rejection_reason}</dd></>}
        {req.exclusion_reason && <><dt>Not exported</dt><dd>{req.exclusion_reason}</dd></>}
        {req.unresolved_references.length > 0 && <><dt>Open references</dt><dd><Value value={req.unresolved_references} /></dd></>}
      </dl>

      {req.findings.map((f, i) => (
        <div key={i} className="finding"><strong>{f.category}</strong> {f.explanation}</div>
      ))}

      <EngineerReviewBox req={req} runId={runId} review={view.engineer_reviews[req.draft_id]} onReview={onReview} />

      <h4>Source evidence</h4>
      {supporting.length === 0 && <p className="muted small">No passage text was captured for this requirement.</p>}
      {supporting.map((p) => (
        <blockquote key={p.passage_id} className={p.passage_id === req.primary_passage_id ? "primary" : ""}>
          <div className="bq-meta">
            {p.passage_id === req.primary_passage_id ? "PRIMARY · " : ""}
            ¶ {p.paragraph_label ?? "—"} · printed p. {p.printed_start_page ?? "?"} · PDF p. {p.pdf_page_indices?.map((i) => i + 1).join(", ")}
            {p.heading_ancestry?.length ? ` · ${p.heading_ancestry.join(" › ")}` : ""}
            {p.parsing_flags?.length ? ` · flags: ${p.parsing_flags.join(", ")}` : ""}
          </div>
          {p.text}
        </blockquote>
      ))}

      <h4>Cited page</h4>
      {link ? (
        <div className="page-frame" ref={pageFrame}>
          <header>
            <strong>{doc?.regulation} {doc?.version_label}</strong>
            <span className="muted">printed page {link.printed_page ?? "?"} · PDF page {link.pdf_page_index + 1}</span>
            <span className="spacer" />
            {mode !== "fixture" && (
              <a href={api.pdfUrl(link.document_id, link.pdf_page_index, mode, snapshot, searchHint(primary?.text))}
                 target="_blank" rel="noreferrer">Open PDF at this page ↗</a>
            )}
          </header>
          {primary && (
            // Fallback until the corpus provides passage coordinates: show the cited text right above the page.
            <div className="cited-passage">
              <span className="cited-label">Cited passage · ¶ {primary.paragraph_label ?? "—"}</span>
              <p>{primary.text.length > 420 ? `${primary.text.slice(0, 420)}…` : primary.text}</p>
            </div>
          )}
          <img className="page" alt={`${doc?.regulation ?? "Source"} page ${link.printed_page ?? link.pdf_page_index + 1}`}
               src={api.pageUrl(link.document_id, link.pdf_page_index, mode, snapshot)} />
        </div>
      ) : <p className="muted small">No source page is mapped for the primary passage.</p>}
    </section>
  );
}

const REVIEW_OPTIONS: [EngineerReviewStatus, string][] = [["unreviewed", "Unreviewed"], ["approved", "Approve"], ["flagged", "Flag"]];

function EngineerReviewBox({ req, runId, review, onReview }: {
  req: Requirement; runId: string; review?: EngineerReview; onReview: OnReview;
}) {
  const [note, setNote] = useState(review?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = review?.status ?? "unreviewed";
  useEffect(() => setNote(review?.note ?? ""), [review?.note]);

  const save = async (status: EngineerReviewStatus) => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.putReview(runId, req.draft_id, status, note);
      onReview(req.draft_id, status === "unreviewed" && !note ? null : saved);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="review-box">
      <div className="row-flex">
        <strong className="small">Engineer review</strong>
        <span className="spacer" />
        <div className="segmented" role="group" aria-label="Engineer review">
          {REVIEW_OPTIONS.map(([status, label]) => (
            <button key={status} type="button" disabled={saving} aria-pressed={current === status}
                    className={`${status} ${current === status ? "on" : ""}`} onClick={() => save(status)}>{label}</button>
          ))}
        </div>
      </div>
      <textarea placeholder="Note (optional): why it is approved or what needs checking" value={note}
                onChange={(e) => setNote(e.target.value)} onBlur={() => note !== (review?.note ?? "") && save(current)} />
      <p className="hint">Recorded with this analysis. It does not change the exported submission.</p>
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
