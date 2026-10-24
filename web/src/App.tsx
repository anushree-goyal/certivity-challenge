import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Health, RunSummary } from "./types";
import { Landing } from "./components/Landing";
import { Sidebar } from "./components/Sidebar";
import { NewRunForm } from "./components/NewRunForm";
import { RunDetail } from "./components/RunDetail";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));
  const workspace = useRef<HTMLDivElement>(null);

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => undefined), []);
  const toWorkspace = useCallback((smooth = true) => {
    workspace.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }, []);

  useEffect(() => {
    const loadHealth = () => api.health().then((h) => { setHealth(h); setHealthError(null); })
      .catch((e) => setHealthError(String(e.message ?? e)));
    loadHealth();
    refreshRuns();
    const runsTimer = setInterval(refreshRuns, 4000);
    const healthTimer = setInterval(loadHealth, 30000);
    return () => { clearInterval(runsTimer); clearInterval(healthTimer); };
  }, [refreshRuns]);

  // A link to a specific analysis opens straight at the workspace, past the front page.
  const opensRun = useRef(selected !== null);
  useEffect(() => {
    if (!opensRun.current) return;
    const t = window.setTimeout(() => toWorkspace(false), 60);  // after first layout
    return () => window.clearTimeout(t);
  }, [toWorkspace]);

  const select = (id: string | null) => {
    setSelected(id);
    history.replaceState(null, "", id ? `?run=${id}` : location.pathname);
  };

  return (
    <>
      <Landing onStart={() => { select(null); toWorkspace(); }} onScroll={() => toWorkspace()} />
      <div className="shell" id="workspace" ref={workspace}>
        <Sidebar runs={runs} selected={selected} health={health} healthError={healthError}
                 onNew={() => select(null)} onSelect={select} newActive={!selected} />
        <main className="main">
          {selected
            ? <RunDetail key={selected} runId={selected} onFinished={refreshRuns} />
            : <NewRunForm health={health} onCreated={(id) => { refreshRuns(); select(id); }} />}
        </main>
      </div>
    </>
  );
}
