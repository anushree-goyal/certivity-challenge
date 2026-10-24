import { useEffect, useState } from "react";
import { api } from "../api";
import type { CaseInfo, Health } from "../types";

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
    <form className="card newrun" onSubmit={submit}>
      <h2>New run</h2>
      <label>Input case from package
        <select value={packageCase} onChange={(e) => setPackageCase(e.target.value)}>
          <option value="">— upload my own input —</option>
          {cases.map((c) => (
            <option key={c.case_id} value={c.case_id}>
              {c.case_id} ({[c.has_spec && "spec", ...c.drawings].filter(Boolean).join(", ")})
            </option>
          ))}
        </select>
      </label>
      {!packageCase && (
        <>
          <label>Specification (.md / .txt)
            <input type="file" accept=".md,.markdown,.txt" onChange={(e) => setSpecFile(e.target.files?.[0] ?? null)} />
          </label>
          {!specFile && (
            <label>…or paste specification text
              <textarea rows={4} value={specText} onChange={(e) => setSpecText(e.target.value)} />
            </label>
          )}
        </>
      )}
      <label>{packageCase ? "Extra" : ""} exploded-view drawing(s)
        <input type="file" accept=".png,.jpg,.jpeg,.webp" multiple onChange={(e) => setDrawings(Array.from(e.target.files ?? []))} />
      </label>
      <div className="row">
        <label>Team ID<input value={teamId} onChange={(e) => setTeamId(e.target.value)} placeholder="team-07" /></label>
        <label>Case ID<input value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder={packageCase || "case folder name"} /></label>
      </div>
      <div className="row">
        <label>Pipeline
          <select value={mode} onChange={(e) => setMode(e.target.value as "real" | "fixture")}>
            <option value="real">Real processing</option>
            <option value="fixture">Fixture (synthetic demo data)</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={reuseCached} onChange={(e) => setReuseCached(e.target.checked)} />
          Reuse cached result if identical
        </label>
      </div>
      {mode === "real" && health && !health.real_pipeline_ready && (
        <p className="warn">The real pipeline is not ready (see status bar). This run will fail with the reason.</p>
      )}
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? "Starting…" : "Start run"}</button>
    </form>
  );
}

function localGet(key: string): string {
  try { return localStorage.getItem(`certivity.${key}`) ?? ""; } catch { return ""; }
}
function localSet(key: string, value: string) {
  try { localStorage.setItem(`certivity.${key}`, value); } catch { /* storage unavailable */ }
}
