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

/** Per-output metadata for batch jobs (parallel to output_names) — each Stage-B image's
 * frozen coverage_cell + its own seed, echoed from the worker's batch manifest. */
export interface OutputMeta {
  coverage_cell?: CoverageCell;
  seed?: number | null;
  index?: number;
  method?: string | null;
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
  output_names?: string[];   // multi cast / batch job: all outputs (one job → N)
  output_meta?: Record<string, OutputMeta>;
  // Batch jobs: the run's item counts — failed/skipped > 0 = a PARTIAL dataset
  // (status "stopped" = the user's graceful ⏹). Drives the Stage B/C warning banner.
  batch?: { count: number; ok: number; failed: number; skipped: number; status?: string | null } | null;
  seed?: number | null;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "canceled";

export interface Job {
  id: string;
  pipeline: string;
  mode: string;
  params: Record<string, unknown>;
  requester_id?: string;
  profile_version_id?: string | null;
  stage?: string | null;
  coverage_cell?: CoverageCell | null;   // Stage-B recipe cell (P1/M3)
  status: JobStatus;
  progress: number;
  partial_outputs?: string[];   // interim results — a running multi cast streams its pool
  // Clean/polish post-pass chaining (2026-06-11): specs still to run after this job,
  // and — when this job IS a chained pass — its parent + pass name.
  post_passes?: Array<Record<string, unknown>>;
  chained_from?: string | null;
  pass?: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  wall_s: number | null;
  result: JobResult | null;
  log_tail: string;
  note?: string;
  retry_count?: number;
  vram_estimate_gb?: number;
  batch_id: string;
  index: number;
  batch_size: number;
}

export interface DiskStatus {
  state: "ok" | "warn" | "hard";
  blocked: boolean;
  reason: string | null;
  project: { used_gb: number; cap_gb: number; headroom_pct: number } | null;
  disk: { free_gb: number; total_gb: number; free_pct: number } | null;
  thresholds: { warn_pct: number; hard_pct: number };
}

// Why the queue is paused: "resume" = the resume-paused load (R88, automatic on every
// project open with pending work) | "user" = an explicit pause. Lets the dock say WHY.
export type PauseReason = "resume" | "user" | null;

export interface JobsResponse {
  jobs: Record<string, Job>;
  counts: Record<JobStatus, number>;
  paused: boolean;
  pause_reason?: PauseReason;
  vram_budget_gb: number;
  disk?: DiskStatus;
}

export interface QueueState {
  paused: boolean;
  pause_reason?: PauseReason;
  vram_budget_gb: number;
  counts: Record<JobStatus, number>;
}

export interface GenerateRequest {
  pipeline?: "zimage" | "multi" | "sd35";
  mode?: "t2i" | "ideate" | "img2img" | "inpaint";
  prompt: string;
  count?: number;
  num_candidates?: number;
  ideation_mode?: "fast" | "refined";
  seed?: number | null;
  width?: number;
  height?: number;
  model_name?: string | null;
  num_steps?: number | null;
  guidance_scale?: number | null;
  negative_prompt?: string | null;
  // P1/M3 Stage-B img2img/inpaint inputs (out/-relative names) + the catalog-validated
  // advanced tunables channel (keyed by GET /models param names; see ModelCatalog).
  init_image?: string | null;
  mask_image?: string | null;
  strength?: number | null;
  params?: Record<string, unknown>;
  asset_id?: string;
  version_id?: string;
  stage?: "A" | "B" | "C";
  apply_style?: boolean;
  dry_run?: boolean;
}

/** A chained clean/polish pass plan (returned by dry runs). */
export interface PostPassSpec {
  pass: string;
  backend: string;
  model_name?: string | null;
  strength?: number;
  prompt?: string | null;
  negative_prompt?: string | null;
  seed?: number;
}

/** What `/generate` returns for `dry_run: true` — the pre-flight review payload
 * (resolved prompt incl. the style fragment, the exact argv, planned job count). */
export interface GeneratePreview {
  dry_run: true;
  pipeline: string;
  count: number;
  num_candidates: number | null;
  argv: string[];
  prompt: string;
  post_passes?: PostPassSpec[];
  cwd: string;
  output_dir: string;
}

export interface StyleInfo {
  id: string;
  fragment: string;
  enabled_default: boolean;
}

export interface AssetSummary {
  id: string;
  name: string;
  asset_class: string;
  slug?: string;
  active_version: string;
  version_count: number;
}

export interface GenerateResponse {
  batch_id: string;
  count: number;
  job_ids: string[];
}

export interface ProjectFormat {
  aspect: [number, number];
  resolution: [number, number];
  fps: number;
  audio_master: { container: string; rate_hz: number; bits: number; channels: number };
}

export interface ProjectInfo {
  open: boolean;
  path?: string;
  id?: string;
  name?: string;
  format?: ProjectFormat;
  size_cap_gb?: number;
  free_space_gb?: number;
}

export interface FootprintReport {
  projected_master_gb: number;
  suggested_cap_gb: number;
  frames: number;
  cap_sufficient?: boolean;
  warning?: string;
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${orchestratorUrl()}/health`, { signal });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return (await res.json()) as Health;
}

export async function getProject(signal?: AbortSignal): Promise<ProjectInfo> {
  const res = await fetch(`${orchestratorUrl()}/project`, { signal });
  if (!res.ok) throw new Error(`project ${res.status}`);
  return (await res.json()) as ProjectInfo;
}

export async function getDisk(signal?: AbortSignal): Promise<DiskStatus> {
  const res = await fetch(`${orchestratorUrl()}/disk`, { signal });
  if (!res.ok) throw new Error(`disk ${res.status}`);
  return (await res.json()) as DiskStatus;
}

// --- P1: L1 style + L2 assets ---
export async function getStyle(signal?: AbortSignal): Promise<StyleInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible/style`, { signal });
  if (!res.ok) throw new Error(`style ${res.status}`);
  return (await res.json()) as StyleInfo;
}

export async function setStyle(fragment?: string, enabled_default?: boolean): Promise<StyleInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible/style`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ fragment, enabled_default }),
  });
  if (!res.ok) throw new Error(`set style ${res.status}: ${await res.text()}`);
  return (await res.json()) as StyleInfo;
}

export async function listAssets(signal?: AbortSignal): Promise<{ assets: AssetSummary[] }> {
  const res = await fetch(`${orchestratorUrl()}/assets`, { signal });
  if (!res.ok) throw new Error(`assets ${res.status}`);
  return await res.json();
}

export async function createAsset(name: string, asset_class = "characters"): Promise<{ profile: AssetSummary }> {
  const res = await fetch(`${orchestratorUrl()}/assets`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ name, asset_class }),
  });
  if (!res.ok) throw new Error(`create asset ${res.status}: ${await res.text()}`);
  return await res.json();
}

// --- P1/M2: Stage-A casting (candidates + hero ★ persisted into version.json) ---
export interface CastingCandidate {
  id: string;
  job_id: string;
  file: string;
  source_output?: string;
  pipeline?: string | null;
  seed?: number | null;
  starred: boolean;
  added_at?: string;
}

// P1/M3: the frozen coverage-cell metadata (P1→P2 contract; mirrors orchestrator/coverage.py).
export interface CoverageCell {
  shot_size: "face_closeup" | "portrait" | "waist_up" | "full_body";
  angle: "front" | "three_quarter_left" | "three_quarter_right" | "profile_left" | "profile_right" | "back";
  expression: "neutral" | "smile" | "serious" | "sad" | "surprised";
  background: string;
}

// A Stage-C curated ref (the future LoRA corpus). ref_set was string[] pre-M3; it now carries
// each kept image's coverage_cell + provenance (version.schema.json).
export interface RefItem {
  id: string;
  file: string;
  coverage_cell: CoverageCell;
  source_output?: string;
  job_id?: string;
  pipeline?: string | null;
  method?: string | null;
  seed?: number | null;
  added_at?: string;
}

export interface ProfileVersion {
  id: string;
  name: string;
  finalized: boolean;
  prompt_template: string;
  anchor_ref?: string | null;
  ref_set: RefItem[];
  casting: CastingCandidate[];
}

export interface AssetDetail {
  profile: AssetSummary;
  versions: ProfileVersion[];
}

export async function getAsset(assetId: string, signal?: AbortSignal): Promise<AssetDetail> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}`, { signal });
  if (!res.ok) throw new Error(`asset ${res.status}`);
  return (await res.json()) as AssetDetail;
}

/** Star (or un-star) a completed candidate into a version's casting set. `output` selects
 * a specific candidate from a multi pool (omit for a single-output zimage job). */
export async function starCandidate(
  assetId: string,
  jobId: string,
  starred = true,
  output?: string,
  versionId?: string,
): Promise<ProfileVersion> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/casting/star`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({
      job_id: jobId,
      starred,
      output: output ?? null,
      version_id: versionId ?? null,
    }),
  });
  if (!res.ok) throw new Error(`star ${res.status}: ${await res.text()}`);
  return (await res.json()) as ProfileVersion;
}

/** Serve a saved casting candidate image from the version's casting/ dir. */
export function castingUrl(assetId: string, file: string, versionId?: string): string {
  const q = versionId ? `?version_id=${encodeURIComponent(versionId)}` : "";
  return `${orchestratorUrl()}/assets/${assetId}/casting/${encodeURIComponent(file)}${q}`;
}

// --- P1/M3: Stage-B expansion + Stage-C curation + Save -------------------------
const RECIPE_PRESETS = ["comprehensive", "full_coverage", "portrait_heavy", "full_body", "npc_lite"] as const;
export type RecipePreset = (typeof RECIPE_PRESETS)[number];
export const recipePresets = RECIPE_PRESETS;

export interface StageBRequest {
  version_id?: string;
  preset?: RecipePreset;
  character_clause?: string | null;
  pipeline?: "zimage" | "sd35";
  model_name?: string | null;
  strength?: number;
  width?: number;
  height?: number;
  base_seed?: number | null;
  apply_style?: boolean;
  params?: Record<string, unknown>;
  dry_run?: boolean;
}

async function postAsset(assetId: string, path: string, body: unknown): Promise<ProfileVersion> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} ${res.status}: ${await res.text()}`);
  return (await res.json()) as ProfileVersion;
}

/** Fire Stage-B expansion: recipe → ONE batch img2img job covering every cell (the
 * worker loads the model once and loops; `items` = the cell count). */
export async function stageB(assetId: string, body: StageBRequest): Promise<{ batch_id: string; count: number; items?: number; job_ids: string[]; kept_target: number[] }> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/stage-b`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`stage-b ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** Stage-B dry-run payload — the pre-flight review (recipe size, hero, first cell + argv). */
export interface StageBPreview {
  dry_run: true;
  preset: string;
  pipeline: string;
  planned_jobs: number;     // 1 — the whole recipe runs as a single batch job
  items?: number;           // cells in that batch (model loads once)
  post_passes?: PostPassSpec[];
  kept_target: number[];
  hero: string;
  first_cell: { index: number; coverage_cell: CoverageCell; prompt: string; method: string; seed: number };
  first_argv: string[];
}

export async function stageBPreview(assetId: string, body: StageBRequest): Promise<StageBPreview> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/stage-b`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ ...body, dry_run: true }),
  });
  if (!res.ok) throw new Error(`stage-b preview ${res.status}: ${await res.text()}`);
  return (await res.json()) as StageBPreview;
}

/** Keep a completed Stage-B candidate into the curated ref_set (records its coverage_cell). */
export function keepRef(assetId: string, jobId: string, output?: string, versionId?: string) {
  return postAsset(assetId, "refs/keep", { job_id: jobId, output: output ?? null, version_id: versionId ?? null });
}

/** Cull (un-keep) a curated ref by id. */
export function cullRef(assetId: string, refId: string, versionId?: string) {
  return postAsset(assetId, "refs/cull", { ref_id: refId, version_id: versionId ?? null });
}

/** Save AssetProfile (persist the identity clause; Saved, not Finalized). */
export function saveProfile(assetId: string, promptTemplate?: string, versionId?: string) {
  return postAsset(assetId, "save", { prompt_template: promptTemplate ?? null, version_id: versionId ?? null });
}

/** Serve a curated ref image from the version's refs/ dir. */
export function refUrl(assetId: string, file: string, versionId?: string): string {
  const q = versionId ? `?version_id=${encodeURIComponent(versionId)}` : "";
  return `${orchestratorUrl()}/assets/${assetId}/refs/${encodeURIComponent(file)}${q}`;
}

// --- P1/M3: model catalog (GET /models) — variants + tunable params per pipeline ---
export interface ModelVariant {
  id: string;
  repo_id: string;
  gated: boolean;
  note?: string;
  [k: string]: unknown;
}
/** One tunable parameter spec from the catalog (drives the UI's parameter controls). */
export interface ParamSpec {
  name: string;
  flag?: string;
  type: "int" | "float" | "str" | "enum" | "flag" | "image";
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  choices?: string[];
  modes?: string[];
  note?: string;
  advanced?: boolean;
}

export interface PipelineModels {
  variants: ModelVariant[];
  params: ParamSpec[];
  modes?: string[];
  loom_access?: string;
}
export type ModelCatalog = Record<string, PipelineModels>;

export async function getModels(signal?: AbortSignal): Promise<ModelCatalog> {
  const res = await fetch(`${orchestratorUrl()}/models`, { signal });
  if (!res.ok) throw new Error(`models ${res.status}`);
  return ((await res.json()).models ?? {}) as ModelCatalog;
}

export interface ComponentInfo {
  id: string;
  kind: "code" | "model_weight";
  phase: string;
  present: boolean;
  state: "phase-essential" | "installed-but-unavailable" | "missing" | "declared";
  detail: string;
}

export interface LaunchReport {
  active_phases: string[];
  code_ok: boolean;
  weights_ok: boolean;
  launch_ok: boolean;
  blocking: { id: string; detail: string }[];
  weights_missing: string[];
  components: ComponentInfo[];
}

export async function getComponents(signal?: AbortSignal): Promise<LaunchReport> {
  const res = await fetch(`${orchestratorUrl()}/components`, { signal });
  if (!res.ok) throw new Error(`components ${res.status}`);
  return (await res.json()) as LaunchReport;
}

export async function fetchComponents(): Promise<{ report: LaunchReport }> {
  const res = await fetch(`${orchestratorUrl()}/components/fetch`, {
    method: "POST",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  if (!res.ok) throw new Error(`fetch components ${res.status}: ${await res.text()}`);
  return (await res.json()) as { report: LaunchReport };
}

export async function createProject(
  dest: string,
  name: string,
  size_cap_gb?: number,
): Promise<ProjectInfo> {
  const res = await fetch(`${orchestratorUrl()}/project`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ dest, name, ...(size_cap_gb ? { size_cap_gb } : {}) }),
  });
  if (!res.ok) throw new Error(`create project ${res.status}: ${await res.text()}`);
  return (await res.json()) as ProjectInfo;
}

export interface ProjectListEntry {
  path: string;
  active: boolean;
  exists: boolean;
  name: string | null;
  id: string | null;
  size_cap_gb: number | null;
}

export async function listProjects(signal?: AbortSignal): Promise<{ active: string | null; projects: ProjectListEntry[] }> {
  const res = await fetch(`${orchestratorUrl()}/projects`, { signal });
  if (!res.ok) throw new Error(`projects ${res.status}`);
  return await res.json();
}

export async function forgetProject(path: string): Promise<void> {
  const res = await fetch(`${orchestratorUrl()}/project/forget`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`forget ${res.status}`);
}

/** Close the active project — the app runs project-less until one is created/opened;
 * a relaunch won't auto-reopen it. 409 while a job is running. */
export async function closeProject(): Promise<void> {
  const res = await fetch(`${orchestratorUrl()}/project/close`, {
    method: "POST",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  if (!res.ok) throw new Error(`close project ${res.status}: ${await res.text()}`);
}

export async function openProject(path: string): Promise<ProjectInfo> {
  const res = await fetch(`${orchestratorUrl()}/project/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`open project ${res.status}: ${await res.text()}`);
  return (await res.json()) as ProjectInfo;
}

export async function estimateFootprint(
  length_s: number,
  width: number,
  height: number,
  fps: number,
  size_cap_gb?: number,
): Promise<FootprintReport> {
  const res = await fetch(`${orchestratorUrl()}/project/estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ length_s, width, height, fps, size_cap_gb }),
  });
  if (!res.ok) throw new Error(`estimate ${res.status}`);
  return (await res.json()) as FootprintReport;
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
  if (res.status === 409) {
    throw new Error("no project open — create or open a project first");
  }
  if (res.status === 507) {
    throw new Error(`disk hard-stop — ${await res.text()}`);
  }
  if (res.status === 412) {
    throw new Error(`model weights missing — fetch them first (${await res.text()})`);
  }
  if (!res.ok) throw new Error(`generate ${res.status}: ${await res.text()}`);
  return (await res.json()) as GenerateResponse;
}

/** Pre-flight a generate request without spending GPU (dry_run) — review the resolved
 * prompt (style prepend visible), the exact worker argv, and the planned job count. */
export async function generatePreview(req: GenerateRequest): Promise<GeneratePreview> {
  return (await generate({ ...req, dry_run: true })) as unknown as GeneratePreview;
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

/** Gracefully stop a running BATCH job: finishes the current image, keeps everything
 * already generated (vs cancel = kill + discard the partial dir). */
export async function stopJob(id: string): Promise<void> {
  const res = await fetch(`${orchestratorUrl()}/jobs/${encodeURIComponent(id)}/stop`, {
    method: "POST",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  // 409 = not a running batch — treat as a no-op.
  if (!res.ok && res.status !== 409) throw new Error(`stop ${res.status}`);
}

export async function deleteJob(id: string): Promise<void> {
  // Delete a finished generation + ALL its artifacts (out dir, manifest, log, queue
  // entry, lineage edge) — orchestrator-owned, atomic, no orphans.
  const res = await fetch(`${orchestratorUrl()}/jobs/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  if (!res.ok) throw new Error(`delete ${res.status}: ${await res.text()}`);
}

async function queueControl(action: "pause" | "unpause"): Promise<QueueState> {
  const res = await fetch(`${orchestratorUrl()}/queue/${action}`, {
    method: "POST",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  if (!res.ok) throw new Error(`${action} ${res.status}`);
  return (await res.json()) as QueueState;
}

export const pauseQueue = () => queueControl("pause");
export const unpauseQueue = () => queueControl("unpause");

export function outputUrl(name: string): string {
  // name may be a per-job subpath (job_id/file.png); encode each segment.
  const enc = name.split("/").map(encodeURIComponent).join("/");
  return `${orchestratorUrl()}/outputs/${enc}`;
}
