import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { CaseInfo, Health } from "../types";
import { prettyCase } from "./Sidebar";
import { ExplodedAssembly } from "./Drawings";

// Fixture (synthetic) processing is for development only: reachable with ?dev=1, read once at load so it
// survives the app rewriting the URL when an analysis is selected.
const DEV_MODE = (() => {
  try { return new URLSearchParams(location.search).get("dev") === "1"; } catch { return false; }
})();

type Step = 1 | 2 | 3;
const STEPS: [Step, string][] = [[1, "Specification or drawing"], [2, "Applicable regulations"], [3, "Cited requirements"]];

/** Pipeline step chips: hover or keyboard focus previews the step on the drawing; a tap toggles it on touch. */
function StepChips({ active, onChange }: { active: Step | null; onChange: (step: Step | null) => void }) {
  // "mouse" also covers keyboard focus (no pointer involved); touch and pen taps toggle instead.
  const lastPointer = useRef<string>("mouse");
  return (
    <div className="steps" role="group" aria-label="How it works">
      {STEPS.map(([n, label]) => (
        <button key={n} type="button" className={`step${active === n ? " active" : ""}`} aria-pressed={active === n}
                onPointerDown={(e) => { lastPointer.current = e.pointerType; }}
                onKeyDown={() => { lastPointer.current = "mouse"; }}
                onPointerEnter={(e) => { if (e.pointerType === "mouse") onChange(n); }}
                onPointerLeave={(e) => { if (e.pointerType === "mouse") onChange(null); }}
                onFocus={() => { if (lastPointer.current === "mouse") onChange(n); }}  // touch taps toggle via onClick
                onBlur={() => onChange(null)}
                onClick={() => { if (lastPointer.current !== "mouse") onChange(active === n ? null : n); }}>
          <b>{n}</b>{label}
        </button>
      ))}
    </div>
  );
}

export function NewRunForm({ health, onCreated }: { health: Health | null; onCreated: (id: string) => void }) {
  const [cases, setCases] = useState<CaseInfo[]>([]);
  const [packageCase, setPackageCase] = useState("");
  const [specFile, setSpecFile] = useState<File | null>(null);
  const [specText, setSpecText] = useState("");
  const [drawings, setDrawings] = useState<File[]>([]);
  // The platform only accepts an upload whose case_id is the case folder name; own files get it typed here.
  const [caseId, setCaseId] = useState("");
  const [mode, setMode] = useState<"real" | "fixture">("real");
  const [reuseCached, setReuseCached] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeStep, setActiveStep] = useState<Step | null>(null);

  // Main-goal cases only (sub-goal folders are listed by the API but not runnable here); held-out first.
  const loadCases = () => api.cases().then((all) => setCases(all.filter((c) => c.type === "main"))).catch(() => undefined);
  useEffect(() => { loadCases(); }, []);

  // Team ID (CERTIVITY_TEAM_ID) and case ID are not entered here: the server takes the team ID from its
  // configuration and derives the case ID (case folder name, or a slug of the uploaded spec).
  const chooseCase = (id: string) => {
    setPackageCase(id);
    setError(null);
  };

  const selectedCase = cases.find((c) => c.case_id === packageCase) ?? null;
  const caseHasInputs = !!selectedCase && (!!selectedCase.spec_file || selectedCase.drawings.length > 0);
  const hasInput = (packageCase ? caseHasInputs : false) || !!specFile || !!specText.trim() || drawings.length > 0;
  const missingPdfs = health?.corpus_freshness?.missing_pdfs ?? [];
  const teamWarning = !health?.team_id ? null
    : !health.team_id.configured ? "Exports are disabled until CERTIVITY_TEAM_ID is set in .env — runs still work."
    : health.team_id.warning;  // e.g. a placeholder team id

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!hasInput) {
      setError("Before starting: choose a case or add a specification or drawing.");
      return;
    }
    if (!packageCase && !caseId.trim()) {
      setError("Before starting: enter the case ID the platform expects (the case folder name).");
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.set("mode", DEV_MODE ? mode : "real");
    form.set("reuse_cached", String(DEV_MODE && reuseCached));  // normal runs are always fresh
    form.set("dev", String(DEV_MODE));
    if (packageCase) form.set("package_case", packageCase);
    else if (caseId.trim()) form.set("case_id", caseId.trim());
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
          <StepChips active={activeStep} onChange={setActiveStep} />
        </div>
        <ExplodedAssembly step={activeStep} />
      </section>

      <div className={`form-grid${DEV_MODE ? "" : " single"}`}>
        <section className={`card input-panel${activeStep === 1 ? " step-target" : ""}`}>
          <h2>Product input</h2>
          <p className="hint">A technical specification and/or an exploded-view drawing of the component.</p>
          <label>Case from the regulation package
            <select value={packageCase} onChange={(e) => chooseCase(e.target.value)} onFocus={loadCases}>
              <option value="">Upload my own input</option>
              {cases.map((c) => (
                <option key={c.case_id} value={c.case_id}>
                  {c.held_out ? "Held-out: " : ""}{prettyCase(c.case_id)} ({[c.spec_file, ...c.drawings].filter(Boolean).join(", ") || "no inputs"})
                </option>
              ))}
            </select>
          </label>
          {selectedCase && (
            <div className="picked-inputs">
              <span className="muted small">Inputs picked up from the case folder:</span>
              <ul>
                {selectedCase.spec_file && <li>Specification · <code>{selectedCase.spec_file}</code></li>}
                {selectedCase.drawings.map((d) => <li key={d}>Drawing · <code>{d}</code></li>)}
                {!selectedCase.spec_file && selectedCase.drawings.length === 0 && <li className="error">No .md/.txt or image files found</li>}
              </ul>
              {selectedCase.spec_files.length > 1 && (
                <p className="hint">Several text files found; using <code>{selectedCase.spec_file}</code>.</p>
              )}
            </div>
          )}
          {!packageCase && (
            <>
              <label>Case ID
                <input value={caseId} onChange={(e) => setCaseId(e.target.value)}
                       placeholder={cases.find((c) => c.held_out)?.case_id ?? "e.g. heldout-front-bumper"} />
              </label>
              <p className="hint">Written into the exported file. The platform only accepts the case folder name, e.g.{" "}
                <code>{cases.find((c) => c.held_out)?.case_id ?? "heldout-front-bumper"}</code>.</p>
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

          {health && !health.real_pipeline_ready && (!DEV_MODE || mode === "real") && (
            <div className="banner">The full pipeline is not ready (see system status in the sidebar). The run will stop and show why.</div>
          )}
          {missingPdfs.length > 0 && (
            <div className="banner">
              {missingPdfs.length} base regulation PDF{missingPdfs.length === 1 ? " is" : "s are"} not downloaded, so
              {missingPdfs.length === 1 ? " it" : " they"} cannot be cited. See the Regulations page.
            </div>
          )}
          {teamWarning && <div className="banner">{teamWarning}</div>}
          <div className="form-actions">
            <button type="submit" disabled={busy} aria-busy={busy}>{busy ? "Starting…" : "Start analysis"}</button>
            {error && <span className="start-error" role="alert">{error}</span>}
          </div>
        </section>

        {DEV_MODE && (
          <section className="card dev-options">
            <h2>Developer options</h2>
            <p className="hint">Shown only with ?dev=1.</p>
            <label>Processing
              <select value={mode} onChange={(e) => setMode(e.target.value as "real" | "fixture")}>
                <option value="real">Full analysis (corpus and model)</option>
                <option value="fixture">Fixture: synthetic data</option>
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={reuseCached} onChange={(e) => setReuseCached(e.target.checked)} />
              Reuse a cached result for identical input and configuration
            </label>
            {mode === "fixture" && <div className="banner fixture">Fixture output is synthetic and is labelled as such everywhere.</div>}
          </section>
        )}
      </div>
    </form>
  );
}
