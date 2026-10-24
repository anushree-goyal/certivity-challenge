import { useEffect, useState } from "react";
import { api } from "../api";
import type { CaseInfo, Health } from "../types";
import { prettyCase } from "./Sidebar";
import { ExplodedAssembly } from "./Drawings";

// Fixture (synthetic) processing is for development only: reachable with ?dev=1, read once at load so it
// survives the app rewriting the URL when an analysis is selected.
const DEV_MODE = (() => {
  try { return new URLSearchParams(location.search).get("dev") === "1"; } catch { return false; }
})();

const CASE_ID_PATTERN = /^[a-z0-9-]+$/;

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

  const chooseCase = (id: string) => {
    setPackageCase(id);
    setCaseId(id);  // the case folder name is the case_id the platform expects
    setError(null);
  };
  const changeTeam = (value: string) => {
    setTeamId(value);
    localSet("team_id", value.trim());
  };

  const hasInput = !!packageCase || !!specFile || !!specText.trim() || drawings.length > 0;
  const trimmedTeam = teamId.trim();
  const trimmedCase = caseId.trim();
  const isPackageFolder = cases.some((c) => c.case_id === trimmedCase);  // folder names are accepted as they are
  const caseIdError = !trimmedCase ? "Case ID is required."
    : !isPackageFolder && !CASE_ID_PATTERN.test(trimmedCase) ? "Case ID may contain only lowercase letters, digits and hyphens." : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const problems = [
      !hasInput && "choose a case or add a specification or drawing",
      !trimmedTeam && "enter your team ID (from hackathon.certivity.io)",
      caseIdError && caseIdError.replace(/\.$/, "").toLowerCase(),
    ].filter(Boolean);
    if (problems.length) {
      setError(`Before starting: ${problems.join("; ")}.`);
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.set("team_id", trimmedTeam);
    form.set("case_id", trimmedCase);
    form.set("mode", DEV_MODE ? mode : "real");
    form.set("reuse_cached", String(reuseCached));
    if (packageCase) form.set("package_case", packageCase);
    if (specFile) form.set("spec_file", specFile);
    else if (specText.trim()) form.set("spec_text", specText);
    drawings.forEach((d) => form.append("drawings", d));
    try {
      const run = await api.createRun(form);
      onCreated(run.run_id);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate>
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
            <select value={packageCase} onChange={(e) => chooseCase(e.target.value)}>
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
          <h2>Run settings</h2>
          <div className="two">
            <label>Team ID <span className="req" aria-hidden>*</span>
              <input value={teamId} onChange={(e) => changeTeam(e.target.value)} required aria-required
                     placeholder="your team id from hackathon.certivity.io" autoComplete="off" />
            </label>
            <label>Case ID <span className="req" aria-hidden>*</span>
              {packageCase ? (
                <select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                  {cases.map((c) => <option key={c.case_id} value={c.case_id}>{c.case_id}</option>)}
                </select>
              ) : (
                <input value={caseId} onChange={(e) => setCaseId(e.target.value.toLowerCase())} required aria-required
                       aria-invalid={!!caseId && !!caseIdError} placeholder="e.g. front-bumper" list="case-folders" autoComplete="off" />
              )}
              <datalist id="case-folders">{cases.map((c) => <option key={c.case_id} value={c.case_id} />)}</datalist>
            </label>
          </div>
          {!packageCase && caseId && caseIdError && <p className="field-error">{caseIdError}</p>}
          <p className="hint">The case ID is the case's folder name in the regulation package; it is written into the exported file.</p>

          <label>Processing
            {DEV_MODE ? (
              <select value={mode} onChange={(e) => setMode(e.target.value as "real" | "fixture")}>
                <option value="real">Full analysis (corpus and model)</option>
                <option value="fixture">Fixture: synthetic data (dev only)</option>
              </select>
            ) : (
              <div className="static-field">Full analysis (corpus and model)</div>
            )}
          </label>
          <label className="check">
            <input type="checkbox" checked={reuseCached} onChange={(e) => setReuseCached(e.target.checked)} />
            Reuse a cached result for identical input and configuration
          </label>
          {health && !health.real_pipeline_ready && (!DEV_MODE || mode === "real") && (
            <div className="banner">The full pipeline is not ready (see system status in the sidebar). The run will stop and show why.</div>
          )}
          {DEV_MODE && mode === "fixture" && <div className="banner fixture">Fixture output is synthetic and is labelled as such everywhere.</div>}
          {error && <div className="banner error" role="alert">{error}</div>}
          <div className="form-actions">
            <button type="submit" disabled={busy}>{busy ? "Starting…" : "Start analysis"}</button>
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
