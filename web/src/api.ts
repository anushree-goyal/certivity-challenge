import type {
  CaseInfo, EngineerReview, EngineerReviewStatus, ExportStatus, Health, RegulationsReport, ReimportStatus,
  RunComparison, RunStatus, RunSummary, RunView,
} from "./types";

export const OFFLINE_MESSAGE = "Server not reachable — is the app running?";

// Connectivity: any failed request (no response at all) marks the server unreachable; any response marks it back.
type Listener = (online: boolean) => void;
const listeners = new Set<Listener>();
let online = true;
function setOnline(value: boolean) {
  if (value === online) return;
  online = value;
  listeners.forEach((l) => l(value));
}
export function onConnectivity(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

async function request(url: string, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    setOnline(false);
    throw new Error(OFFLINE_MESSAGE);
  }
  setOnline(true);
  return res;
}

async function errorMessage(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  const detail = body.detail ?? body.reason;
  if (typeof detail === "string") return detail;
  if (detail) return JSON.stringify(detail);
  return res.status >= 500 ? `The server had an error (${res.status}).` : `${res.status} ${res.statusText}`;
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await request(url);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

async function sendJSON<T>(url: string, init: RequestInit): Promise<T> {
  const res = await request(url, init);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json().catch(() => ({}) as T);
}

export const api = {
  health: () => getJSON<Health>("/api/health"),
  cases: () => getJSON<CaseInfo[]>("/api/cases"),
  regulations: () => getJSON<RegulationsReport>("/api/regulations"),
  reimportStatus: () => getJSON<ReimportStatus>("/api/corpus/reimport"),
  startReimport: () => sendJSON<ReimportStatus>("/api/corpus/reimport", { method: "POST" }),
  runs: () => getJSON<RunSummary[]>("/api/runs"),
  status: (id: string) => getJSON<RunStatus>(`/api/runs/${id}`),
  results: (id: string) => getJSON<RunView>(`/api/runs/${id}/results`),
  exportStatus: (id: string) => getJSON<ExportStatus>(`/api/runs/${id}/export`),
  createRun: (form: FormData) => sendJSON<{ run_id: string; origin: string }>("/api/runs", { method: "POST", body: form }),
  cancel: (id: string) => sendJSON<unknown>(`/api/runs/${id}/cancel`, { method: "POST" }),
  compare: (a: string, b: string) => getJSON<RunComparison>(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  putReview: (id: string, draftId: string, status: EngineerReviewStatus, note: string) =>
    sendJSON<EngineerReview>(`/api/runs/${id}/reviews/${encodeURIComponent(draftId)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, note }),
    }),
  submissionUrl: (id: string) => `/api/runs/${id}/submission`,
  inputUrl: (id: string, name: string) => `/api/runs/${id}/input/${encodeURIComponent(name)}`,
  pageUrl: (documentId: string, pdfPageIndex: number, mode: string, snapshot?: string | null) =>
    `/api/documents/${encodeURIComponent(documentId)}/pages/${pdfPageIndex}?${query(mode, snapshot)}`,
  /** #page=N (physical, from the zero-based index) is the reliable part; #search= is a hint some viewers honour. */
  pdfUrl: (documentId: string, pdfPageIndex: number, mode: string, snapshot?: string | null, searchHint?: string) =>
    `/api/documents/${encodeURIComponent(documentId)}/pdf?${query(mode, snapshot)}#page=${pdfPageIndex + 1}` +
    (searchHint ? `&search=${encodeURIComponent(searchHint)}` : ""),
};

function query(mode: string, snapshot?: string | null) {
  const q = new URLSearchParams({ mode });
  if (snapshot) q.set("snapshot_id", snapshot);
  return q.toString();
}
