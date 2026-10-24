import { useEffect, useState } from "react";
import { api } from "../api";
import type { CaseInfo, Health } from "../types";
import { prettyCase } from "./Sidebar";
import { ExplodedAssembly } from "./Drawings";

export function NewRunForm({ health, onCreated }: { health: Health | null; onCreated: (id: string) => void }) {
  const [cases, setCases] = useState<CaseInfo[]>([]);
  const [packageCase, setPackageCase] = useState("");
  const [teamId, setTeamId] = useState(() => localGet("team_id"));
  const [caseId, setCaseId] = useState("");
  const [specFile, setSpecFile] = useState<File | null>(null);
  const [specText, setSpecText] = useState("");
  const [drawings, setDrawings] = useState<File[]>([]);
  const [mode, setMode] = useState<"real" | "fixture">("real");
  const [reuseCached, setReuseCached] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.cases().then(setCases).catch(() => undefined); }, []);
  useEffect(() => { if (health && !health.real_pipeline_ready) setMode("fixture"); }, [health]);

  const hasInput = !!packageCase || !!specFile || !!specText.trim() || drawings.length > 0;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.set("team_id", teamId);
    form.set("case_id", caseId || packageCase);
    form.set("mode", mode);
    form.set("reuse_cached", String(reuseCached));
    if (packageCase) form.set("package_case", packageCase);
    if (specFile) form.set("spec_file", specFile);
    else if (specText.trim()) form.set("spec_text", specText);
    drawings.forEach((d) => form.append("drawings", d));
    try {
      localSet("team_id", teamId);
      const run = await api.createRun(form);
      onCreated(run.run_id);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <div className="crumbs"><span>Analyses</span><span>New</span></div>
      <section className="card hero">
        <div>
          <h1>New compliance analysis</h1>
          <p>Upload a technical specification or an exploded-view drawing. ProofTech finds the UN Regulations that apply,
            extracts each engineering obligation and links it to the exact base-version page and paragraph.</p>
          <div className="steps">
            <span className="step"><b>1</b>Specification or drawing</span>
            <span className="step"><b>2</b>Applicable regulations</span>
            <span className="step"><b>3</b>Cited requirements</span>
          </div>
        </div>
        <ExplodedAssembly />
      </section>

      <div className="form-grid">
        <section className="card">
          <h2>Product input</h2>
          <p className="hint">A technical specification and/or an exploded-view drawing of the component.</p>
          <label>Case from the regulation package
            <select value={packageCase} onChange={(e) => setPackageCase(e.target.value)}>
              <option value="">Upload my own input</option>
              {cases.map((c) => (
                <option key={c.case_id} value={c.case_id}>
                  {prettyCase(c.case_id)} ({[c.has_spec && "spec", ...c.drawings].filter(Boolean).join(", ")})
                </option>
              ))}
            </select>
          </label>
          {!packageCase && (
            <>
              <label>Technical specification (.md, .txt)
                <input type="file" accept=".md,.markdown,.txt" onChange={(e) => setSpecFile(e.target.files?.[0] ?? null)} />
              </label>
              {!specFile && (
                <label>Or paste the specification
                  <textarea rows={6} value={specText} onChange={(e) => setSpecText(e.target.value)}
                            placeholder="Product type, vehicle category, functions, parts, materials…" />
                </label>
              )}
            </>
          )}
          <label>{packageCase ? "Additional exploded-view drawings" : "Exploded-view drawings (PNG, JPEG, WebP)"}
            <input type="file" accept=".png,.jpg,.jpeg,.webp" multiple onChange={(e) => setDrawings(Array.from(e.target.files ?? []))} />
          </label>
        </section>

        <section className="card">
          <h2>Submission</h2>
          <div className="two">
            <label>Team ID<input value={teamId} onChange={(e) => setTeamId(e.target.value)} placeholder="team-07" /></label>
            <label>Case ID<input value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder={packageCase || "case folder name"} /></label>
          </div>
          <label>Processing
            <select value={mode} onChange={(e) => setMode(e.target.value as "real" | "fixture")}>
              <option value="real">Full analysis (corpus and model)</option>
              <option value="fixture">Fixture: synthetic data for interface testing</option>
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={reuseCached} onChange={(e) => setReuseCached(e.target.checked)} />
            Reuse a cached result for identical input and configuration
          </label>
          {mode === "real" && health && !health.real_pipeline_ready && (
            <div className="banner">The full pipeline is not ready (see system status in the sidebar). The run will stop and show why.</div>
          )}
          {mode === "fixture" && <div className="banner fixture">Fixture output is synthetic and is labelled as such everywhere.</div>}
          {error && <div className="banner error">{error}</div>}
          <div className="form-actions">
            <button type="submit" disabled={busy || !hasInput}>{busy ? "Starting…" : "Start analysis"}</button>
            {!hasInput && <span className="hint">Choose a case or add a specification or drawing.</span>}
          </div>
        </section>
      </div>
    </form>
  );
}

function localGet(key: string): string {
  try { return localStorage.getItem(`certivity.${key}`) ?? ""; } catch { return ""; }
}
function localSet(key: string, value: string) {
  try { localStorage.setItem(`certivity.${key}`, value); } catch { /* storage unavailable */ }
}
