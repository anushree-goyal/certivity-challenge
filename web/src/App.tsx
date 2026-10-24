import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Health, RunSummary } from "./types";
import { Landing } from "./components/Landing";
import { Sidebar } from "./components/Sidebar";
import { NewRunForm } from "./components/NewRunForm";
import { RunDetail } from "./components/RunDetail";
import { RegulationsPage } from "./components/RegulationsPage";
import { CorpusBanner } from "./components/CorpusBanner";

type View = "analyses" | "regulations";

function initialView(): { view: View; reg: string | null } {
  const q = new URLSearchParams(location.search);
  return { view: q.get("view") === "regulations" ? "regulations" : "analyses", reg: q.get("reg") };
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));
  const [view, setView] = useState<View>(() => initialView().view);
  const [focusReg, setFocusReg] = useState<string | null>(() => initialView().reg);
  const workspace = useRef<HTMLDivElement>(null);

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => undefined), []);
  const loadHealth = useCallback(() => api.health().then((h) => { setHealth(h); setHealthError(null); })
    .catch((e) => setHealthError(String(e.message ?? e))), []);
  const toWorkspace = useCallback((smooth = true) => {
    workspace.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }, []);

  useEffect(() => {
    loadHealth();
    refreshRuns();
    const runsTimer = setInterval(refreshRuns, 4000);
    const healthTimer = setInterval(loadHealth, 30000);
    return () => { clearInterval(runsTimer); clearInterval(healthTimer); };
  }, [refreshRuns, loadHealth]);

  // A link to a specific analysis or the regulations page opens straight at the workspace, past the front page.
  const opensWorkspace = useRef(selected !== null || view === "regulations");
  useEffect(() => {
    if (!opensWorkspace.current) return;
    const t = window.setTimeout(() => toWorkspace(false), 60);  // after first layout
    return () => window.clearTimeout(t);
  }, [toWorkspace]);

  const select = (id: string | null) => {
    setView("analyses");
    setSelected(id);
    history.replaceState(null, "", id ? `?run=${id}` : location.pathname);
  };
  const openRegulations = (regulation: string | null = null) => {
    setView("regulations");
    setFocusReg(regulation);
    const q = new URLSearchParams({ view: "regulations" });
    if (regulation) q.set("reg", regulation);
    history.replaceState(null, "", `?${q}`);
    toWorkspace(false);
  };

  return (
    <>
      <Landing onStart={() => { select(null); toWorkspace(); }} onScroll={() => toWorkspace()} />
      <div className="shell" id="workspace" ref={workspace}>
        <Sidebar runs={runs} selected={view === "analyses" ? selected : null} health={health} healthError={healthError}
                 onNew={() => select(null)} onSelect={select} newActive={view === "analyses" && !selected}
                 view={view} onRegulations={() => openRegulations(null)} />
        <main className="main">
          <CorpusBanner health={health} onChanged={loadHealth} />
          {view === "regulations"
            ? <RegulationsPage focus={focusReg} />
            : selected
              ? <RunDetail key={selected} runId={selected} onFinished={refreshRuns} onOpenRegulation={openRegulations} />
              : <NewRunForm health={health} onCreated={(id) => { refreshRuns(); select(id); }} />}
          <footer className="app-footer">
            <a href="https://github.com/Certivity/industrial-ai-hackathon" target="_blank" rel="noreferrer">Participant package (GitHub) ↗</a>
          </footer>
        </main>
      </div>
    </>
  );
}
