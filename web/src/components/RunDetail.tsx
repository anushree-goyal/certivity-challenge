import { useEffect, useState } from "react";
import { api } from "../api";
import type { EngineerReview, RunStatus, RunView } from "../types";
import { OriginBadge, StateBadge, needsReview } from "./badges";
import { RequirementsView } from "./RequirementsView";
import { ApplicabilityView, CoverageView, FindingsView, ProfileView, ValidationView } from "./panels";
import { CompareView } from "./CompareView";
import { prettyCase } from "./Sidebar";
import { SourceSheet } from "./Drawings";

const TABS = ["Requirements", "Product", "Applicability", "Coverage", "Review findings", "Validation", "Compare", "Log"] as const;
type Tab = (typeof TABS)[number];

export function RunDetail({ runId, onFinished }: { runId: string; onFinished: () => void }) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [view, setView] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Requirements");

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const s = await api.status(runId);
        if (stop) return;
        setStatus(s);
        if (s.finished) {
          api.results(runId).then((v) => !stop && setView(v)).catch(() => undefined);
          onFinished();
          return;
        }
      } catch (e) {
        if (!stop) setError(String((e as Error).message));
        return;
      }
      timer = window.setTimeout(poll, 1500);
    };
    poll();
    return () => { stop = true; window.clearTimeout(timer); };
  }, [runId, onFinished]);

  if (error && !status) return <div className="banner error">{error}</div>;
  if (!status) return <p className="muted">Loading analysis…</p>;

  const setReview = (draftId: string, review: EngineerReview | null) => setView((v) => {
    if (!v) return v;
    const reviews = { ...v.engineer_reviews };
    if (review) reviews[draftId] = review; else delete reviews[draftId];
    return { ...v, engineer_reviews: reviews };
  });

  const inSubmission = view?.requirements.filter((r) => r.requirement_id) ?? [];
  // Without a completed automated review, every exported requirement still needs a person to check it.
  const attention = view && !view.review_complete ? inSubmission.length
    : inSubmission.filter((r) => needsReview(r, view?.engineer_reviews[r.draft_id])).length;
  const approved = inSubmission.filter((r) => view?.engineer_reviews[r.draft_id]?.status === "approved").length;
  const regulations = new Set(inSubmission.map((r) => r.source.regulation)).size;
  const p = status.progress;
  const notices = [...status.warnings, ...(view?.view_warnings ?? [])];

  return (
    <div>
      <div className="crumbs"><span>Analyses</span><span>{prettyCase(status.case_id)}</span><span className="mono">{status.run_id}</span></div>
      <div className="head">
        <h1>{prettyCase(status.case_id)}</h1>
        <StateBadge state={status.state} />
        <OriginBadge origin={status.origin} />
        <span className="muted small">team {status.team_id}</span>
        <span className="spacer" />
        {!status.finished && (status.cancel_requested
          ? <span className="muted small">Cancelling after the current stage…</span>
          : <button className="danger" onClick={() => api.cancel(runId).catch((e) => setError(String(e.message)))}>Cancel</button>)}
        {view?.submission.available
          ? <a className="button" href={api.submissionUrl(runId)} download>Export requirements JSON</a>
          : <button disabled title="No validated submission for this run">Export requirements JSON</button>}
      </div>

      {!status.finished && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <div className="row-flex"><strong>{p?.stage ?? status.stage}</strong><span className="muted">{p?.message}</span>
            {p?.total ? <span className="muted small">{p.completed ?? 0} of {p.total}</span> : null}</div>
          <div className={`progress-bar ${p?.total ? "" : "indeterminate"}`}>
            <div style={p?.total ? { width: `${Math.round(100 * (p.completed ?? 0) / p.total)}%` } : undefined} />
          </div>
        </div>
      )}

      {status.origin === "fixture" && (
        <div className="banner fixture">Fixture data: synthetic records for interface testing. These are not regulatory findings.</div>
      )}
      {status.origin === "cached" && <div className="banner info">Cached result reused from analysis {status.cached_from}. No new processing was done.</div>}
      {view && !view.review_complete && (
        <div className="banner">Automated review was incomplete (for example, no model credential or the call budget ran out). Requirements are not source-checked.</div>
      )}
      {error && <div className="banner error">{error}</div>}
      {status.errors.map((e, i) => <div key={i} className="banner error">Error in {e.stage}: {e.message}</div>)}
      {notices.length > 0 && (
        <div className="banner">
          <div><strong>{notices.length} notice{notices.length > 1 ? "s" : ""}</strong>
            <ul>{notices.map((w, i) => <li key={i}>{w}</li>)}</ul></div>
        </div>
      )}

      {view && (
        <>
          <div className="stats">
            <Stat label="Requirements" value={inSubmission.length} sub={`across ${regulations} regulation${regulations === 1 ? "" : "s"}`} />
            <Stat label="Source-checked" value={view.review_complete ? inSubmission.length - attention : 0} sub="automated evidence review" />
            <Stat label="Needs review" value={attention} sub={view.review_complete ? "issues, open references or flagged" : "automated review did not complete"} attention={attention > 0} />
            <Stat label="Engineer approved" value={`${approved} / ${inSubmission.length}`} sub="human review" />
          </div>

          <nav className="tabs">
            {TABS.map((t) => (
              <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
                {t}
                {t === "Requirements" && <span className="tab-count">{view.requirements.length}</span>}
                {t === "Review findings" && <span className="tab-count">{view.findings.length}</span>}
              </button>
            ))}
          </nav>
          {tab === "Requirements" && <RequirementsView view={view} runId={runId} onReview={setReview} />}
          {tab === "Product" && <ProfileView view={view} runId={runId} />}
          {tab === "Applicability" && <ApplicabilityView view={view} />}
          {tab === "Coverage" && <CoverageView view={view} />}
          {tab === "Review findings" && <FindingsView view={view} />}
          {tab === "Validation" && <ValidationView view={view} />}
          {tab === "Compare" && <CompareView runId={runId} />}
          {tab === "Log" && <EventLog status={status} />}
        </>
      )}
      {!view && !status.finished && (
        <div className="card" style={{ textAlign: "center", marginBottom: "1rem" }}>
          <div className="empty-art"><SourceSheet /></div>
          <p className="muted">Reading regulations and tracing each requirement to its source paragraph…</p>
        </div>
      )}
      {!view && <EventLog status={status} />}
    </div>
  );
}

function Stat({ label, value, sub, attention }: { label: string; value: number | string; sub: string; attention?: boolean }) {
  return (
    <div className={`stat ${attention ? "attention" : ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{sub}</div>
    </div>
  );
}

function EventLog({ status }: { status: RunStatus }) {
  return (
    <section className="card">
      <h2>Processing log</h2>
      <ol className="events">
        {status.events.map((e, i) => (
          <li key={i} className={e.level === "error" ? "error" : ""}>
            {e.timestamp?.slice(11, 19)} [{e.stage}] {e.message}{e.total ? ` (${e.completed ?? 0}/${e.total})` : ""}
          </li>
        ))}
      </ol>
    </section>
  );
}
