import { useEffect, useState } from "react";
import { api } from "../api";
import type { Health, ReimportStatus } from "../types";

/** Shown when the regulation package changed since the active corpus snapshot was imported. Re-import builds a
 *  new snapshot through the corpus module; it is disabled while an analysis is running or queued. */
export function CorpusBanner({ health, onChanged }: { health: Health | null; onChanged: () => void }) {
  const [status, setStatus] = useState<ReimportStatus | null>(health?.corpus_reimport ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (health?.corpus_reimport) setStatus(health.corpus_reimport); }, [health?.corpus_reimport]);
  useEffect(() => {
    if (status?.state !== "running") return;
    const t = window.setInterval(() => {
      api.reimportStatus().then((s) => { setStatus(s); if (s.state !== "running") onChanged(); }).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(t);
  }, [status?.state, onChanged]);

  const fresh = health?.corpus_freshness;
  const running = status?.state === "running";
  const busy = !!health?.app.active_run || (health?.app.queued_runs ?? 0) > 0;
  if (!fresh || (!fresh.needs_import && !running && status?.state !== "failed")) return null;

  const start = () => {
    setError(null);
    api.startReimport().then(setStatus).catch((e) => setError(String(e.message)));
  };
  return (
    <div className="banner corpus-banner" role="status">
      <div>
        <strong>{running ? "Re-importing the regulation corpus…" : status?.state === "failed" ? "Corpus re-import failed" : "Regulation package changed — corpus needs re-import"}</strong>
        {!running && fresh.reasons.length > 0 && <ul>{fresh.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
        {status?.state === "failed" && status.error && <p className="small">{status.error}</p>}
        {error && <p className="small error">{error}</p>}
        {running && <p className="small">New analyses can start when it finishes. Runs already finished keep showing their own snapshot.</p>}
      </div>
      <span className="spacer" />
      <button type="button" onClick={start} disabled={running || busy}
              title={busy ? "Wait until the running analysis finishes" : undefined}>
        {running ? "Importing…" : "Re-import corpus"}
      </button>
    </div>
  );
}
