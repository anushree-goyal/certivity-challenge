import type { CaseInfo, Health, RunStatus, RunSummary, RunView } from "./types";

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.reason || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  health: () => getJSON<Health>("/api/health"),
  cases: () => getJSON<CaseInfo[]>("/api/cases"),
  runs: () => getJSON<RunSummary[]>("/api/runs"),
  status: (id: string) => getJSON<RunStatus>(`/api/runs/${id}`),
  results: (id: string) => getJSON<RunView>(`/api/runs/${id}/results`),
  async createRun(form: FormData): Promise<{ run_id: string; origin: string }> {
    const res = await fetch("/api/runs", { method: "POST", body: form });
    const body = await res.json();
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
    return body;
  },
  submissionUrl: (id: string) => `/api/runs/${id}/submission`,
  inputUrl: (id: string, name: string) => `/api/runs/${id}/input/${encodeURIComponent(name)}`,
  pageUrl: (documentId: string, pdfPageIndex: number, mode: string, snapshot?: string | null) =>
    `/api/documents/${encodeURIComponent(documentId)}/pages/${pdfPageIndex}?${query(mode, snapshot)}`,
  // Browser PDF viewers number pages physically from 1, so the zero-based index maps to #page=index+1.
  pdfUrl: (documentId: string, pdfPageIndex: number, mode: string, snapshot?: string | null) =>
    `/api/documents/${encodeURIComponent(documentId)}/pdf?${query(mode, snapshot)}#page=${pdfPageIndex + 1}`,
};

function query(mode: string, snapshot?: string | null) {
  const q = new URLSearchParams({ mode });
  if (snapshot) q.set("snapshot_id", snapshot);
  return q.toString();
}
