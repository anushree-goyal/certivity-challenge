import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Health, RunSummary } from "./types";
import { Sidebar } from "./components/Sidebar";
import { NewRunForm } from "./components/NewRunForm";
import { RunDetail } from "./components/RunDetail";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => undefined), []);

  useEffect(() => {
    const loadHealth = () => api.health().then((h) => { setHealth(h); setHealthError(null); })
      .catch((e) => setHealthError(String(e.message ?? e)));
    loadHealth();
    refreshRuns();
    const runsTimer = setInterval(refreshRuns, 4000);
    const healthTimer = setInterval(loadHealth, 30000);
    return () => { clearInterval(runsTimer); clearInterval(healthTimer); };
  }, [refreshRuns]);

  const select = (id: string | null) => {
    setSelected(id);
    history.replaceState(null, "", id ? `?run=${id}` : location.pathname);
  };

  return (
    <div className="shell">
      <Sidebar runs={runs} selected={selected} health={health} healthError={healthError}
               onNew={() => select(null)} onSelect={select} newActive={!selected} />
      <main className="main">
        {selected
          ? <RunDetail key={selected} runId={selected} onFinished={refreshRuns} />
          : <NewRunForm health={health} onCreated={(id) => { refreshRuns(); select(id); }} />}
      </main>
    </div>
  );
}
