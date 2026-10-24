import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Health, RunSummary } from "./types";
import { HealthBar } from "./components/HealthBar";
import { NewRunForm } from "./components/NewRunForm";
import { RunList } from "./components/RunList";
import { RunDetail } from "./components/RunDetail";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => undefined), []);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(String(e.message ?? e)));
    refreshRuns();
    const t = setInterval(refreshRuns, 4000);
    return () => clearInterval(t);
  }, [refreshRuns]);

  const select = (id: string) => {
    setSelected(id);
    history.replaceState(null, "", `?run=${id}`);
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>Certivity requirements</h1>
        <HealthBar health={health} error={healthError} />
      </header>
      <div className="layout">
        <aside className="sidebar">
          <NewRunForm health={health} onCreated={(id) => { refreshRuns(); select(id); }} />
          <RunList runs={runs} selected={selected} onSelect={select} />
        </aside>
        <main className="main">
          {selected ? <RunDetail runId={selected} onFinished={refreshRuns} /> : (
            <div className="empty">Start a run or pick one from the list.</div>
          )}
        </main>
      </div>
    </div>
  );
}
