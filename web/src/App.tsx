import { useCallback, useEffect, useRef, useState } from "react";
import { api, OFFLINE_MESSAGE, onConnectivity } from "./api";
import { usePoller } from "./usePoller";
import type { Health, RunSummary } from "./types";
import { Landing } from "./components/Landing";
import { Sidebar } from "./components/Sidebar";
import { NewRunForm } from "./components/NewRunForm";
import { RunDetail } from "./components/RunDetail";
import { RegulationsPage } from "./components/RegulationsPage";
import { SubgoalPage } from "./components/SubgoalPage";
import { CorpusBanner } from "./components/CorpusBanner";

type View = "analyses" | "regulations" | "subgoal";

function initialView(): { view: View; reg: string | null } {
  const q = new URLSearchParams(location.search);
  const v = q.get("view");
  return { view: v === "regulations" || v === "subgoal" ? v : "analyses", reg: q.get("reg") };
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));
  const [view, setView] = useState<View>(() => initialView().view);
  const [focusReg, setFocusReg] = useState<string | null>(() => initialView().reg);
  const workspace = useRef<HTMLDivElement>(null);

  const [online, setOnline] = useState(true);
  const [retryIn, setRetryIn] = useState(1000);

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => undefined), []);
  const loadHealth = useCallback(() => api.health().then((h) => { setHealth(h); setHealthError(null); })
    .catch((e) => setHealthError(String(e.message ?? e))), []);
  const toWorkspace = useCallback((smooth = true) => {
    workspace.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }, []);

  // Load once; afterwards refresh on events rather than on a blind timer.
  useEffect(() => {
    loadHealth();
    refreshRuns();
    const onFocus = () => { if (document.visibilityState !== "hidden") { refreshRuns(); loadHealth(); } };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    const off = onConnectivity((up) => {
      setOnline(up);
      if (up) { setRetryIn(1000); refreshRuns(); loadHealth(); }
    });
    return () => { window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onFocus); off(); };
  }, [refreshRuns, loadHealth]);

  // The run list polls (every 3 s) only while some run is queued or running.
  const anyActive = runs.some((r) => r.state === "queued" || r.state === "running");
  usePoller(refreshRuns, 3000, online && anyActive);
  // Health: every 30 s normally; while the server is unreachable, retry with backoff (1 s, 2 s, 4 s … 15 s).
  usePoller(() => loadHealth().finally(() => setRetryIn((t) => Math.min(t * 2, 15000))), online ? 30000 : retryIn, true);

  // A link to a specific analysis or the regulations page opens straight at the workspace, past the front page.
  const opensWorkspace = useRef(selected !== null || view !== "analyses");
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

  const openSubgoal = () => {
    setView("subgoal");
    history.replaceState(null, "", "?view=subgoal");
    toWorkspace(false);
  };

  return (
    <>
      <Landing onStart={() => { select(null); toWorkspace(); }} onScroll={() => toWorkspace()} />
      <div className="shell" id="workspace" ref={workspace}>
        <Sidebar runs={runs} selected={view === "analyses" ? selected : null} health={health} healthError={healthError}
                 onNew={() => select(null)} onSelect={select} newActive={view === "analyses" && !selected}
                 view={view} onRegulations={() => openRegulations(null)} onSubgoal={openSubgoal} />
        <main className="main">
          {!online && <div className="banner error offline-banner" role="alert">{OFFLINE_MESSAGE} Retrying…</div>}
          <CorpusBanner health={health} onChanged={loadHealth} />
          {view === "subgoal" ? <SubgoalPage /> : view === "regulations"
            ? <RegulationsPage focus={focusReg} />
            : selected
              ? <RunDetail key={selected} runId={selected} health={health} onFinished={refreshRuns} onOpenRegulation={openRegulations} />
              : <NewRunForm health={health} onCreated={(id) => { refreshRuns(); select(id); }} />}
          <footer className="app-footer">
            <a href="https://github.com/Certivity/industrial-ai-hackathon" target="_blank" rel="noreferrer">Participant package (GitHub) ↗</a>
          </footer>
        </main>
      </div>
    </>
  );
}
