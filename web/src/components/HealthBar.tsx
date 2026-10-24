import type { Health } from "../types";

function Pill({ ok, label, title }: { ok: boolean; label: string; title?: string }) {
  return <span className={`pill ${ok ? "ok" : "bad"}`} title={title}>{ok ? "●" : "○"} {label}</span>;
}

export function HealthBar({ health, error }: { health: Health | null; error: string | null }) {
  if (error) return <div className="health"><Pill ok={false} label="API unreachable" title={error} /></div>;
  if (!health) return <div className="health muted">Checking…</div>;
  const missing = Object.entries(health.modules).filter(([, m]) => !m.ready && m.required).map(([n]) => n);
  return (
    <div className="health">
      <Pill ok={health.participant_package.ready} label="Package" title={health.participant_package.path} />
      <Pill ok={health.corpus.ready}
            label={health.corpus.ready ? `Corpus ${health.corpus.snapshot_id ?? ""} (${health.corpus.base_documents} docs)` : "Corpus"}
            title={health.corpus.error} />
      <Pill ok={health.llm.ready} label={health.llm.ready ? `Model ${health.llm.identity ?? ""}` : "Model"} title={health.llm.error} />
      <Pill ok={health.real_pipeline_ready} label={health.real_pipeline_ready ? "Real pipeline ready" : "Real pipeline not ready"}
            title={missing.length ? `Missing: ${missing.join(", ")}` : undefined} />
    </div>
  );
}
