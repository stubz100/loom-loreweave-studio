// Thin client for the local orchestrator (FastAPI on 127.0.0.1, R101).
//
// In dev (`npm run dev`) the orchestrator runs separately; URL from
// VITE_LOOM_ORCH_URL or the default. In the packaged app the Tauri shell spawns
// the sidecar and (P0-16) will inject the URL + token; for now the fixed
// loopback URL is used and the token is not yet enforced.

const DEFAULT_URL = "http://127.0.0.1:8765";

// The Tauri shell injects these into the webview on READY (window.__LOOM_*); in
// `npm run dev` they come from .env / .env.local (VITE_*). See lib.rs + config.py.
declare global {
  interface Window {
    __LOOM_TOKEN__?: string;
    __LOOM_ORCH_URL__?: string;
  }
}

export function orchestratorUrl(): string {
  if (typeof window !== "undefined" && window.__LOOM_ORCH_URL__) return window.__LOOM_ORCH_URL__;
  // @ts-expect-error - import.meta.env is provided by Vite
  const fromEnv = import.meta.env?.VITE_LOOM_ORCH_URL as string | undefined;
  return fromEnv || DEFAULT_URL;
}

export function orchestratorToken(): string {
  if (typeof window !== "undefined" && window.__LOOM_TOKEN__) return window.__LOOM_TOKEN__;
  // @ts-expect-error - import.meta.env is provided by Vite
  const fromEnv = import.meta.env?.VITE_LOOM_ORCH_TOKEN as string | undefined;
  return fromEnv || "";
}

export interface Health {
  status: string;
  app_version: string;
  schema_version: number;
  pid: number;
  uptime_s: number;
}

export interface JobResult {
  ok: boolean;
  returncode: number;
  outputs: string[];
  manifest_path: string | null;
  duration_s: number | null;
  stderr_tail: string;
  manifest_status?: string | null;
  error?: string | null;
  output_name?: string;
  seed?: number | null;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "canceled";

export interface Job {
  id: string;
  pipeline: string;
  mode: string;
  params: Record<string, unknown>;
  status: JobStatus;
  progress: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  wall_s: number | null;
  result: JobResult | null;
  log_tail: string;
  batch_id: string;
  index: number;
  batch_size: number;
}

export interface JobsResponse {
  jobs: Record<string, Job>;
  counts: Record<JobStatus, number>;
}

export interface GenerateRequest {
  prompt: string;
  count?: number;
  seed?: number | null;
  width?: number;
  height?: number;
}

export interface GenerateResponse {
  batch_id: string;
  count: number;
  job_ids: string[];
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${orchestratorUrl()}/health`, { signal });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return (await res.json()) as Health;
}

export async function generate(req: GenerateRequest): Promise<GenerateResponse> {
  const res = await fetch(`${orchestratorUrl()}/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Loom-Token": orchestratorToken(),
    },
    body: JSON.stringify(req),
  });
  if (res.status === 401) {
    throw new Error("401 unauthorized — orchestrator token missing/mismatched (set .env.local)");
  }
  if (!res.ok) throw new Error(`generate ${res.status}: ${await res.text()}`);
  return (await res.json()) as GenerateResponse;
}

export async function listJobs(signal?: AbortSignal): Promise<JobsResponse> {
  const res = await fetch(`${orchestratorUrl()}/jobs`, { signal });
  if (!res.ok) throw new Error(`jobs ${res.status}`);
  return (await res.json()) as JobsResponse;
}

export async function cancelJob(id: string): Promise<void> {
  const res = await fetch(`${orchestratorUrl()}/jobs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  // 409 = already finished/unknown — treat as a no-op.
  if (!res.ok && res.status !== 409) throw new Error(`cancel ${res.status}`);
}

export function outputUrl(name: string): string {
  // name may be a per-job subpath (job_id/file.png); encode each segment.
  const enc = name.split("/").map(encodeURIComponent).join("/");
  return `${orchestratorUrl()}/outputs/${enc}`;
}
