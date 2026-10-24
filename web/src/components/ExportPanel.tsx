import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ExportStatus, Health, RunStatus, RunView } from "../types";

type Availability =
  | { kind: "valid" }
  | { kind: "invalid"; errors: { path: string; message: string }[] }
  | { kind: "unavailable"; reason: string };

/** The server builds the export at download time with the current CERTIVITY_TEAM_ID and validates it; this
 *  mirrors that answer. A run made before the team ID was set exports once it is set, without re-running. */
function availability(status: RunStatus, exp: ExportStatus | null): Availability {
  if (!status.finished) return { kind: "unavailable", reason: `the run is still ${status.state}` };
  if (!exp) return { kind: "unavailable", reason: "checking…" };
  if (exp.available) return { kind: "valid" };
  if (exp.code === "team_missing") return { kind: "unavailable", reason: "team ID not configured" };
  if (exp.code === "invalid") return { kind: "invalid", errors: exp.errors };
  return { kind: "unavailable", reason: exp.reason ?? "no submission was built for this run" };
}

/** Why a valid download still deserves a second look before uploading. */
function cautions(status: RunStatus, view: RunView | null): string[] {
  const out: string[] = [];
  if (status.state === "partial") out.push("The run is partial.");
  if (view && !view.review_complete) out.push("The automated evidence review did not complete, so requirements are not source-checked.");
  for (const w of status.warnings) if (!out.includes(w)) out.push(w);
  return out;
}

export function ExportPanel({ runId, status, view, health }: {
  runId: string; status: RunStatus; view: RunView | null; health: Health | null;
}) {
  const [dialog, setDialog] = useState<"confirm" | "errors" | null>(null);
  const [exp, setExp] = useState<ExportStatus | null>(null);
  const teamConfigured = health?.team_id?.configured;
  useEffect(() => {
    if (!status.finished) return;
    api.exportStatus(runId).then(setExp).catch((e) => setExp({ available: false, case_id: status.case_id, code: null,
      reason: String((e as Error).message), errors: [] }));
  }, [runId, status.finished, status.case_id, teamConfigured]);
  const state = availability(status, exp);
  const notes = state.kind === "valid" ? cautions(status, view) : [];

  const download = () => {
    const a = document.createElement("a");
    a.href = api.submissionUrl(runId);  // the server names the file {case_id}_{team_id}_{run_id}.json
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  const onExport = () => {
    if (state.kind === "invalid") setDialog("errors");
    else if (state.kind === "valid" && notes.length) setDialog("confirm");
    else if (state.kind === "valid") download();
  };

  return (
    <div className="export-box">
      <div className="export-case" title="Written into the exported file; check it before uploading">
        Case ID <code>{status.case_id}</code>
      </div>
      <div className="export-row">
        {state.kind === "valid" && <span className="chip green" title="Passed the official participant validator">Valid · official validator</span>}
        {state.kind === "invalid" && <span className="chip error">Invalid · {state.errors.length} validator error{state.errors.length === 1 ? "" : "s"}</span>}
        {state.kind === "unavailable" && exp?.code === "team_missing"
          && <span className="chip attention" title="Set CERTIVITY_TEAM_ID in .env and restart; no need to re-run">Export disabled: team ID not configured</span>}
        {state.kind === "unavailable" && exp?.code !== "team_missing"
          && <span className="chip outline" title={state.reason}>Not available: {state.reason}</span>}
        <button type="button" onClick={onExport} disabled={state.kind === "unavailable"}>
          {state.kind === "invalid" ? "Show validator errors" : "Export requirements JSON"}
        </button>
      </div>
      <ul className="export-checklist">
        <li>Upload at hackathon.certivity.io</li>
        <li>The last valid upload counts, not the best</li>
        <li>At most 5 valid uploads per hour</li>
        <li>Held-out uploads close Sunday 13:00</li>
      </ul>

      {dialog === "confirm" && (
        <Modal title="Export a partial result?" onClose={() => setDialog(null)}>
          <p>This file passed the official validator, but before you upload it, note:</p>
          <ul>{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          <p className="muted small">For the held-out case the last valid upload counts, so a weaker file uploaded later replaces a better one.</p>
          <div className="modal-actions">
            <button type="button" className="ghost" onClick={() => setDialog(null)}>Cancel</button>
            <button type="button" onClick={() => { setDialog(null); download(); }}>Download anyway</button>
          </div>
        </Modal>
      )}
      {dialog === "errors" && state.kind === "invalid" && (
        <Modal title="Submission did not pass the validator" onClose={() => setDialog(null)}>
          <p>This file would be rejected on upload, so it cannot be downloaded. Validator errors:</p>
          <ul className="validator-errors">
            {state.errors.map((e, i) => <li key={i}><code>{e.path}</code> {e.message}</li>)}
          </ul>
          <div className="modal-actions"><button type="button" onClick={() => setDialog(null)}>Close</button></div>
        </Modal>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>(".modal-actions button:last-child")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); previous?.focus(); };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal card" role="dialog" aria-modal="true" aria-label={title} ref={panel} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}
