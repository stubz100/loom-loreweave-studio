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
 * frozen coverage_cell + its own seed, echoed from the worker's batch manifest.
 * Postproc jobs (M3.5 birefnet) carry the artifact `role` instead (matte/cutout/bgmask). */
export interface OutputMeta {
  coverage_cell?: CoverageCell;
  seed?: number | null;
  index?: number;
  method?: string | null;
  /** the EXACT per-cell prompt this image was generated with (Stage-B batch items carry
   * their own prompt; the job-level prompt is only the `[dataset …]` summary label). */
  prompt?: string;
  role?: string;
  /** identity pass (M4): "locked" | "no_face_passthrough" + the measured anchor cosine. */
  identity?: string;
  anchor_cos?: number;
  /** restore pass (M6): "restored" | "no_face_passthrough" | "portrait_crop" + face count. */
  restore?: string;
  faces?: number;
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
  pipeline?: "zimage" | "multi" | "sd35" | "flux2";
  mode?: "t2i" | "ideate" | "img2img" | "inpaint" | "ref";
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
  style_id?: string;        // which L1 style to apply; omit = the active default
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
  global_negative?: string;
}

/** A named L1 style in the collection (2026-06-13). */
export interface StyleEntry {
  id: string;
  name: string;
  fragment: string;
  global_negative?: string;
}
export interface StylesInfo {
  styles: StyleEntry[];
  active_style_id: string;
  enabled_default: boolean;
}

/** M8 — the full L1 World record. */
export interface SpineCharacter {
  id: string;
  name: string;
  snippet: string;
  linked_asset_id?: string | null;
}
export interface BibleInfo {
  id: string;
  world?: string;
  style: StyleInfo;
  spine?: { premise?: string; characters?: SpineCharacter[] };
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

export async function setStyle(fragment?: string, enabled_default?: boolean,
                               global_negative?: string, style_id?: string): Promise<StyleInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible/style`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ fragment, enabled_default, global_negative, style_id }),
  });
  if (!res.ok) throw new Error(`set style ${res.status}: ${await res.text()}`);
  return (await res.json()) as StyleInfo;
}

// --- L1 style COLLECTION (2026-06-13): multiple named styles --------------------
export async function getStyles(signal?: AbortSignal): Promise<StylesInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible/styles`, { signal });
  if (!res.ok) throw new Error(`styles ${res.status}`);
  return (await res.json()) as StylesInfo;
}

async function stylesMutate(path: string, method: string, body?: unknown): Promise<StylesInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible/styles${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) throw new Error(`styles ${res.status}: ${await res.text()}`);
  return (await res.json()) as StylesInfo;
}

export const addStyle = (name: string, fragment = "", global_negative = "") =>
  stylesMutate("", "POST", { name, fragment, global_negative });
export const updateStyle = (id: string, patch: Partial<Pick<StyleEntry, "name" | "fragment" | "global_negative">>) =>
  stylesMutate(`/${encodeURIComponent(id)}`, "PUT", patch);
export const deleteStyle = (id: string) =>
  stylesMutate(`/${encodeURIComponent(id)}`, "DELETE");
export const setActiveStyle = (style_id: string) =>
  stylesMutate("/active", "POST", { style_id });

// --- M8: L1 World (world prose + global negative + story spine) ------------------

export async function getBible(signal?: AbortSignal): Promise<BibleInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible`, { signal });
  if (!res.ok) throw new Error(`bible ${res.status}`);
  return (await res.json()) as BibleInfo;
}

async function bibleWrite(path: string, method: string, body?: unknown): Promise<BibleInfo> {
  const res = await fetch(`${orchestratorUrl()}/bible/${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} ${res.status}: ${await res.text()}`);
  return (await res.json()) as BibleInfo;
}

export const setWorld = (world: string) => bibleWrite("world", "PUT", { world });
export const setPremise = (premise: string) => bibleWrite("spine/premise", "PUT", { premise });
export const upsertSpineCharacter = (b: { character_id?: string; name?: string; snippet?: string }) =>
  bibleWrite("spine/character", "POST", b);
export const removeSpineCharacter = (cid: string) =>
  bibleWrite(`spine/character/${encodeURIComponent(cid)}`, "DELETE");

/** Materialize a spine character into a stub AssetProfile (R55). */
export async function createSpineStub(characterId: string):
    Promise<{ profile: AssetSummary; linked_asset_id: string }> {
  const res = await fetch(`${orchestratorUrl()}/bible/spine/character/stub`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ character_id: characterId }),
  });
  if (!res.ok) throw new Error(`stub ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** Push the spine snippet into the linked profile (manual, R55). */
export async function resyncSpineStub(characterId: string):
    Promise<{ linked_asset_id: string; prompt_template: string }> {
  const res = await fetch(
    `${orchestratorUrl()}/bible/spine/character/${encodeURIComponent(characterId)}/resync`,
    { method: "POST", headers: { "X-Loom-Token": orchestratorToken() } });
  if (!res.ok) throw new Error(`resync ${res.status}: ${await res.text()}`);
  return await res.json();
}

// --- M9: profile export / import (R66/R67) ---------------------------------------

/** Fetch a profile bundle (.zip of profile + all versions) WITH the auth token; the caller
 * turns the Blob into an object URL to trigger the download. Export is token-gated (M9
 * review — it packages every version + file), so a plain anchor href can't carry it. */
export async function exportProfile(assetId: string): Promise<Blob> {
  const res = await fetch(
    `${orchestratorUrl()}/assets/${encodeURIComponent(assetId)}/export`,
    { headers: { "X-Loom-Token": orchestratorToken() } });
  if (!res.ok) throw new Error(`export ${res.status}: ${await res.text()}`);
  return await res.blob();
}

/** Import a bundle (.zip bytes) as a new profile — rename on collision (R67). */
export async function importProfile(bytes: ArrayBuffer):
    Promise<{ profile: AssetSummary; renamed_from: string | null }> {
  const res = await fetch(`${orchestratorUrl()}/assets/import`, {
    method: "POST",
    headers: { "Content-Type": "application/zip", "X-Loom-Token": orchestratorToken() },
    body: bytes,
  });
  if (!res.ok) throw new Error(`import ${res.status}: ${await res.text()}`);
  return await res.json();
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

/** M4 face anchor (R94): the version's chosen face image, copied into faces/. */
export interface AnchorInfo {
  file: string;
  source_output?: string;
  job_id?: string;
  set_at: string;
  /** Durable verification stamp (M4 review): a done identity job proved this anchor
   * has a detectable face. Survives queue pruning; a re-pick clears it. */
  verified_at?: string;
  verified_by_job?: string;
}

/** M0c: one persisted postprocess step in a base image's stack (source/output lineage). */
export interface PostprocStep {
  id: string;
  preset: "clean" | "refine" | "custom" | "restore";
  backend: string;
  mode: string;
  params: Record<string, unknown>;
  mask?: string | null;
  requires_mask?: boolean;
  source: string;                 // out/-relative input image (base, or prior step's output)
  output?: string | null;         // produced image once the step's job completes
  job_id?: string | null;
  status: "configured" | "queued" | "running" | "done" | "failed";
  added_at?: string;
}

/** M0c: an ordered postprocess stack anchored to a base image (out/-relative). */
export interface PostprocStack { base: string; steps: PostprocStep[]; }

export interface ProfileVersion {
  id: string;
  name: string;
  finalized: boolean;
  prompt_template: string;
  anchor_ref?: string | null;
  anchor?: AnchorInfo | null;
  ref_set: RefItem[];
  casting: CastingCandidate[];
  /** P1-12: out/-relative output names rejected during Stage-C culling (persistent). */
  rejected?: string[];
}

/** P1-12: mark/unmark a Stage-B candidate output rejected (persistent cull-from-view). */
export function rejectOutput(assetId: string, jobId: string, output?: string,
                             rejected = true, versionId?: string) {
  return postAsset(assetId, "refs/reject", {
    job_id: jobId, output: output ?? null, rejected, version_id: versionId ?? null,
  });
}

/** Set the version's face anchor from an owned job output (M4, R94). */
export function setAnchor(assetId: string, jobId: string, output?: string, versionId?: string) {
  return postAsset(assetId, "anchor", { job_id: jobId, output: output ?? null, version_id: versionId ?? null });
}

/** Clear the face anchor (opt the version out of the identity lock, R93). */
export function clearAnchor(assetId: string, versionId?: string) {
  return postAsset(assetId, "anchor", { job_id: null, version_id: versionId ?? null });
}

/** M6.1: derive a restored 512² face PORTRAIT from an owned output (a better anchor
 * base than a small face in a full-body shot); anchor the resulting tile. */
export async function deriveFacePortrait(assetId: string, jobId: string, output?: string,
                                         versionId?: string):
    Promise<{ job_id: string; source_output: string }> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/anchor/derive`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ job_id: jobId, output: output ?? null,
                           version_id: versionId ?? null }),
  });
  if (!res.ok) throw new Error(`derive portrait ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** Serve the version's anchor image. */
export function anchorUrl(assetId: string, versionId?: string): string {
  const q = versionId ? `?version_id=${encodeURIComponent(versionId)}` : "";
  return `${orchestratorUrl()}/assets/${assetId}/anchor/file${q}`;
}

// --- M5: profile versioning (copy-on-create, finalize/lock, active switch) --------

/** Deep-copy `parentVersionId` (default: the active version) into a fresh, unlocked
 * version that becomes active (R50/R58/R59). */
export async function createVersion(assetId: string, name?: string, parentVersionId?: string):
    Promise<{ profile: { active_version: string; versions: string[] }; version: ProfileVersion }> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ name: name ?? null, parent_version_id: parentVersionId ?? null }),
  });
  if (!res.ok) throw new Error(`create version ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** Finalize = pure-intent lock (R60): the version becomes immutable. Idempotent. */
export async function finalizeVersion(assetId: string, versionId: string): Promise<ProfileVersion> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/versions/${encodeURIComponent(versionId)}/finalize`, {
    method: "POST",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  if (!res.ok) throw new Error(`finalize ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** Switch the profile's active version — everything downstream scopes to it. */
export async function activateVersion(assetId: string, versionId: string):
    Promise<{ active_version: string }> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/versions/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ version_id: versionId }),
  });
  if (!res.ok) throw new Error(`activate ${res.status}: ${await res.text()}`);
  return await res.json();
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
  /** "flux2" = identity-preserving reference conditioning (the hero rides as a reference, §11). */
  pipeline?: "zimage" | "sd35" | "flux2";
  model_name?: string | null;
  strength?: number;
  width?: number;
  height?: number;
  base_seed?: number | null;
  apply_style?: boolean;
  style_id?: string;        // which L1 style to apply; omit = the active default
  params?: Record<string, unknown>;
  dry_run?: boolean;
  /** M3.5 — "mixed" realizes inpaint-method cells against the hero's bg mask
   * (background diversity); needs `bg_mask` (a *_bgmask.png from a matte job). */
  realize?: "img2img" | "mixed";
  bg_mask?: string | null;
  inpaint_strength?: number;
  /** M4 — identity-lock pass (R93): omit = on when the version has an anchor;
   * false = opt out; true = require (422 without an anchor). */
  identity?: boolean;
  identity_min_det_score?: number;
}

/** M7 — video-sketch harvest: one ltxv i2v job from the hero ★ aimed at a target
 * coverage cell; a chained frame_harvest pass extracts stills carrying that cell. */
export async function sketchHero(assetId: string, body: {
  version_id?: string; shot_size?: string; angle?: string; expression?: string;
  motion_prompt?: string | null; character_clause?: string | null;
  every?: number; max_frames?: number; apply_style?: boolean; style_id?: string;
  params?: Record<string, unknown>;
}): Promise<{ job_id: string; cell: CoverageCell; prompt: string }> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/stage-b/sketch`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`sketch ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** Matte the version's hero ★ (M3.5): one birefnet job → matte / cutout / bg mask. */
export async function matteHero(assetId: string, versionId?: string, params?: Record<string, unknown>):
    Promise<{ job_id: string; batch_id: string; hero: string }> {
  const res = await fetch(`${orchestratorUrl()}/assets/${assetId}/stage-b/matte`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ version_id: versionId ?? null, params: params ?? {} }),
  });
  if (!res.ok) throw new Error(`matte ${res.status}: ${await res.text()}`);
  return await res.json();
}

/** M0c: the project's postprocess stacks (project-level — works on ANY image). */
export async function getPostprocStacks(): Promise<PostprocStack[]> {
  const res = await fetch(`${orchestratorUrl()}/postproc/stacks`);
  if (!res.ok) throw new Error(`postproc/stacks ${res.status}: ${await res.text()}`);
  return ((await res.json()) as { stacks: PostprocStack[] }).stacks;
}

/** M0c: configure (persist, NOT queue) a postprocess step onto a base image's stack. */
export async function addPostprocStep(body: {
  base: string; preset?: PostprocStep["preset"]; backend?: string;
  params?: Record<string, unknown>; mask?: string | null; requires_mask?: boolean;
}): Promise<PostprocStack[]> {
  const res = await fetch(`${orchestratorUrl()}/postproc/step`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`postproc/step ${res.status}: ${await res.text()}`);
  return ((await res.json()) as { stacks: PostprocStack[] }).stacks;
}

/** M0c: fire a configured step's job over its source image (the runner records its output).
 * `requesterId`/`stage` route the produced tile into the caller's current grid (a character
 * version + bootstrap stage); omit for the Sandbox (the project default). */
export async function queuePostprocStep(stepId: string, requesterId?: string,
                                        stage?: string): Promise<PostprocStack[]> {
  const res = await fetch(`${orchestratorUrl()}/postproc/step/${stepId}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Loom-Token": orchestratorToken() },
    body: JSON.stringify({ requester_id: requesterId ?? null, stage: stage ?? null }),
  });
  if (!res.ok) throw new Error(`postproc queue ${res.status}: ${await res.text()}`);
  return ((await res.json()) as { stacks: PostprocStack[] }).stacks;
}

/** M0c: remove the LAST step of its stack (the chain tail). */
export async function removePostprocStep(stepId: string): Promise<PostprocStack[]> {
  const res = await fetch(`${orchestratorUrl()}/postproc/step/${stepId}`, {
    method: "DELETE",
    headers: { "X-Loom-Token": orchestratorToken() },
  });
  if (!res.ok) throw new Error(`postproc remove ${res.status}: ${await res.text()}`);
  return ((await res.json()) as { stacks: PostprocStack[] }).stacks;
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
  planned_jobs: number;     // 1, or 2 under realize="mixed" (img2img + inpaint batches)
  items?: number;           // cells across the batch job(s) (model loads once per job)
  split?: Record<string, number>;   // cells per mode, e.g. { img2img: 9, inpaint: 8 }
  realize?: "img2img" | "mixed";
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

/** Keep a completed Stage-B candidate into the curated ref_set (records its coverage_cell).
 * `allowUnlocked` (default true from the UI): curate ANY output, including a pre-pass image
 * whose chained clean/polish/identity/restore passes are still pending/un-run — keeping is a
 * deliberate human action (user 2026-06-13). The backend keeps the guard for API callers. */
export function keepRef(assetId: string, jobId: string, output?: string, versionId?: string,
                        allowUnlocked = true) {
  return postAsset(assetId, "refs/keep", { job_id: jobId, output: output ?? null,
                                           version_id: versionId ?? null,
                                           allow_unlocked: allowUnlocked });
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
  /** clean/polish post-pass param — orchestrator-chained, never a worker flag. */
  post?: boolean;
}

/** M0d Part B — a flux2 "Sampling" preset (one-click model_name + steps + guidance). */
export interface Flux2SamplingPreset {
  id: string;
  label: string;
  model_name: string;
  num_steps: number;
  guidance: number;
  default?: boolean;
  recommended?: boolean;
  note?: string;
}

export interface PipelineModels {
  variants: ModelVariant[];
  params: ParamSpec[];
  modes?: string[];
  loom_access?: string;
  /** flux2 only (M0d Part B): the Sampling pull-down rows. */
  sampling_presets?: Flux2SamplingPreset[];
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
