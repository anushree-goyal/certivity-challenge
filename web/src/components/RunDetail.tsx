import { useEffect, useState } from "react";
import { api } from "../api";
import type { RunStatus, RunView } from "../types";
import { OriginBadge, StateBadge } from "./badges";
import { RequirementsView } from "./RequirementsView";
import { ApplicabilityView, CoverageView, FindingsView, ProfileView, ValidationView } from "./panels";

const TABS = ["Requirements", "Product", "Applicability", "Coverage", "Review", "Validation", "Log"] as const;
type Tab = (typeof TABS)[number];

export function RunDetail({ runId, onFinished }: { runId: string; onFinished: () => void }) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [view, setView] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Requirements");

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    setStatus(null); setView(null); setError(null);
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

  if (error) return <div className="card error">{error}</div>;
  if (!status) return <div className="card muted">Loading run…</div>;
  const p = status.progress;

  return (
    <div className="detail">
      <section className="card runhead">
        <div className="runline">
          <h2>{status.case_id}</h2>
          <StateBadge state={status.state} />
          <OriginBadge origin={status.origin} />
          <span className="muted small">team {status.team_id} · {status.run_id}</span>
          <span className="spacer" />
          <DownloadButton runId={runId} available={!!view?.submission.available} count={view?.submission.requirement_count} />
        </div>
        {status.origin === "fixture" && (
          <p className="banner fixture">Fixture mode: synthetic records for interface testing. Not real regulation text or results.</p>
        )}
        {status.origin === "cached" && (
          <p className="banner cached">Cached result reused from run {status.cached_from}; no new processing was done.</p>
        )}
        {!status.finished && (
          <div className="progress">
            <span className="spinner" /> <strong>{p?.stage ?? status.stage}</strong> {p?.message}
            {p?.total ? <span className="muted"> ({p.completed ?? 0} / {p.total})</span> : null}
          </div>
        )}
        {status.errors.map((e, i) => <p key={i} className="error">Error in {e.stage}: {e.message}</p>)}
        {status.warnings.map((w, i) => <p key={i} className="warn">{w}</p>)}
        {view?.view_warnings.map((w, i) => <p key={`v${i}`} className="warn">{w}</p>)}
      </section>

      {view && (
        <>
          <nav className="tabs">
            {TABS.map((t) => (
              <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
                {t}{t === "Requirements" ? ` (${view.requirements.length})` : t === "Review" ? ` (${view.findings.length})` : ""}
              </button>
            ))}
          </nav>
          {tab === "Requirements" && <RequirementsView view={view} />}
          {tab === "Product" && <ProfileView view={view} runId={runId} />}
          {tab === "Applicability" && <ApplicabilityView view={view} />}
          {tab === "Coverage" && <CoverageView view={view} />}
          {tab === "Review" && <FindingsView view={view} />}
          {tab === "Validation" && <ValidationView view={view} />}
          {tab === "Log" && <EventLog status={status} />}
        </>
      )}
      {!view && <EventLog status={status} />}
    </div>
  );
}

function DownloadButton({ runId, available, count }: { runId: string; available: boolean; count?: number }) {
  if (!available) return <button disabled title="No validated submission for this run">Download JSON</button>;
  return <a className="button" href={api.submissionUrl(runId)} download>Download validated JSON ({count})</a>;
}

function EventLog({ status }: { status: RunStatus }) {
  return (
    <section className="card">
      <h3>Event log</h3>
      <ol className="events">
        {status.events.map((e, i) => (
          <li key={i} className={e.level === "error" ? "error" : ""}>
            <span className="muted small">{e.timestamp}</span> <strong>{e.stage}</strong> {e.message}
            {e.total ? ` (${e.completed ?? 0}/${e.total})` : ""}
          </li>
        ))}
      </ol>
    </section>
  );
}
