import { useEffect, useMemo, useRef, useState } from "react";
import {
  cancelJob,
  castingUrl,
  refUrl,
  closeProject,
  createAsset,
  createProject,
  deleteJob,
  estimateFootprint,
  fetchComponents,
  forgetProject,
  generate,
  generatePreview,
  getAsset,
  getComponents,
  getDisk,
  getHealth,
  getProject,
  getStyle,
  getStyles,
  addStyle,
  updateStyle,
  deleteStyle,
  setActiveStyle,
  getBible,
  exportProfile,
  importProfile,
  setWorld,
  setPremise,
  upsertSpineCharacter,
  removeSpineCharacter,
  createSpineStub,
  resyncSpineStub,
  type BibleInfo,
  type SpineCharacter,
  listAssets,
  listJobs,
  listProjects,
  openProject,
  outputUrl,
  setStyle,
  starCandidate,
  stopJob,
  unpauseQueue,
  stageB,
  stageBPreview,
  matteHero,
  sketchHero,
  setAnchor,
  clearAnchor,
  deriveFacePortrait,
  anchorUrl,
  keepRef,
  rejectOutput,
  createVersion,
  finalizeVersion,
  activateVersion,
  cullRef,
  saveProfile,
  getPostprocStacks,
  addPostprocStep,
  queuePostprocStep,
  removePostprocStep,
  type PostprocStack,
  type PostprocStep,
  getModels,
  recipePresets,
  type GeneratePreview,
  type GenerateRequest,
  type JobsResponse,
  type ModelCatalog,
  type AssetSummary,
  type CastingCandidate,
  type DiskStatus,
  type Health,
  type Job,
  type LaunchReport,
  type ParamSpec,
  type PauseReason,
  type ProjectInfo,
  type ProjectListEntry,
  type RecipePreset,
  type AnchorInfo,
  type RefItem,
  type ProfileVersion,
  type StageBPreview,
  type StageBRequest,
  type StyleInfo,
  type StylesInfo,
  type StyleEntry,
} from "./lib/orchestrator";
import { log } from "./lib/log";

// Coverage-cell vocabulary (frozen P1→P2 contract, coverage.py) — drives the Stage-C
// curation filters (P1-12). Keep in lockstep with the backend vocab.
const COV_SHOT_SIZES = ["face_closeup", "portrait", "waist_up", "full_body"];
const COV_ANGLES = ["front", "three_quarter_left", "three_quarter_right",
                    "profile_left", "profile_right", "back"];
const COV_EXPRESSIONS = ["neutral", "smile", "serious", "sad", "surprised"];

// M2 shell — three-pane layout + Job Queue dock (kb-loom-p0.md §10). The stage is
// a generate bar + a simple selectable result grid (the smoke target / casting-grid
// embryo, §12). Batches are fired at the orchestrator, which serializes them through
// one GPU worker; the UI polls /jobs and streams each result into the grid.

type Conn = "connecting" | "online" | "offline";

export default function App() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [health, setHealth] = useState<Health | null>(null);
  const [project, setProject] = useState<ProjectInfo | null>(null);

  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(3);
  // Generation pipeline — selectable everywhere since 2026-06-10 #3 (the sandbox is the
  // experimentation surface: multi casting + zimage/sd35 t2i all work unscoped).
  const [castPipeline, setCastPipeline] = useState<"multi" | "zimage" | "sd35" | "flux2">("multi");
  const [numCandidates, setNumCandidates] = useState(2);
  const [ideationMode, setIdeationMode] = useState<"fast" | "refined">("fast");
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [viewer, setViewer] = useState<string | null>(null);   // full-res lightbox image src
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState({ queued: 0, running: 0, done: 0, failed: 0, canceled: 0 });
  const [paused, setPaused] = useState(false);
  const [pauseReason, setPauseReason] = useState<PauseReason>(null);
  const [showQueue, setShowQueue] = useState(false);
  const [disk, setDisk] = useState<DiskStatus | null>(null);
  const [launch, setLaunch] = useState<LaunchReport | null>(null);
  const [fetching, setFetching] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [projectList, setProjectList] = useState<ProjectListEntry[]>([]);
  const [assets, setAssets] = useState<AssetSummary[]>([]);
  const [activeAsset, setActiveAsset] = useState<AssetSummary | null>(null);
  const [casting, setCasting] = useState<CastingCandidate[]>([]);
  const [style, setStyleState] = useState<StyleInfo | null>(null);
  const [styles, setStyles] = useState<StylesInfo | null>(null);   // L1 style collection
  const [genStyleId, setGenStyleId] = useState("");                // which style for this gen/edit
  const [styleDraft, setStyleDraft] = useState("");
  // M8 — L1 World view toggle (ASSETS bootstrap vs the WORLD authoring surface).
  const [view, setView] = useState<"assets" | "world">("assets");
  // M0b — L1 authoring sub-tab (rail nav for the World workspace): styles / world / spine.
  const [l1Tab, setL1Tab] = useState<"styles" | "world" | "spine">("styles");
  const [applyStyle, setApplyStyle] = useState(true);
  // P1/M3 bootstrap stages (A casting · B expansion · C curation) + their controls.
  const [stage, setStage] = useState<"A" | "B" | "C">("A");
  const [recipePreset, setRecipePreset] = useState<RecipePreset>("full_coverage");
  const [stageBPipeline, setStageBPipeline] = useState<"zimage" | "sd35" | "flux2">("zimage");
  const [stageBModel, setStageBModel] = useState("");   // "" = the worker default variant
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [stageBStrength, setStageBStrength] = useState(0.55);
  // M3.5 — cell realization: img2img-only, or mixed (inpaint cells repaint the background
  // around the held subject; needs the hero's bg mask from a birefnet matte job).
  const [realize, setRealize] = useState<"img2img" | "mixed">("img2img");
  // M4 — face anchor (R94) + identity-lock pass (R93). null = auto (on when an anchor
  // exists); the checkbox writes an explicit true/false override.
  const [anchorInfo, setAnchorInfo] = useState<AnchorInfo | null>(null);
  const [identityOn, setIdentityOn] = useState<boolean | null>(null);
  // P1-12 — curation throughput: persistent rejected[] (version.json), coverage-cell
  // filters, and a bulk-select set (cell keys) for keep/reject sweeps (~100→~30).
  const [rejected, setRejected] = useState<string[]>([]);
  // M5 — the asset's version records (the selector reads name/finalized per id).
  const [versionList, setVersionList] = useState<ProfileVersion[]>([]);
  // M7 — video-sketch harvest controls (target cell + motion + harvest density).
  const [skShot, setSkShot] = useState("waist_up");
  const [skAngle, setSkAngle] = useState("profile_left");
  const [skExpr, setSkExpr] = useState("neutral");
  const [skMotion, setSkMotion] = useState("");
  const [skEvery, setSkEvery] = useState(6);
  const [skMax, setSkMax] = useState(24);
  // M5 (F2) — the new-version modal: name + PARENT picker (copy from ANY prior version,
  // R59 — the prompt-only flow could only copy the active one).
  const [showNewVersion, setShowNewVersion] = useState(false);
  const [newVerName, setNewVerName] = useState("");
  const [newVerParent, setNewVerParent] = useState("");
  const [filterShot, setFilterShot] = useState("");
  const [filterAngle, setFilterAngle] = useState("");
  const [filterExpr, setFilterExpr] = useState("");
  const [showRejected, setShowRejected] = useState(false);
  const [bulkSel, setBulkSel] = useState<Set<string>>(new Set());
  const [characterClause, setCharacterClause] = useState("");
  const [promptTemplate, setPromptTemplate] = useState("");
  const [refSet, setRefSet] = useState<RefItem[]>([]);
  // M0c — per-base-image postprocess stacks for the active version (inline panel).
  const [postprocStacks, setPostprocStacks] = useState<PostprocStack[]>([]);
  const [busy, setBusy] = useState(false);
  // Parameter drawers (review 2026-06-10, issue 2): catalog-driven tunables, kept as a
  // sparse record (unset = use the worker/model default — nothing is sent).
  const [showParamsA, setShowParamsA] = useState(false);
  const [advParamsA, setAdvParamsA] = useState<Record<string, unknown>>({});
  const [showParamsB, setShowParamsB] = useState(false);
  const [advParamsB, setAdvParamsB] = useState<Record<string, unknown>>({});
  // Dry-run pre-flight review modal: the preview payload + the request to fire on [Run ▶].
  const [preview, setPreview] = useState<GeneratePreview | StageBPreview | null>(null);
  const pendingRunRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    log.info("Loreweave Studio UI started (log level:", log.level + ")");
    // Load the model catalog once (drives the Stage-B model-variant selector).
    getModels().then(setCatalog).catch(() => {});
  }, []);

  // Full-res viewer: ESC closes the lightbox.
  useEffect(() => {
    if (!viewer) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setViewer(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewer]);

  // Apply a /jobs payload to the UI — the single place queue state lands (review
  // 2026-06-10: the old second 1.2 s poll loop is gone; the steady 2 s probe + an
  // immediate refresh after every action keep the grid/dock live without double-polling).
  const applyJobs = (r: JobsResponse) => {
    setJobs(r.jobs);
    setCounts(r.counts);
    setPaused(r.paused);
    setPauseReason(r.pause_reason ?? null);
    if (r.disk) setDisk(r.disk);
    // After a relaunch the grid is empty but the queue may hold persisted pending
    // jobs — seed the grid from them so they're reviewable + cancelable before
    // unpause (R88 "Review/Unpause"; review #3).
    setBatchIds((prev) => {
      if (prev.length > 0) return prev;
      return Object.values(r.jobs)
        .filter((j) => j.status === "queued" || j.status === "running")
        .sort((a, b) => a.created_at.localeCompare(b.created_at))
        .map((j) => j.id);
    });
  };

  // One-shot refresh right after an action (generate/cancel/unpause/…) so the user sees
  // the transition immediately instead of waiting out the 2 s probe.
  const refreshJobs = async () => {
    try {
      applyJobs(await listJobs());
    } catch {
      /* transient — the steady probe catches up */
    }
  };

  // Health probe (every 2 s) — also THE steady /jobs poll (single poller).
  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const h = await getHealth();
        if (!alive) return;
        setHealth(h);
        setConn("online");
        try {
          const [p, d, l, j] = await Promise.all([
            getProject(), getDisk(), getComponents(), listJobs(),
          ]);
          if (alive) {
            setProject(p);
            setDisk(d);
            setLaunch(l);
            applyJobs(j);
          }
        } catch {
          /* project/disk/components/jobs fetch transient */
        }
      } catch {
        if (alive) setConn("offline");
      }
    };
    probe();
    const id = window.setInterval(probe, 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onUnpause = async () => {
    try {
      await unpauseQueue();
      setPaused(false);
      setPauseReason(null);
    } catch (e) {
      setError(String(e));
    }
    void refreshJobs();
  };

  const onCancel = async (id: string) => {
    try {
      await cancelJob(id);
    } catch (e) {
      setError(String(e));
    }
    void refreshJobs(); // observe the transition to canceled
  };

  // Delete a finished generation + all its files (orchestrator-owned, atomic — no
  // orphaned manifest/log). A persistent gallery of past generations is the P1 casting
  // grid; this is just the safe per-image cull (R44 cull, P0-lite).
  const onDelete = async (id: string) => {
    if (!window.confirm("Delete this generation and all its files? This cannot be undone.")) return;
    try {
      await deleteJob(id);
      log.info("deleted generation:", id);
      setBatchIds((prev) => prev.filter((b) => b !== id));
      if (selected === id) setSelected(null);
    } catch (e) {
      log.error("delete failed:", e);
      setError(String(e));
    }
    void refreshJobs();
  };

  // Split the parameter-drawer record into top-level request fields vs the catalog
  // `params` channel (top-level ones get API-side validation, e.g. width % 16).
  const TOP_LEVEL = new Set(["width", "height", "seed", "num_steps", "guidance_scale",
                             "negative_prompt", "model_name"]);
  const splitAdvParams = (vals: Record<string, unknown>) => {
    const top: Record<string, unknown> = {};
    const channel: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(vals)) {
      if (v === undefined || v === null || v === "") continue;
      (TOP_LEVEL.has(k) ? top : channel)[k] = v;
    }
    return { top, channel };
  };

  // Build the Stage-A / sandbox generate request (shared by Run and Preview).
  const buildGenerateReq = (): GenerateRequest | null => {
    setError(null);
    if (!project?.open) {
      setError("no project open — create or open one first");
      return null;
    }
    if (!prompt.trim()) {
      setError("enter a prompt");
      return null;
    }
    const { top, channel } = splitAdvParams(advParamsA);
    // Asset selected → the batch is Stage-A casting for it; sandbox → unscoped
    // experimentation (any pipeline, 2026-06-10 #3).
    const scope = activeAsset ? { asset_id: activeAsset.id, stage: "A" as const } : {};
    let req: GenerateRequest;
    if (castPipeline === "multi") {
      // A multi cast = one job → a pool of num_candidates × pipelines candidates.
      // Width/height/seed go top-level; the rest of the multi tunables (clean/polish
      // toggles + sub-params, img2img_batching) ride the catalog params channel.
      req = {
        pipeline: "multi",
        prompt: prompt.trim(),
        num_candidates: numCandidates,
        ideation_mode: ideationMode,
        ...scope,
        apply_style: applyStyle,
        ...(genStyleId ? { style_id: genStyleId } : {}),
        ...(top.width !== undefined ? { width: top.width as number } : {}),
        ...(top.height !== undefined ? { height: top.height as number } : {}),
        ...(top.seed !== undefined ? { seed: top.seed as number } : {}),
        ...(Object.keys(channel).length ? { params: channel } : {}),
      };
    } else {
      req = {
        pipeline: castPipeline,
        prompt: prompt.trim(),
        count,
        ...scope,
        apply_style: applyStyle,
        ...(genStyleId ? { style_id: genStyleId } : {}),
        ...top,
        ...(Object.keys(channel).length ? { params: channel } : {}),
      };
    }
    return req;
  };

  const fireGenerate = async (req: GenerateRequest) => {
    try {
      const res = await generate(req);
      log.info(
        activeAsset ? "cast" : "generate", res.batch_id,
        castPipeline === "multi"
          ? `(multi ×${numCandidates}, ${ideationMode})${activeAsset ? ` for ${activeAsset.name} [Stage A]` : " (sandbox)"}`
          : activeAsset
          ? `(${castPipeline} ×${count}) for ${activeAsset.name} [Stage A]`
          : `(${castPipeline} ×${count}, sandbox)`,
      );
      // Asset grid is derived from jobs (by requester); sandbox tracks its batch ids.
      if (!activeAsset) setBatchIds(res.job_ids);
      setSelected(null);
      void refreshJobs();
    } catch (e) {
      log.error("generate failed:", e);
      setError(String(e));
    }
  };

  const onGenerate = () => {
    const req = buildGenerateReq();
    if (req) void fireGenerate(req);
  };

  // Pre-flight review (issue 2): dry-run the exact request — resolved prompt (style
  // prepend visible), worker argv, job count — then [Run ▶] fires it unchanged.
  const onPreviewGenerate = async () => {
    const req = buildGenerateReq();
    if (!req) return;
    try {
      const p = await generatePreview(req);
      pendingRunRef.current = () => void fireGenerate(req);
      setPreview(p);
    } catch (e) {
      log.error("preview failed:", e);
      setError(String(e));
    }
  };

  // Minimal `loom init` flow (prompt-based; a native folder picker + format/cap wizard
  // is a later UI pass). Shows the footprint estimate so the size cap is an informed
  // choice (R164), then creates + opens the project (which resume-pauses its queue).
  const onNewProject = async () => {
    setError(null);
    const dest = window.prompt("New project folder (must be empty):");
    if (!dest) return;
    const name = window.prompt("Project name:", dest.split(/[\\/]/).pop() || "story");
    if (!name) return;
    try {
      const est = await estimateFootprint(1800, 1280, 720, 24);
      const cap = window.prompt(
        `Size cap (GB). A 30-min 720p master is ~${est.projected_master_gb} GB; ` +
          `suggested ${est.suggested_cap_gb} GB (min 50).`,
        String(est.suggested_cap_gb),
      );
      if (cap === null) return;
      const p = await createProject(dest, name, Math.max(50, parseInt(cap, 10) || 250));
      log.info("created project:", p.name, "at", p.path);
      setProject(p);
      setBatchIds([]);
      setSelected(null);
      void refreshJobs();
    } catch (e) {
      log.error("create project failed:", e);
      setError(String(e));
    }
  };

  const openByPath = async (path: string) => {
    setError(null);
    setShowPicker(false);
    try {
      const p = await openProject(path);
      log.info("opened project:", p.name, "at", p.path);
      setProject(p);
      setBatchIds([]);
      setSelected(null);
      void refreshJobs();
    } catch (e) {
      log.error("open project failed:", e);
      setError(String(e));
    }
  };

  // Project picker: show the registry of known projects (from .loom_state, machine-local)
  // so you choose from a list instead of typing a path. "Browse…" keeps the manual entry.
  const onTogglePicker = async () => {
    if (showPicker) {
      setShowPicker(false);
      return;
    }
    try {
      const r = await listProjects();
      setProjectList(r.projects);
    } catch (e) {
      setError(String(e));
    }
    setShowPicker(true);
  };

  const onBrowseProject = () => {
    setShowPicker(false);
    const path = window.prompt("Open project folder (contains project.json):");
    if (path) void openByPath(path);
  };

  // Close the active project (2026-06-10 #2): the app runs project-less (generation and
  // the library disable) until a project is created/opened; a relaunch won't auto-reopen.
  // Non-destructive — the queue/outputs stay on disk; reopening resumes (paused, R88).
  const onCloseProject = async () => {
    setError(null);
    try {
      await closeProject();
      log.info("project closed");
      setProject({ open: false });
      setBatchIds([]);
      setSelected(null);
      setJobs({});
      setCounts({ queued: 0, running: 0, done: 0, failed: 0, canceled: 0 });
      setPaused(false);
      setPauseReason(null);
    } catch (e) {
      log.error("close project failed:", e);   // e.g. 409 while a job is running
      setError(String(e));
    }
  };

  // Gracefully stop a running batch job: finish the current image, keep the completed
  // ones (vs ✕ cancel = kill + discard the partial dir).
  const onStop = async (id: string) => {
    try {
      await stopJob(id);
    } catch (e) {
      setError(String(e));
    }
    void refreshJobs();
  };

  const onForgetProject = async (path: string) => {
    try {
      await forgetProject(path);
      setProjectList((prev) => prev.filter((p) => p.path !== path));
    } catch (e) {
      setError(String(e));
    }
  };

  // --- L2 Asset Studio (P1/M1) ---
  const refreshAssets = async () => {
    try {
      const [a, s, st] = await Promise.all([listAssets(), getStyle(), getStyles()]);
      setAssets(a.assets);
      setStyleState(s);
      setStyles(st);
      setGenStyleId(st.active_style_id);   // per-gen selection resets to the active default
      const sel = st.styles.find((x) => x.id === st.active_style_id);
      setStyleDraft(sel?.fragment ?? s.fragment);
      setApplyStyle(s.enabled_default);
    } catch {
      /* no project / transient */
    }
  };

  // Reload just the style collection (after add/delete/set-active/edit in L1 or the bar).
  const refreshStyles = async () => {
    try {
      const st = await getStyles();
      setStyles(st);
      setGenStyleId((cur) => (cur && st.styles.some((x) => x.id === cur)) ? cur : st.active_style_id);
    } catch { /* transient */ }
  };

  // Pick which L1 style applies to the next generation (and is the one the bar edits).
  const onSelectGenStyle = (id: string) => {
    setGenStyleId(id);
    const sel = styles?.styles.find((x) => x.id === id);
    if (sel) setStyleDraft(sel.fragment);
  };

  // Load the library + style whenever the open project changes.
  useEffect(() => {
    if (!project?.open) {
      setAssets([]);
      setActiveAsset(null);
      setCasting([]);
      setStyleState(null);
      setPostprocStacks([]);
      return;
    }
    void refreshAssets();
    void refreshPostproc();   // M0c — project-level stacks (independent of the active asset)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.open, project?.id]);

  const onCreateAsset = async () => {
    setError(null);
    const name = window.prompt("New character name:");
    if (!name) return;
    try {
      const r = await createAsset(name);
      log.info("created asset:", name);
      const a = await listAssets();
      setAssets(a.assets);
      // Switch to the new asset through the SAME full per-asset reset the rail uses
      // (stage→A, selection/bulk/filters/Stage-B controls cleared, refreshCasting
      // repopulates casting/refSet/rejected/anchor/versions for the new id). A partial
      // reset here left the PREVIOUS asset's curated refSet in state, so its Stage-C ref
      // tiles rendered as broken placeholders under the new asset's id until the next real
      // switch (user-reported 2026-06-14; same leak class as the asset switch, line ~651).
      onSelectAsset(a.assets.find((x) => x.id === r.profile.id) ?? null);
    } catch (e) {
      log.error("create asset failed:", e);
      setError(String(e));
    }
  };

  // M9 — export the active asset (download the bundle) / import a bundle as a new profile.
  const importFileRef = useRef<HTMLInputElement | null>(null);
  const onExport = async () => {
    if (!activeAsset) return;
    setError(null);
    try {
      const blob = await exportProfile(activeAsset.id);   // token-gated fetch (M9 review)
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${activeAsset.name || activeAsset.id}_bundle.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      log.error("export failed:", e);
      setError(String(e));
    }
  };
  const onImportFile = async (file: File) => {
    setError(null);
    try {
      const res = await importProfile(await file.arrayBuffer());
      const a = await listAssets();
      setAssets(a.assets);
      // Full per-asset reset (same leak class as create, above): refreshCasting rescopes
      // refSet/rejected/anchor/versions to the imported id so no stale tiles linger.
      onSelectAsset(a.assets.find((x) => x.id === res.profile.id) ?? null);
      log.info("imported:", res.profile.name,
               res.renamed_from ? `(renamed from ${res.renamed_from})` : "");
    } catch (e) {
      log.error("import failed:", e);
      setError(String(e));
    }
  };

  // Load the active version's casting set (candidates + which one is the hero ★) so the
  // grid can star/highlight by job_id. Persisted in version.json (P1/M2).
  const refreshCasting = async (asset: AssetSummary | null) => {
    if (!asset) {
      setCasting([]);
      setRefSet([]);
      setPromptTemplate("");
      setAnchorInfo(null);
      setRejected([]);
      setVersionList([]);
      return;
    }
    try {
      const detail = await getAsset(asset.id);
      const v = detail.versions.find((x) => x.id === asset.active_version);
      setCasting(v?.casting ?? []);
      setRefSet(v?.ref_set ?? []);
      setPromptTemplate(v?.prompt_template ?? "");
      setAnchorInfo(v?.anchor ?? null);
      setRejected(v?.rejected ?? []);
      setVersionList(detail.versions ?? []);
    } catch {
      setCasting([]);
      setRefSet([]);
      setAnchorInfo(null);
      setRejected([]);
      setVersionList([]);
    }
  };

  // M0c — postprocess stacks are PROJECT-level (any image): loaded independently of the
  // active asset (work in the Sandbox too), refreshed on project open + after step changes.
  const refreshPostproc = async () => {
    if (!project?.open) { setPostprocStacks([]); return; }
    try { setPostprocStacks(await getPostprocStacks()); }
    catch (e) { log.error("postproc stacks load failed:", e); }
  };

  // M0c — postprocess stack handlers (the inline Inspector panel on any selected image,
  // project-level — no asset required, works in the Sandbox too).
  const onAddPostprocStep = async (base: string, preset: PostprocStep["preset"],
                                   backend: string | undefined,
                                   params: Record<string, unknown>) => {
    if (!project?.open) return;
    setBusy(true); setError(null);
    try {
      setPostprocStacks(await addPostprocStep({ base, preset, backend, params }));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const onQueuePostprocStep = async (stepId: string) => {
    setBusy(true); setError(null);
    try {
      setPostprocStacks(await queuePostprocStep(stepId));
      void refreshJobs();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const onRemovePostprocStep = async (stepId: string) => {
    setBusy(true); setError(null);
    try {
      setPostprocStacks(await removePostprocStep(stepId));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  // The step output is recorded by the runner's completion observer (server-side). When a
  // queued step's job finishes, re-fetch the project stacks so the persisted output lands
  // (which also re-opens the "add next step" gate). Self-terminating: once persisted, status
  // flips off "queued" so this stops firing.
  useEffect(() => {
    const lagging = postprocStacks.some((s) => s.steps.some((st) =>
      st.job_id && st.status === "queued"
      && ["done", "failed"].includes(jobs[st.job_id]?.status ?? "")));
    if (lagging) void refreshPostproc();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs]);

  // M5 — version ops. Switching/creating resets the per-version Stage-B controls (the
  // same leak class the asset switch had) and re-scopes everything via refreshCasting.
  const _switchToVersion = async (activeVersion: string) => {
    if (!activeAsset) return;
    const updated = { ...activeAsset, active_version: activeVersion };
    setActiveAsset(updated);
    setSelected(null);
    setBulkSel(new Set());
    setRealize("img2img");
    setIdentityOn(null);
    await refreshCasting(updated);
    void refreshAssets();
  };

  const onCreateVersion = () => {
    if (!activeAsset) return;
    setNewVerName("");
    setNewVerParent(activeAsset.active_version);   // default parent = active (R59: any)
    setShowNewVersion(true);
  };

  const onCreateVersionConfirm = async () => {
    if (!activeAsset) return;
    setError(null);
    try {
      const res = await createVersion(activeAsset.id, newVerName.trim() || undefined,
                                      newVerParent || undefined);
      log.info("version:", res.version.id, `(${res.version.name}) copied — now active`);
      setShowNewVersion(false);
      await _switchToVersion(res.profile.active_version);
    } catch (e) {
      log.error("create version failed:", e);
      setError(String(e));
    }
  };

  const onActivateVersion = async (vid: string) => {
    if (!activeAsset || vid === activeAsset.active_version) return;
    setError(null);
    try {
      const profile = await activateVersion(activeAsset.id, vid);
      await _switchToVersion(profile.active_version);
    } catch (e) {
      setError(String(e));
    }
  };

  const onFinalizeVersion = async () => {
    if (!activeAsset) return;
    if (!window.confirm("Finalize = LOCK this version (R60): immutable afterwards — "
                        + "any change needs a new version. Continue?")) return;
    setError(null);
    try {
      await finalizeVersion(activeAsset.id, activeAsset.active_version);
      await refreshCasting(activeAsset);
    } catch (e) {
      setError(String(e));
    }
  };

  const onSelectAsset = (asset: AssetSummary | null) => {
    setActiveAsset(asset);
    setSelected(null);
    setStage("A");
    setBulkSel(new Set());
    setFilterShot(""); setFilterAngle(""); setFilterExpr("");
    // Stage-B controls are PER-ASSET decisions — carrying them across assets leaks state
    // (review 2026-06-11: realize="mixed" without this asset's matte → 422; an explicit
    // identity override would shadow the server's verified-anchor auto behavior).
    setRealize("img2img");
    setIdentityOn(null);
    void refreshCasting(asset);
    void refreshJobs(); // refresh jobs so the derived asset grid is current
  };

  // Star/un-star a specific candidate output as the hero ★ (persists into version.json).
  const onStar = async (jobId: string, output?: string) => {
    if (!activeAsset) return;
    const current = casting.find((c) => c.source_output === output);
    const makeHero = !(current?.starred ?? false); // toggle
    try {
      const version = await starCandidate(activeAsset.id, jobId, makeHero, output);
      setCasting(version.casting);
      log.info(makeHero ? "starred hero:" : "un-starred:", output ?? jobId, "for", activeAsset.name);
    } catch (e) {
      log.error("star failed:", e);
      setError(String(e));
    }
  };

  // Stage-B expansion: build the coverage-matrix dataset (one img2img job per cell from the hero).
  const hasHero = casting.some((c) => c.starred);

  // M4 review (Medium): the anchor is VERIFIED once a done+ok identity job for this
  // version ran after it was (re-)picked — the worker hard-fails on a faceless anchor,
  // so a successful run is the proof. Durable stamp first (verified_at survives queue
  // pruning); live job scan as instant feedback between version refreshes.
  const anchorVerified = useMemo(() => {
    if (!activeAsset || !anchorInfo) return false;
    if (anchorInfo.verified_at) return true;
    return Object.values(jobs).some((j) =>
      j.pipeline === "identity" && j.status === "done"
      && j.requester_id === activeAsset.active_version
      && j.result?.ok === true
      && (j.created_at ?? "") >= anchorInfo.set_at);
  }, [jobs, activeAsset, anchorInfo]);

  // M3.5: the newest done matte job's bg mask for this version — enables realize="mixed".
  // Selected by the artifact's output_meta.role (the adapter's contract), NOT a filename
  // suffix (review 2026-06-11 Low: naming changes must not break mask discovery).
  const bgMask = useMemo(() => {
    if (!activeAsset) return null;
    const done = Object.values(jobs)
      .filter((j) => j.pipeline === "birefnet" && j.status === "done"
        && j.requester_id === activeAsset.active_version)
      .sort((a, b) => ((a.created_at ?? "") < (b.created_at ?? "") ? -1 : 1));
    for (let i = done.length - 1; i >= 0; i--) {
      const r = done[i].result;
      const m = (r?.output_names ?? []).find((n) => r?.output_meta?.[n]?.role === "bgmask");
      if (m) return m;
    }
    return null;
  }, [jobs, activeAsset]);

  // If the mask disappears (matte job deleted, asset switched), "mixed" can't run —
  // downgrade instead of letting the next Generate 422 (review 2026-06-11).
  useEffect(() => {
    if (realize === "mixed" && !bgMask) setRealize("img2img");
  }, [realize, bgMask]);

  // M4: pick the selected output as the version's face anchor / clear it (R94).
  const onSetAnchor = async (jobId: string, output?: string) => {
    if (!activeAsset) return;
    setError(null);
    try {
      const version = await setAnchor(activeAsset.id, jobId, output);
      setAnchorInfo(version.anchor ?? null);
      log.info("anchor:", "set from", output ?? jobId, "→ identity pass available");
    } catch (e) {
      log.error("set anchor failed:", e);
      setError(String(e));
    }
  };

  const onClearAnchor = async () => {
    if (!activeAsset) return;
    try {
      const version = await clearAnchor(activeAsset.id);
      setAnchorInfo(version.anchor ?? null);
      setIdentityOn(null);
    } catch (e) {
      setError(String(e));
    }
  };

  // M6.1: derive a restored 512² face portrait from the selected output — the resulting
  // tile is a normal job output, so "⚓ set as face anchor" picks it up.
  const onDerivePortrait = async (jobId: string, output?: string) => {
    if (!activeAsset) return;
    setError(null);
    try {
      const res = await deriveFacePortrait(activeAsset.id, jobId, output);
      log.info("portrait:", res.job_id, "← face crop of", res.source_output,
               "— anchor the result when it lands");
      void refreshJobs();
    } catch (e) {
      log.error("derive portrait failed:", e);
      setError(String(e));
    }
  };

  // M7: fire a cell-targeted video sketch from the hero — the chained harvest's frames
  // stream into the grid carrying the target cell (curate them like recipe cells).
  const onSketch = async () => {
    if (!activeAsset || !hasHero) return;
    setError(null);
    setBusy(true);
    try {
      const res = await sketchHero(activeAsset.id, {
        shot_size: skShot, angle: skAngle, expression: skExpr,
        motion_prompt: skMotion.trim() || null,
        every: skEvery, max_frames: skMax,
        ...(characterClause.trim() ? { character_clause: characterClause.trim() } : {}),
      });
      log.info("sketch:", res.job_id,
               `→ ${skShot}/${skAngle}/${skExpr} — harvest streams the frames`);
      void refreshJobs();
    } catch (e) {
      log.error("sketch failed:", e);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Matte the hero (BiRefNet): one queued job → matte + cutout + the bg-inpaint mask.
  const onMatteHero = async () => {
    if (!activeAsset || !hasHero) return;
    setError(null);
    setBusy(true);
    try {
      const res = await matteHero(activeAsset.id);
      log.info("matte:", res.job_id, "→ subject matte / cutout / bg mask from the hero ★");
      void refreshJobs();
    } catch (e) {
      log.error("matte failed:", e);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const buildStageBBody = (): StageBRequest => {
    // Drawer mapping: width/height → top-level (API-validated), the catalog "seed"
    // control = the recipe's base_seed (per-cell seeds derive from it), rest → params.
    const { top, channel } = splitAdvParams(advParamsB);
    return {
      preset: recipePreset,
      pipeline: stageBPipeline,
      model_name: stageBModel || undefined,
      strength: stageBStrength,
      realize,
      ...(realize === "mixed" ? { bg_mask: bgMask ?? undefined } : {}),
      ...(identityOn !== null ? { identity: identityOn } : {}),   // omit = auto (R93)
      character_clause: characterClause.trim() || undefined,
      apply_style: applyStyle,
      ...(genStyleId ? { style_id: genStyleId } : {}),
      ...(top.width !== undefined ? { width: top.width as number } : {}),
      ...(top.height !== undefined ? { height: top.height as number } : {}),
      ...(top.seed !== undefined ? { base_seed: top.seed as number } : {}),
      ...(top.num_steps !== undefined || top.guidance_scale !== undefined
          || top.negative_prompt !== undefined || Object.keys(channel).length
        ? { params: {
              ...(top.num_steps !== undefined ? { num_steps: top.num_steps } : {}),
              ...(top.guidance_scale !== undefined ? { guidance_scale: top.guidance_scale } : {}),
              ...(top.negative_prompt !== undefined ? { negative_prompt: top.negative_prompt } : {}),
              ...channel,
            } }
        : {}),
    };
  };

  const fireStageB = async (body: StageBRequest) => {
    if (!activeAsset) return;
    setError(null);
    setBusy(true);
    try {
      const res = await stageB(activeAsset.id, body);
      log.info("stage-b:", res.batch_id,
               `${recipePreset} → ${res.count} batch job(s) (${res.items ?? "?"} cells)`,
               `(keep ~${res.kept_target.join("–")})`);
      setStage("C"); // jump to curation as candidates stream in
      void refreshJobs();
    } catch (e) {
      log.error("stage-b failed:", e);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onStageB = () => {
    if (!activeAsset || !hasHero) return;
    void fireStageB(buildStageBBody());
  };

  // Stage-B pre-flight: dry-run the recipe (planned job count, hero, first cell + argv)
  // before committing the GPU to ~17–78 img2img jobs.
  const onPreviewStageB = async () => {
    if (!activeAsset || !hasHero) return;
    setError(null);
    try {
      const body = buildStageBBody();
      const p = await stageBPreview(activeAsset.id, body);
      pendingRunRef.current = () => void fireStageB(body);
      setPreview(p);
    } catch (e) {
      log.error("stage-b preview failed:", e);
      setError(String(e));
    }
  };

  // P1-12: mark/unmark a candidate rejected (persistent cull-from-view, no image copy).
  const onReject = async (jobId: string, output: string | undefined, flag: boolean) => {
    if (!activeAsset) return;
    try {
      const version = await rejectOutput(activeAsset.id, jobId, output, flag);
      setRejected(version.rejected ?? []);
      setRefSet(version.ref_set);
    } catch (e) {
      log.error("reject failed:", e);
      setError(String(e));
    }
  };

  // Stage-C curation: keep ✓ a candidate into the curated ref_set / cull ✕ a kept one.
  const onKeep = async (jobId: string, output?: string) => {
    if (!activeAsset) return;
    try {
      const version = await keepRef(activeAsset.id, jobId, output);
      setRefSet(version.ref_set);
      setRejected(version.rejected ?? []);   // keep wins over a stale reject mark
    } catch (e) {
      log.error("keep failed:", e);
      setError(String(e));
    }
  };
  const onCull = async (refId: string) => {
    if (!activeAsset) return;
    try {
      const version = await cullRef(activeAsset.id, refId);
      setRefSet(version.ref_set);
    } catch (e) {
      log.error("cull failed:", e);
      setError(String(e));
    }
  };
  const onSaveProfile = async () => {
    if (!activeAsset) return;
    setError(null);
    try {
      const version = await saveProfile(activeAsset.id, promptTemplate.trim());
      setPromptTemplate(version.prompt_template);
      log.info("saved AssetProfile:", activeAsset.name,
               `(${version.ref_set.length} refs, Saved not Finalized)`);
    } catch (e) {
      log.error("save failed:", e);
      setError(String(e));
    }
  };

  const onSaveStyle = async () => {
    try {
      // Save the fragment into the SELECTED style (genStyleId) + the on/off gate (review:
      // enabled_default must be settable, not just stored). 2026-06-13: styles are a
      // collection, so the bar edits whichever style is picked.
      const s = await setStyle(styleDraft, applyStyle, undefined, genStyleId || undefined);
      setStyleState(s);
      await refreshStyles();
      log.info("style saved", genStyleId ? `(${genStyleId})` : "", "apply:", applyStyle);
    } catch (e) {
      setError(String(e));
    }
  };

  // Explicit, on-demand model fetch (R163) when the launch gate reports a missing
  // P0-essential weight — the no-surprise alternative to auto-downloading at startup.
  const onFetchWeights = async () => {
    setError(null);
    setFetching(true);
    try {
      const res = await fetchComponents();
      setLaunch(res.report);
    } catch (e) {
      setError(String(e));
    } finally {
      setFetching(false);
    }
  };

  const dot = conn === "online" ? "ok" : conn === "offline" ? "err" : "warn";
  // A selected cell key is either "<jobId>" or "<jobId>:<output>" (multi candidate).
  const selJob = selected ? jobs[selected.split(":")[0]] : null;
  // The selected candidate's own output (multi pool) — the Inspector shows THAT image +
  // its provenance, not the pool's first output (review 2026-06-10 #2).
  const selOutput = selected && selected.includes(":")
    ? selected.slice(selected.indexOf(":") + 1)
    : undefined;
  // M0c — the selected image's out/-relative name (the postprocess-stack base).
  const selBase = selOutput ?? selJob?.result?.output_name;
  // When an asset is active, the grid is derived from its jobs (lineage requester =
  // active version); the sandbox tracks the last batch's ids.
  // The grid is stage-scoped: Stage A shows casting jobs (stage A); Stage B/C show the
  // Stage-B dataset candidates (stage B). Legacy jobs with no stage read as A.
  const gridStage = stage === "A" ? "A" : "B";
  // Sandbox grid: the last batch's jobs + any clean/polish pass jobs chained off them
  // (a pass follows its parent into the grid; chains can nest — polish after clean).
  const sandboxIds = (() => {
    const ids = new Set(batchIds);
    let grew = true;
    while (grew) {
      grew = false;
      for (const j of Object.values(jobs)) {
        if (j.chained_from && ids.has(j.chained_from) && !ids.has(j.id)) {
          ids.add(j.id);
          grew = true;
        }
      }
    }
    const known = Object.values(jobs)
      .filter((j) => ids.has(j.id))
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map((j) => j.id);
    const unknown = batchIds.filter((id) => !jobs[id]);   // just-fired, not polled yet
    return [...unknown, ...known];
  })();
  const gridIds = activeAsset
    ? Object.values(jobs)
        .filter((j) => j.requester_id === activeAsset.active_version
                       && (j.stage ?? "A") === gridStage)
        .sort((a, b) => a.created_at.localeCompare(b.created_at))
        .map((j) => j.id)
    : sandboxIds;
  // Which saved candidate outputs are starred (the hero ★, persisted in version.json).
  const starredOutputs = new Set(casting.filter((c) => c.starred).map((c) => c.source_output));
  // Partial/stopped Stage-B datasets (review 2026-06-10): failed/skipped cells mean the
  // coverage matrix is incomplete — warn in Stages B/C instead of a silent green done.
  const partialDatasets = activeAsset
    ? Object.values(jobs).filter(
        (j) => j.requester_id === activeAsset.active_version
          && (j.stage ?? "") === "B" && j.status === "done"
          && !!j.result?.batch
          && (j.result.batch.failed > 0 || j.result.batch.skipped > 0))
    : [];
  // Stage-C: which candidate outputs are kept into the curated ref_set (+ output → ref id).
  const keptByOutput = new Map(refSet.map((r) => [r.source_output, r.id] as const));
  // Flatten jobs → candidate cells: a multi cast (one job → N outputs) expands into one
  // tile per candidate; everything else is one tile per job. A still-RUNNING cast
  // streams its interim candidates (partial_outputs) as tiles the moment each lands
  // (user request 2026-06-10), plus one placeholder tile for the in-flight remainder.
  type Cell = { key: string; job?: Job; output?: string; interim?: boolean; refItem?: RefItem };
  const cells: Cell[] = gridIds.flatMap((id) => {
    const job = jobs[id];
    const names = job?.result?.output_names;
    if (names && names.length > 1) {
      return names.map((o) => ({ key: `${id}:${o}`, job, output: o }));
    }
    const partial = job?.status === "running" ? job.partial_outputs ?? [] : [];
    if (partial.length > 0) {
      return [
        ...partial.map((o) => ({ key: `${id}:${o}`, job, output: o, interim: true })),
        { key: id, job },     // the in-flight remainder (progress bar + cancel live here)
      ];
    }
    return [{ key: id, job, output: job?.result?.output_name }];
  });

  // P1-12 — Stage-C curation throughput: coverage-cell filters + hide-rejected. The
  // cell's coverage comes from per-output meta (batch jobs) or the job field (legacy).
  const rejectedSet = new Set(rejected);
  const covOf = (c: Cell) =>
    c.refItem?.coverage_cell
    ?? (c.output ? c.job?.result?.output_meta?.[c.output]?.coverage_cell : undefined)
    ?? c.job?.coverage_cell ?? undefined;
  // M5 review (F1): a copied version's ref_set is DURABLE (refs/ files) but has no
  // generation jobs for the new version id — synthesize tiles for kept refs whose
  // source output isn't already on the grid, served from refs/ via refUrl. They render
  // kept (✓ → cull works through the ref id) so "copy parent, then edit" is real.
  const jobOutputs = new Set(cells.map((c) => c.output).filter(Boolean));
  const durableRefCells: Cell[] = (stage === "C" && activeAsset)
    ? refSet.filter((r) => !r.source_output || !jobOutputs.has(r.source_output))
        .map((r) => ({ key: `ref:${r.id}`, refItem: r }))
    : [];
  const stageCells = stage !== "C" ? cells
    : [...durableRefCells, ...cells].filter((c) => {
        if (!showRejected && c.output && rejectedSet.has(c.output)) return false;
        const cov = covOf(c);
        if (filterShot && cov?.shot_size !== filterShot) return false;
        if (filterAngle && cov?.angle !== filterAngle) return false;
        if (filterExpr && cov?.expression !== filterExpr) return false;
        return true;
      });

  // Bulk keep/reject over the selected cell keys (per-item isolation: one 409 — e.g. a
  // pre-lock tile — doesn't abort the sweep; authoritative state refreshed once at the end).
  const onBulk = async (action: "keep" | "reject") => {
    if (!activeAsset || bulkSel.size === 0) return;
    setBusy(true);
    setError(null);
    const errs: string[] = [];
    for (const key of bulkSel) {
      const i = key.indexOf(":");
      const jobId = i === -1 ? key : key.slice(0, i);
      const output = i === -1 ? jobs[key]?.result?.output_name ?? undefined : key.slice(i + 1);
      try {
        if (action === "keep") await keepRef(activeAsset.id, jobId, output);
        else await rejectOutput(activeAsset.id, jobId, output, true);
      } catch (e) {
        errs.push(String(e));
      }
    }
    await refreshCasting(activeAsset);
    setBulkSel(new Set());
    setBusy(false);
    if (errs.length) setError(`${errs.length} item(s) failed — first: ${errs[0]}`);
  };

  // M5 (F3): a finalized version is server-locked — the UI must FEEL read-only too
  // (mutating controls hidden/disabled, not error-prone).
  const activeVersionLocked =
    versionList.find((v) => v.id === activeAsset?.active_version)?.finalized ?? false;

  // Keyboard curation (P1-12): arrows move the selection, k = keep, x = toggle reject,
  // space = toggle bulk-select. Stage C only; the grid div is focusable.
  const onGridKey = (e: React.KeyboardEvent) => {
    if (stage !== "C" || stageCells.length === 0) return;
    const idx = Math.max(0, stageCells.findIndex((c) => c.key === selected));
    let next = -1;
    if (e.key === "ArrowRight") next = Math.min(idx + 1, stageCells.length - 1);
    else if (e.key === "ArrowLeft") next = Math.max(idx - 1, 0);
    else if (e.key === "ArrowDown") next = Math.min(idx + 5, stageCells.length - 1);
    else if (e.key === "ArrowUp") next = Math.max(idx - 5, 0);
    if (next !== -1) {
      e.preventDefault();
      setSelected(stageCells[next].key);
      return;
    }
    if (activeVersionLocked) return;     // finalized = read-only (arrows still navigate)
    const cur = stageCells[idx];
    if (!cur?.job || cur.job.status !== "done") return;
    if (e.key === "k") {
      e.preventDefault();
      void onKeep(cur.job.id, cur.output);
    } else if (e.key === "x") {
      e.preventDefault();
      void onReject(cur.job.id, cur.output, !(cur.output && rejectedSet.has(cur.output)));
    } else if (e.key === " ") {
      e.preventDefault();
      setBulkSel((s) => {
        const n = new Set(s);
        if (n.has(cur.key)) n.delete(cur.key); else n.add(cur.key);
        return n;
      });
    }
  };

  return (
    <div className="app">
      <header className="titlebar">
        <span className="title">Loreweave Studio</span>
        {/* M0a — project actions live in a conventional File menu; the top bar keeps the
            current project + orchestrator status only (no more inline New/Open/Close strip). */}
        <div className="filemenu-wrap">
          <button className="proj-btn" onClick={onTogglePicker} disabled={conn !== "online"}
                  title="project actions">
            File ▾
          </button>
          {showPicker && (
            <div className="filemenu" role="menu">
              <button className="filemenu-item" disabled={conn !== "online"}
                      onClick={() => { setShowPicker(false); void onNewProject(); }}>
                New project…
              </button>
              <button className="filemenu-item" disabled={conn !== "online"}
                      onClick={onBrowseProject}>
                Open folder…
              </button>
              {project?.open && (
                <button className="filemenu-item" disabled={conn !== "online"}
                        onClick={() => { setShowPicker(false); void onCloseProject(); }}
                        title="close this project (nothing is deleted — its queue resumes paused on reopen)">
                  Close project
                </button>
              )}
              <div className="filemenu-sep" />
              <div className="filemenu-label">RECENT</div>
              {projectList.length === 0 && (
                <div className="picker-empty">no recent projects</div>
              )}
              {projectList.map((p) => (
                <div key={p.path} className={`picker-row ${p.exists ? "" : "missing"}`}>
                  <button
                    className="picker-open"
                    disabled={!p.exists}
                    title={p.path}
                    onClick={() => { setShowPicker(false); void openByPath(p.path); }}
                  >
                    <span className="picker-name">
                      {p.name || "(unknown)"} {p.active && <span className="picker-active">● open</span>}
                    </span>
                    <span className="picker-path">
                      {p.path}
                      {!p.exists && " — missing"}
                      {p.size_cap_gb ? ` · ${p.size_cap_gb}G cap` : ""}
                    </span>
                  </button>
                  <button className="picker-forget" title="forget (remove from list)"
                          onClick={() => onForgetProject(p.path)}>✕</button>
                </div>
              ))}
            </div>
          )}
        </div>
        <span className="project">
          {project?.open ? project.name : <span className="muted">no project</span>}
        </span>
        <span className="spacer" />
        <span className={`status dot-${dot}`}>
          <i className="dot" /> orchestrator: {conn}
          {health ? ` · v${health.app_version}` : ""}
        </span>
      </header>

      <div className="panes">
        <nav className="rail">
          <div className="view-tabs">
            <button className={`view-tab ${view === "assets" ? "on" : ""}`}
                    onClick={() => setView("assets")}>L2 · Assets</button>
            <button className={`view-tab ${view === "world" ? "on" : ""}`}
                    onClick={() => setView("world")}
                    title="L1 World — style, world prose, story spine (M8)">L1 · World</button>
          </div>
          {/* M0a — each workspace owns its rail: L2 → the asset library; L1 → the World nav
              (Visual Styles / World / Story Spine sub-tabs land in M0b). The asset list no
              longer bleeds into the L1 workspace. */}
          {view === "assets" && (
            <>
              <div className="rail-head">
                ASSETS
                <button className="rail-add" onClick={onCreateAsset}
                        disabled={conn !== "online" || !project?.open} title="new character">
                  + Character
                </button>
                <button className="rail-add" onClick={() => importFileRef.current?.click()}
                        disabled={conn !== "online" || !project?.open}
                        title="import a profile bundle (.zip) — always a NEW profile, rename on collision (R67)">
                  ⤒ Import
                </button>
                <input ref={importFileRef} type="file" accept=".zip,application/zip"
                       style={{ display: "none" }}
                       onChange={(e) => {
                         const f = e.target.files?.[0];
                         if (f) void onImportFile(f);
                         e.target.value = "";   // allow re-importing the same file
                       }} />
              </div>
              {!project?.open && <div className="muted">open a project first</div>}
              {project?.open && assets.length === 0 && (
                <div className="muted">no assets yet — add a character</div>
              )}
              <button
                className={`asset-row sandbox ${activeAsset === null ? "sel" : ""}`}
                onClick={() => onSelectAsset(null)}
              >
                ▦ Sandbox <span className="muted">(unscoped)</span>
              </button>
              {assets.map((a) => (
                <button
                  key={a.id}
                  className={`asset-row ${activeAsset?.id === a.id ? "sel" : ""}`}
                  onClick={() => onSelectAsset(a)}
                  title={`${a.asset_class} · ${a.version_count} version(s)`}
                >
                  <span className="asset-dot" /> {a.name}
                </button>
              ))}
            </>
          )}
          {view === "world" && (
            <>
              <div className="rail-head">WORLD <span className="muted">L1 bible</span></div>
              <button className={`asset-row ${l1Tab === "styles" ? "sel" : ""}`}
                      onClick={() => setL1Tab("styles")}
                      title="named style snippets — pick one per generation (R104)">
                🎨 Visual styles
              </button>
              <button className={`asset-row ${l1Tab === "world" ? "sel" : ""}`}
                      onClick={() => setL1Tab("world")}
                      title="long-form world summary (authoring context)">
                🌍 World
              </button>
              <button className={`asset-row ${l1Tab === "spine" ? "sel" : ""}`}
                      onClick={() => setL1Tab("spine")}
                      title="premise + characters → stub profiles (R55)">
                🧬 Story spine
              </button>
            </>
          )}
        </nav>

        <main className="stage">
          {view === "world" ? (
            <WorldWorkspace
              project={project}
              tab={l1Tab}
              onError={setError}
              onStubCreated={() => void refreshAssets()}
              onStylesChanged={() => { void refreshStyles(); void refreshAssets(); }}
            />
          ) : (
          <>
          {activeAsset && (
            <div className="stage-ctx">
              <span className="ctx-asset">{activeAsset.name}</span>
              <select
                className="ver-select"
                value={activeAsset.active_version}
                onChange={(e) => void onActivateVersion(e.target.value)}
                title="version selector (M5) — everything below is scoped to the active version"
              >
                {(versionList.length ? versionList
                  : [{ id: activeAsset.active_version, name: "v1_base", finalized: false } as ProfileVersion]
                ).map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}{v.finalized ? " 🔒" : ""}
                  </option>
                ))}
              </select>
              <button
                className="ghost"
                onClick={() => void onCreateVersion()}
                title="copy-on-create (R50/R58): a FULL deep-duplicate of the active version (refs, casting, face anchor) — fresh + unlocked, becomes active"
              >
                + version
              </button>
              {versionList.find((v) => v.id === activeAsset.active_version)?.finalized ? (
                <span
                  className="muted"
                  title="finalized = locked (R60): immutable — create a new version to change anything"
                >
                  🔒 finalized
                </span>
              ) : (
                <button
                  className="ghost"
                  onClick={() => void onFinalizeVersion()}
                  title="finalize = pure-intent lock (R60): the version becomes immutable"
                >
                  finalize 🔒
                </button>
              )}
              <button className="ghost" onClick={() => void onExport()}
                 title="export this profile + ALL its versions as a portable bundle (.zip, R66)">
                ⤓ Export
              </button>
              <span className="muted"> · CHARACTER BOOTSTRAP — </span>
              <span className="stage-switch">
                {([["A", "Casting"], ["B", "Expansion"], ["C", "Curation"]] as const).map(
                  ([s, label]) => (
                    <button
                      key={s}
                      className={`stage-tab ${stage === s ? "on" : ""}`}
                      onClick={() => setStage(s)}
                      title={`Stage ${s} · ${label}`}
                    >
                      {s} · {label}
                    </button>
                  ),
                )}
              </span>
            </div>
          )}
          {(!activeAsset || stage === "A") && (
          <div className="generate-bar">
            <label>
              pipeline
              <select
                value={castPipeline}
                onChange={(e) => {
                  setCastPipeline(e.target.value as "multi" | "zimage" | "sd35" | "flux2");
                  setAdvParamsA({});   // tunables are per-pipeline
                }}
              >
                <option value="multi">multi</option>
                <option value="zimage">zimage</option>
                <option value="sd35">sd35</option>
                <option value="flux2">flux2 🔒</option>
              </select>
            </label>
            <label>
              mode
              <select disabled>
                <option>{castPipeline === "multi" ? "ideate" : "t2i"}</option>
              </select>
            </label>
            <input
              className="prompt"
              placeholder="prompt…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onGenerate();
              }}
            />
            {castPipeline === "multi" ? (
              <>
                <label className="n" title="seeds per cast → num_candidates × pipelines">
                  cand
                  <input
                    type="number"
                    min={1}
                    max={5}
                    value={numCandidates}
                    onChange={(e) => setNumCandidates(clamp(parseInt(e.target.value || "1", 10), 1, 5))}
                  />
                </label>
                <label title="fast = klein-4b/turbo (lighter); refined = klein-9b/large">
                  preset
                  <select value={ideationMode}
                          onChange={(e) => setIdeationMode(e.target.value as "fast" | "refined")}>
                    <option value="fast">fast</option>
                    <option value="refined">refined</option>
                  </select>
                </label>
              </>
            ) : (
              <label className="n">
                N
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={count}
                  onChange={(e) => setCount(clamp(parseInt(e.target.value || "1", 10), 1, 8))}
                />
              </label>
            )}
            <button
              className="ghost"
              onClick={() => setShowParamsA((v) => !v)}
              title="show/hide every tunable parameter (unset = the model default)"
            >
              ⚙ params{Object.keys(advParamsA).length ? ` (${Object.keys(advParamsA).length})` : ""}
            </button>
            <button
              className="ghost"
              onClick={onPreviewGenerate}
              disabled={conn !== "online" || !project?.open}
              title="dry-run: review the resolved prompt + the exact worker command, no GPU"
            >
              Preview
            </button>
            <button
              onClick={onGenerate}
              disabled={
                conn !== "online" ||
                !project?.open ||
                disk?.blocked === true ||
                launch?.weights_ok === false
              }
            >
              {activeAsset ? "Cast ▶" : "Generate ▶"}
            </button>
          </div>
          )}
          {(!activeAsset || stage === "A") && showParamsA && (
            <div className="generate-bar params-bar">
              <ParamControls
                specs={castPipeline === "multi"
                  ? catalog?.multi?.params ?? MULTI_PARAM_SPECS
                  : catalog?.[castPipeline]?.params ?? []}
                mode={castPipeline === "multi" ? "ideate" : "t2i"}
                values={advParamsA}
                onChange={(k, v) => setAdvParamsA((p) => {
                  const next = { ...p };
                  if (v === undefined) delete next[k]; else next[k] = v;
                  return next;
                })}
              />
              <button className="ghost" onClick={() => setAdvParamsA({})}
                      title="clear all overrides (back to model defaults)">reset</button>
            </div>
          )}

          {/* Stage B — coverage-matrix dataset recipe (img2img from the hero) */}
          {activeAsset && stage === "B" && casting.some((c) => c.starred) && (
            <div className="hero-strip">
              <span className="style-label">base image (the Stage-A hero ★ — every cell img2img's from it):</span>
              {casting.filter((c) => c.starred).map((c) => (
                <img
                  key={c.id}
                  className="hero-thumb"
                  src={castingUrl(activeAsset.id, c.file)}
                  alt={c.file}
                  title={`${c.pipeline ?? "?"} · seed ${c.seed ?? "?"} — saved in casting/`}
                />
              ))}
              {anchorInfo && (
                <>
                  <span className="style-label">⚓ face anchor (identity lock):</span>
                  <img
                    className="hero-thumb"
                    src={`${anchorUrl(activeAsset.id)}?_=${encodeURIComponent(anchorInfo.set_at)}`}
                    alt="face anchor"
                    title={`set ${anchorInfo.set_at} from ${anchorInfo.source_output ?? "?"}`}
                  />
                  {!activeVersionLocked && (
                    <button className="ghost" onClick={onClearAnchor}
                            title="clear the anchor (opt this version out of the identity lock)">
                      ✕
                    </button>
                  )}
                </>
              )}
            </div>
          )}
          {activeAsset && stage === "B" && (
            <div className="generate-bar">
              <label title="coverage-matrix preset (R111): candidate count vs detail">
                recipe
                <select value={recipePreset}
                        onChange={(e) => setRecipePreset(e.target.value as RecipePreset)}>
                  {recipePresets.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </label>
              <label title="expansion family: zimage/sd35 = img2img sweep from the hero; flux2 = identity-preserving REFERENCE conditioning (hero carried into new poses/scenes — §11)">
                pipeline
                <select value={stageBPipeline}
                        onChange={(e) => {
                          const v = e.target.value as "zimage" | "sd35" | "flux2";
                          setStageBPipeline(v);
                          setStageBModel("");   // reset variant when the family changes
                          setAdvParamsB({});    // tunables are per-pipeline — a stale
                                                // zimage-only key would 422 on sd35
                          if (v === "flux2") setRealize("img2img");  // flux2 has no mixed axis
                        }}>
                  <option value="zimage">zimage</option>
                  <option value="sd35">sd35</option>
                  <option value="flux2">flux2 — identity ✨</option>
                </select>
              </label>
              <label title="model variant (default = the pipeline's; ⚠ gated variants need a fetch)">
                model
                <select value={stageBModel} onChange={(e) => setStageBModel(e.target.value)}>
                  <option value="">default</option>
                  {(catalog?.[stageBPipeline]?.variants ?? []).map((v) => (
                    <option key={v.id} value={v.id}>{v.id}{v.gated ? " 🔒" : ""}</option>
                  ))}
                </select>
              </label>
              {stageBPipeline === "flux2" ? (
                <span className="muted" title="flux2 (§11) conditions on the hero ★ as an in-context reference — identity is carried into each cell's pose/scene, so there is no img2img strength or mixed/inpaint axis">
                  ✨ reference-conditioned (identity-preserving)
                </span>
              ) : (
                <>
                  <label className="n" title="img2img strength (0–1): higher = more variation, less identity">
                    strength
                    <input type="number" min={0.1} max={1} step={0.05} value={stageBStrength}
                           onChange={(e) => setStageBStrength(clamp(parseFloat(e.target.value || "0.55"), 0.1, 1))} />
                  </label>
                  <label title="cell realization (M3.5): img2img only, or mixed — inpaint-method cells repaint the BACKGROUND around the held subject (background diversity; needs a hero matte)">
                    realize
                    <select value={realize}
                            onChange={(e) => setRealize(e.target.value as "img2img" | "mixed")}>
                      <option value="img2img">img2img</option>
                      <option value="mixed" disabled={!bgMask}>
                        mixed{bgMask ? "" : " (matte first)"}
                      </option>
                    </select>
                  </label>
                </>
              )}
              <button
                className="ghost"
                onClick={onMatteHero}
                disabled={conn !== "online" || !project?.open || !hasHero || busy}
                title="matte the hero ★ (BiRefNet): subject matte + cutout + the background-inpaint mask that `mixed` realization uses"
              >
                {bgMask ? "Matte ✓" : "Matte hero"}
              </button>
              <label
                title={stageBPipeline === "flux2"
                  ? "flux2 reference-conditioning ALREADY carries identity — this is an OPTIONAL extra inswapper face-lock pass (off by default), tick to also run it"
                  : !anchorInfo
                  ? "set a ⚓ face anchor first (select a face image → '⚓ anchor' in the inspector)"
                  : anchorVerified
                  ? "identity-lock pass (M4): swap every cell's face to the ⚓ anchor after generation (no-face cells pass through)"
                  : "anchor UNVERIFIED — tick to run identity now (the first run verifies the anchor face, then it defaults on)"}
              >
                {/* flux2 defaults identity OFF (the reference is the identity mechanism) — the
                    checkbox must reflect that, not claim a lock the backend skips (review). */}
                ⚓ identity{stageBPipeline === "flux2" ? " (extra)" : anchorInfo && !anchorVerified ? " ?" : ""}
                <input
                  type="checkbox"
                  checked={stageBPipeline === "flux2"
                    ? (identityOn ?? false)
                    : (identityOn ?? (Boolean(anchorInfo) && anchorVerified))}
                  disabled={!anchorInfo}
                  onChange={(e) => setIdentityOn(e.target.checked)}
                />
              </label>
              <input
                className="prompt"
                placeholder="character clause (identity — defaults to the saved prompt template)…"
                value={characterClause}
                onChange={(e) => setCharacterClause(e.target.value)}
              />
              <button
                className="ghost"
                onClick={() => setShowParamsB((v) => !v)}
                title="show/hide every tunable parameter (unset = the model default)"
              >
                ⚙ params{Object.keys(advParamsB).length ? ` (${Object.keys(advParamsB).length})` : ""}
              </button>
              <button
                className="ghost"
                onClick={onPreviewStageB}
                disabled={conn !== "online" || !project?.open || !hasHero || busy}
                title="dry-run: review the planned dataset (job count, first cell, argv), no GPU"
              >
                Preview
              </button>
              <button
                onClick={onStageB}
                disabled={conn !== "online" || !project?.open || !hasHero || busy ||
                          disk?.blocked === true}
                title={hasHero ? "" : "star a hero in Stage A first"}
              >
                {busy ? "…" : "Generate Dataset ▶"}
              </button>
            </div>
          )}
          {activeAsset && stage === "B" && (
            <div className="generate-bar sketch-bar">
              <span className="style-label"
                    title="M7 video-sketch harvest (R11): a cheap low-res motion sketch from the hero ★, aimed at ONE coverage cell; the chained harvest extracts stills carrying that cell — pose/angle coverage img2img can't reach">
                🎬 sketch
              </span>
              <label title="target angle the motion should reach">
                angle
                <select value={skAngle} onChange={(e) => setSkAngle(e.target.value)}>
                  {COV_ANGLES.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              <label title="target shot size">
                shot
                <select value={skShot} onChange={(e) => setSkShot(e.target.value)}>
                  {COV_SHOT_SIZES.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              <label title="target expression">
                expr
                <select value={skExpr} onChange={(e) => setSkExpr(e.target.value)}>
                  {COV_EXPRESSIONS.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              <input
                className="prompt"
                placeholder="motion prompt (optional — e.g. 'turns slowly to the left, then looks back')…"
                value={skMotion}
                onChange={(e) => setSkMotion(e.target.value)}
              />
              <label className="n" title="harvest every k-th frame">
                every
                <input type="number" min={1} max={60} value={skEvery}
                       onChange={(e) => setSkEvery(clamp(parseInt(e.target.value || "6", 10), 1, 60))} />
              </label>
              <label className="n" title="max harvested frames per sketch">
                frames
                <input type="number" min={1} max={120} value={skMax}
                       onChange={(e) => setSkMax(clamp(parseInt(e.target.value || "24", 10), 1, 120))} />
              </label>
              <button
                onClick={() => void onSketch()}
                disabled={conn !== "online" || !project?.open || !hasHero || busy ||
                          disk?.blocked === true}
                title={hasHero ? "fire the sketch — the video is an intermediate; its harvested frames stream in as curatable tiles" : "star a hero in Stage A first"}
              >
                🎬 Sketch ▶
              </button>
            </div>
          )}
          {activeAsset && stage === "B" && showParamsB && (
            <div className="generate-bar params-bar">
              <ParamControls
                specs={catalog?.[stageBPipeline]?.params ?? []}
                mode="img2img"
                values={advParamsB}
                onChange={(k, v) => setAdvParamsB((p) => {
                  const next = { ...p };
                  if (v === undefined) delete next[k]; else next[k] = v;
                  return next;
                })}
                exclude={["model_name", "strength", "init_image", "mask_image"]}
              />
              <button className="ghost" onClick={() => setAdvParamsB({})}
                      title="clear all overrides (back to model defaults)">reset</button>
            </div>
          )}
          {activeAsset && stage === "B" && !hasHero && (
            <div className="banner">Star a hero ★ in Stage A before expanding — Stage B img2img's from it.</div>
          )}
          {activeAsset && (stage === "B" || stage === "C") && partialDatasets.length > 0 && (
            <div className="banner">
              ⚠ Partial dataset{partialDatasets.length > 1 ? "s" : ""}:{" "}
              {partialDatasets
                .map((j) => {
                  const b = j.result!.batch!;
                  return `${b.ok}/${b.count} cells (${b.failed} failed, ${b.skipped} skipped`
                    + `${b.status === "stopped" ? ", stopped early" : ""})`;
                })
                .join(" · ")}{" "}
              — the coverage matrix is incomplete; re-run Stage B for the rest or curate what's there.
            </div>
          )}

          {/* Stage C — curation throughput (P1-12): filters + bulk keep/reject + keys */}
          {activeAsset && stage === "C" && (
            <div className="generate-bar curate-bar">
              <label title="filter by coverage shot size">
                shot
                <select value={filterShot} onChange={(e) => setFilterShot(e.target.value)}>
                  <option value="">all</option>
                  {COV_SHOT_SIZES.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              <label title="filter by coverage angle">
                angle
                <select value={filterAngle} onChange={(e) => setFilterAngle(e.target.value)}>
                  <option value="">all</option>
                  {COV_ANGLES.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              <label title="filter by coverage expression">
                expr
                <select value={filterExpr} onChange={(e) => setFilterExpr(e.target.value)}>
                  <option value="">all</option>
                  {COV_EXPRESSIONS.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              <label title="show tiles you already rejected (dimmed; ↩ to un-reject)">
                <input type="checkbox" checked={showRejected}
                       onChange={(e) => setShowRejected(e.target.checked)} />
                show rejected
              </label>
              <span className="muted">
                kept {refSet.length} · rejected {rejected.length} · showing{" "}
                {stageCells.length}/{cells.length}
              </span>
              {bulkSel.size > 0 && !activeVersionLocked && (
                <>
                  <button className="proj-btn" onClick={() => void onBulk("keep")}
                          disabled={busy} title="keep every selected tile into the ref set">
                    ✓ keep {bulkSel.size}
                  </button>
                  <button className="proj-btn" onClick={() => void onBulk("reject")}
                          disabled={busy} title="reject every selected tile (persistent, reversible)">
                    ✕ reject {bulkSel.size}
                  </button>
                  <button className="ghost" onClick={() => setBulkSel(new Set())}>clear</button>
                </>
              )}
              {activeVersionLocked && (
                <span className="muted" title="finalized = locked (R60): curation is read-only — create a new version to change the set">
                  🔒 read-only (finalized)
                </span>
              )}
              <span className="muted" title="click the grid first so it has keyboard focus">
                keys: ←→↑↓ move · k keep · x reject · space select
              </span>
            </div>
          )}
          {/* Stage C — Save the curated AssetProfile (the MVP done-line) */}
          {activeAsset && stage === "C" && (
            <div className="style-bar">
              <span className="style-label">kept {refSet.length}</span>
              <input
                className="style-frag"
                placeholder="prompt template (identity clause saved into the AssetProfile)…"
                value={promptTemplate}
                disabled={activeVersionLocked}
                onChange={(e) => setPromptTemplate(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") onSaveProfile(); }}
              />
              <button className="proj-btn" onClick={onSaveProfile}
                      disabled={conn !== "online" || !project?.open || activeVersionLocked}
                      title={activeVersionLocked
                        ? "finalized = locked (R60) — create a new version to edit" : ""}>
                Save AssetProfile{activeVersionLocked ? " 🔒" : ""}
              </button>
            </div>
          )}
          {project?.open && (
            <div className="style-bar">
              <span className="style-label">L1 style</span>
              <select className="style-sel" value={genStyleId}
                      onChange={(e) => onSelectGenStyle(e.target.value)}
                      title="which L1 style applies to THIS generation (★ = the project default). Edit its fragment here; add/delete/set-default in L1·World.">
                {(styles?.styles ?? []).map((st) => (
                  <option key={st.id} value={st.id}>
                    {st.name}{styles && st.id === styles.active_style_id ? " ★" : ""}
                  </option>
                ))}
              </select>
              <input
                className="style-frag"
                placeholder="style fragment (auto-applied — appended; full editor in L1·World)…"
                value={styleDraft}
                onChange={(e) => setStyleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onSaveStyle();
                }}
              />
              <button className="proj-btn" onClick={onSaveStyle}
                      disabled={styleDraft === (styles?.styles.find((x) => x.id === genStyleId)?.fragment ?? "")
                                && applyStyle === (style?.enabled_default ?? true)}>
                Save
              </button>
              <label className="apply-style"
                     title="apply the selected style to generation — appended after the character prompt (R104, amended 2026-06-10); Save persists the on/off as the default">
                <input type="checkbox" checked={applyStyle}
                       onChange={(e) => setApplyStyle(e.target.checked)} />
                apply
              </label>
            </div>
          )}
          {launch && !launch.weights_ok && (
            <div className="banner banner-err">
              ⛔ Required model weight(s) missing: {launch.weights_missing.join(", ")}.{" "}
              <button className="link" onClick={onFetchWeights} disabled={fetching}>
                {fetching ? "fetching…" : "Fetch now"}
              </button>{" "}
              (explicit, on-demand — R163).
            </div>
          )}
          {conn === "online" && !project?.open && (
            <div className="banner">
              No project open. <button className="link" onClick={onNewProject}>Create</button> or{" "}
              <button className="link" onClick={onTogglePicker}>open</button> a project to generate.
            </div>
          )}
          {disk?.blocked && (
            <div className="banner banner-err">
              ⛔ Disk hard-stop — {disk.reason}. Free space or raise the project size cap; new jobs
              are blocked (running jobs finish).
            </div>
          )}
          {paused && (
            <div className="banner">
              ⏸ Queue paused
              {pauseReason === "resume"
                ? " — resumed from last session; pending work is held for review (R88)"
                : ""}{" "}
              ({counts.queued} queued{counts.running ? `, ${counts.running} running` : ""}).
              New jobs will wait —{" "}
              <button className="link" onClick={onUnpause}>unpause ▶</button>
            </div>
          )}
          {error && <div className="error">⚠ {error}</div>}

          <div className="grid" tabIndex={0} onKeyDown={onGridKey}>
            {stageCells.length === 0 && (
              <p className="muted center span">
                {!activeAsset
                  ? "Fire a batch — results stream in here (the casting-grid embryo)."
                  : stage === "A"
                  ? `Cast ${activeAsset.name} — candidates stream into this grid (Stage A).`
                  : stage === "B"
                  ? "Pick a recipe + Generate Dataset — img2img variations stream in (Stage B)."
                  : cells.length > 0
                  ? "Nothing matches the curation filters — relax them (or untick a filter)."
                  : "Stage-B candidates appear here to keep ✓ / cull ✕ into the curated ref set (Stage C)."}
              </p>
            )}
            {stageCells.map((c) => (
              <GridCell
                key={c.key}
                refSrc={c.refItem && activeAsset
                  ? refUrl(activeAsset.id, c.refItem.file) : undefined}
                locked={activeVersionLocked}
                job={c.job}
                output={c.output}
                interim={c.interim ?? false}
                selected={selected === c.key}
                onClick={() => setSelected(c.key)}
                onView={(src) => setViewer(src)}
                onCancel={() => c.job && onCancel(c.job.id)}
                onDelete={() => c.job && onDelete(c.job.id)}
                castable={!!activeAsset && stage === "A"}
                isHero={!!c.output && starredOutputs.has(c.output)}
                onStar={() => c.job && onStar(c.job.id, c.output)}
                curating={stage === "C"}
                isKept={c.refItem ? true : (!!c.output && keptByOutput.has(c.output))}
                onKeep={() => c.job && onKeep(c.job.id, c.output)}
                onCull={() => {
                  const rid = c.refItem?.id
                    ?? (c.output ? keptByOutput.get(c.output) : undefined);
                  if (rid) onCull(rid);
                }}
                isRejected={!!c.output && rejectedSet.has(c.output)}
                onReject={(flag) => c.job && onReject(c.job.id, c.output, flag)}
                bulkSelected={bulkSel.has(c.key)}
                onToggleBulk={() => setBulkSel((s) => {
                  const n = new Set(s);
                  if (n.has(c.key)) n.delete(c.key); else n.add(c.key);
                  return n;
                })}
              />
            ))}
          </div>
          </>
          )}
        </main>

        <aside className="inspector">
          <div className="rail-head">INSPECTOR</div>
          {!selJob && <div className="muted">Select an image to see job details + lineage.</div>}
          {selJob && activeAsset && selJob.status === "done" && !activeVersionLocked && (
            <>
              <button
                className="ghost"
                onClick={() => void onSetAnchor(selJob.id, selOutput)}
                title="use this image as the version's ⚓ face anchor (M4, R94) — the Stage-B identity pass locks every cell's face to it"
              >
                ⚓ set as face anchor
              </button>
              <button
                className="ghost"
                onClick={() => void onDerivePortrait(selJob.id, selOutput)}
                title="derive a restored 512² face PORTRAIT from this image (GFPGAN crop of the largest face) — a much better anchor base than a small face in a full-body shot; anchor the resulting tile"
              >
                ✨ face portrait
              </button>
            </>
          )}
          {selJob && <Inspector job={selJob} output={selOutput} />}
          {/* M0c — inline postprocess stack BELOW the selected image. PROJECT-level: works on
              ANY done image (Sandbox or any character, any pipeline) — not videos. */}
          {selJob && project?.open && selJob.status === "done"
            && selBase && !/\.(mp4|webm|mov)$/i.test(selBase) && (
            <PostprocPanel
              stack={postprocStacks.find((s) => s.base === selBase)}
              jobs={jobs}
              busy={busy}
              onAdd={(preset, backend, params) =>
                void onAddPostprocStep(selBase, preset, backend, params)}
              onQueue={onQueuePostprocStep}
              onRemove={onRemovePostprocStep}
              onView={(o) => setViewer(outputUrl(o))}
            />
          )}
        </aside>
      </div>

      <footer className="dock">
        {/* Honest queue states (review 2026-06-10): "running" only when a job IS running;
            queued-but-held shows WHY (paused with its reason / disk hold / starting). */}
        <span className={`status dot-${paused ? "warn" : counts.running > 0 ? "warn" : dot}`}>
          <i className="dot" /> JOB QUEUE{" "}
          {paused
            ? `⏸ paused${pauseReason === "resume" ? " (resumed last session)" : ""} — ${counts.queued} queued`
            : counts.running > 0
            ? `▶ running ${counts.running}${counts.queued ? ` · ${counts.queued} queued` : ""}`
            : counts.queued > 0
            ? disk?.blocked
              ? `⛔ held (disk) — ${counts.queued} queued`
              : `▸ starting (${counts.queued} queued)`
            : `· idle`}
        </span>
        <div className="queue-wrap">
          <button className="proj-btn" onClick={() => setShowQueue((v) => !v)}
                  title="list queued/running jobs">
            jobs {showQueue ? "▴" : "▾"}
          </button>
          {showQueue && <QueuePanel jobs={jobs} onCancel={onCancel} onStop={onStop} />}
        </div>
        <span className="meter">
          done {counts.done}
          {counts.failed ? ` · failed ${counts.failed}` : ""}
          {counts.canceled ? ` · canceled ${counts.canceled}` : ""}
        </span>
        <span className={`meter disk-${disk?.state ?? "ok"}`} title={disk?.reason ?? "disk OK"}>
          {disk?.project
            ? `proj ${disk.project.used_gb.toFixed(1)}/${disk.project.cap_gb}G`
            : `proj —/${project?.size_cap_gb ?? 250}G`}
          {disk?.disk ? ` · disk ${disk.disk.free_pct.toFixed(0)}% free` : ""}
          {disk?.state === "warn" ? " ⚠" : disk?.state === "hard" ? " ⛔" : ""}
        </span>
        {paused && (
          <button className="unpause" onClick={onUnpause}>
            unpause ▶
          </button>
        )}
      </footer>
      {showNewVersion && activeAsset && (
        <div className="modal-overlay" onClick={() => setShowNewVersion(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="rail-head">NEW VERSION — copy-on-create (R50/R58/R59)</div>
            <div className="modal-label">name (optional — e.g. "scar", "winter_outfit"):</div>
            <input
              className="prompt"
              autoFocus
              value={newVerName}
              placeholder={`v${versionList.length + 1}`}
              onChange={(e) => setNewVerName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void onCreateVersionConfirm(); }}
            />
            <div className="modal-label">
              parent — the new version is a FULL copy of it (refs, casting, face anchor);
              any prior version works, finalized included:
            </div>
            <select value={newVerParent} onChange={(e) => setNewVerParent(e.target.value)}>
              {versionList.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}{v.finalized ? " 🔒" : ""}
                  {v.id === activeAsset.active_version ? " (active)" : ""}
                </option>
              ))}
            </select>
            <div className="modal-actions">
              <button className="proj-btn" onClick={() => setShowNewVersion(false)}>
                Cancel
              </button>
              <button className="modal-run" onClick={() => void onCreateVersionConfirm()}>
                Create ▶
              </button>
            </div>
          </div>
        </div>
      )}
      {preview && (
        <div className="modal-overlay" onClick={() => { setPreview(null); pendingRunRef.current = null; }}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="rail-head">PRE-FLIGHT REVIEW — dry run, no GPU spent</div>
            {"first_cell" in preview ? (
              <>
                <dl>
                  <dt>recipe</dt>
                  <dd>
                    {preview.preset} → <b>{preview.planned_jobs} batch job{preview.planned_jobs > 1 ? "s" : ""} · {preview.items ?? "?"} cells</b>{" "}
                    {preview.split && Object.keys(preview.split).length > 1
                      ? `(${Object.entries(preview.split).map(([m, n]) => `${n} ${m}`).join(" + ")}; keep ~${preview.kept_target.join("–")})`
                      : `(model loads once; keep ~${preview.kept_target.join("–")})`}
                  </dd>
                  <dt>pipeline</dt><dd>{preview.pipeline}</dd>
                  <dt>hero</dt><dd className="mono">{preview.hero.split(/[\\/]/).pop()}</dd>
                  <dt>cell 1/{preview.items ?? "?"}</dt>
                  <dd>{preview.first_cell.coverage_cell.shot_size} · {preview.first_cell.coverage_cell.angle} · {preview.first_cell.coverage_cell.expression} · {preview.first_cell.coverage_cell.background} (seed {preview.first_cell.seed})</dd>
                </dl>
                <div className="modal-label">first cell prompt (style + clause + cell):</div>
                <div className="modal-prompt">{preview.first_cell.prompt}</div>
                <div className="modal-label">worker command (cell 1):</div>
                <pre className="modal-argv mono">{preview.first_argv.join(" ")}</pre>
              </>
            ) : (
              <>
                <dl>
                  <dt>pipeline</dt><dd>{preview.pipeline}</dd>
                  <dt>jobs</dt>
                  <dd>
                    {preview.num_candidates != null
                      ? `1 cast → ${preview.num_candidates} seed(s) × 3 pipelines = ${preview.num_candidates * 3} candidates`
                      : `${preview.count} job(s)`}
                  </dd>
                  <dt>output</dt><dd className="mono">{preview.output_dir}</dd>
                </dl>
                <div className="modal-label">resolved prompt (style prepend included):</div>
                <div className="modal-prompt">{preview.prompt}</div>
                <div className="modal-label">worker command:</div>
                <pre className="modal-argv mono">{preview.argv.join(" ")}</pre>
              </>
            )}
            {preview.post_passes && preview.post_passes.length > 0 && (
              <>
                <div className="modal-label">chained post-passes (run after, one batch job each):</div>
                <div className="modal-prompt">
                  {preview.post_passes
                    .map((p) => `${p.pass}: ${p.backend}${p.model_name ? ` / ${p.model_name}` : " (default model)"} @ strength ${p.strength}`)
                    .join("  →  ")}
                </div>
              </>
            )}
            <div className="modal-actions">
              <button className="proj-btn" onClick={() => { setPreview(null); pendingRunRef.current = null; }}>
                Close
              </button>
              <button
                className="modal-run"
                onClick={() => {
                  const run = pendingRunRef.current;
                  setPreview(null);
                  pendingRunRef.current = null;
                  run?.();
                }}
              >
                Run ▶
              </button>
            </div>
          </div>
        </div>
      )}
      {viewer && (
        <div className="viewer-backdrop" onClick={() => setViewer(null)}
             title="click anywhere / Esc to close">
          <img className="viewer-img" src={viewer} alt="full resolution"
               onClick={(e) => e.stopPropagation()} />
          <button className="viewer-close" onClick={() => setViewer(null)} title="close (Esc)">
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

/** Queued/running jobs at a glance — id, pipeline/stage, progress, the runner's `note`
 * (OOM retry, re-queued at shutdown, …) which previously was never rendered anywhere. */
function QueuePanel({ jobs, onCancel, onStop }: {
  jobs: Record<string, Job>;
  onCancel: (id: string) => void;
  onStop: (id: string) => void;
}) {
  const order: Record<string, number> = { running: 0, queued: 1 };
  const active = Object.values(jobs)
    .filter((j) => j.status === "queued" || j.status === "running")
    .sort((a, b) => (order[a.status] - order[b.status]) || a.created_at.localeCompare(b.created_at));
  return (
    <div className="queue-panel">
      {active.length === 0 && <div className="picker-empty">queue is empty</div>}
      {active.map((j) => {
        const items = (j.params as Record<string, unknown>)?.batch_items as unknown[] | undefined;
        return (
          <div key={j.id} className="queue-row">
            <span className={`q-st q-${j.status}`}>{j.status === "running" ? "▶" : "⏳"}</span>
            <span className="mono q-id" title={j.id}>{j.id.slice(0, 12)}</span>
            <span className="q-pipe">
              {j.pipeline}/{j.mode}
              {j.pass ? ` · ${j.pass}⤴` : ""}
              {j.stage ? ` · ${j.stage}` : ""}
              {items ? ` · batch ×${items.length}` : ""}
              {j.batch_size > 1 ? ` · ${j.index + 1}/${j.batch_size}` : ""}
            </span>
            <span className="q-prog">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : ""}</span>
            {j.note ? <span className="q-note" title={j.note}>{j.note}</span> : <span className="q-note" />}
            {j.status === "running" && !!items && (
              <button className="picker-forget" title="stop after the current image (keeps completed)"
                      onClick={() => onStop(j.id)}>⏹</button>
            )}
            <button className="picker-forget" title="cancel (kills + discards partial)"
                    onClick={() => onCancel(j.id)}>✕</button>
          </div>
        );
      })}
    </div>
  );
}

function GridCell({
  job,
  output,
  interim = false,
  selected,
  onClick,
  onView,
  onCancel,
  onDelete,
  castable = false,
  isHero = false,
  onStar,
  curating = false,
  isKept = false,
  onKeep,
  onCull,
  isRejected = false,
  onReject,
  bulkSelected = false,
  onToggleBulk,
  refSrc,
  locked = false,
}: {
  job?: Job;
  output?: string;
  interim?: boolean;
  selected: boolean;
  onClick: () => void;
  onView?: (src: string) => void;
  onCancel: () => void;
  onDelete: () => void;
  castable?: boolean;
  isHero?: boolean;
  onStar?: () => void;
  curating?: boolean;
  isKept?: boolean;
  onKeep?: () => void;
  onCull?: () => void;
  isRejected?: boolean;
  onReject?: (flag: boolean) => void;
  bulkSelected?: boolean;
  onToggleBulk?: () => void;
  /** durable ref tile (M5): served from the version's refs/ dir — no job behind it. */
  refSrc?: string;
  /** the active version is finalized (R60) — hide every mutating control. */
  locked?: boolean;
}) {
  const status = job?.status ?? "queued";
  // For a multi candidate, `output` is this tile's specific image; else the job's single output.
  const name = output ?? job?.result?.output_name;
  const prog = job?.progress ?? 0;
  const active = status === "queued" || status === "running";
  const terminal = status === "done" || status === "failed" || status === "canceled";
  const done = status === "done";
  // Tile aspect follows the job's actual output dims (review 2026-06-10 #3: 1024×1024
  // Stage-B images were cropped into the fixed 16:9 cell). Fallback = the t2i default.
  const w = Number(job?.params?.width) || 1280;
  const h = Number(job?.params?.height) || 720;
  // Durable ref tiles (M5, F1): a copied version's kept refs have no job behind them —
  // they render from refs/ as kept (cull works through the ref id), always "done".
  const isRef = !!refSrc;
  // M7: a video-sketch output is an INTERMEDIATE (its harvested frames are the refs) —
  // render a 🎬 placeholder, never curation controls.
  const isVideo = !!name && /\.(mp4|webm|mov)$/i.test(name);
  // Interim tiles (a running cast's already-landed candidates) show their image early.
  const showImg = (!!name && !isVideo && (done || interim)) || isRef;
  // A done job that had chained post-passes (clean/polish/identity/restore) — the tile is
  // the PRE-pass image; its processed pass outputs land as separate tiles. We MARK it
  // (bottom-left) but still allow curating it: keeping is a deliberate choice and EVERY
  // output is selectable (user 2026-06-13; the UI sends allow_unlocked=true).
  const pendingPasses = (job?.post_passes ?? []).map((p) => p.pass);
  const prePass = done && pendingPasses.length > 0;
  const curable = curating && (done || isRef) && !locked && !isVideo;
  return (
    <div
      className={`cell ${selected ? "sel" : ""} ${isHero ? "hero" : ""} ${isKept ? "kept" : ""} ${isRejected ? "rejected" : ""} ${bulkSelected ? "bulksel-on" : ""} st-${isRef ? "done" : status}`}
      style={{ aspectRatio: `${w} / ${h}` }}
      role="button"
      tabIndex={0}
      onClick={onClick}
    >
      {showImg ? (
        <img src={isRef ? refSrc : outputUrl(name!)} alt={isRef ? "curated ref" : job?.id}
             title={isRef ? "curated ref (durable copy in the version's refs/)" : undefined} />
      ) : (
        <span className="cell-status">
          {status === "queued" && "queued…"}
          {status === "running" && "generating…"}
          {status === "failed" && "✕ failed"}
          {status === "canceled" && "⊘ canceled"}
          {status === "done" && (isVideo ? "🎬 video sketch (frames follow ⤵)" : "—")}
        </span>
      )}
      {showImg && onView && (
        <button
          className="view"
          title="view full resolution"
          onClick={(e) => { e.stopPropagation(); onView(isRef ? refSrc! : outputUrl(name!)); }}
        >
          🔍
        </button>
      )}
      {status === "running" && !interim && (
        <div className="progress">
          <div className="bar" style={{ width: `${Math.round(prog * 100)}%` }} />
        </div>
      )}
      {active && !interim && !isRef && (
        <button
          className="cancel"
          title="cancel"
          onClick={(e) => {
            e.stopPropagation();
            onCancel();
          }}
        >
          ✕
        </button>
      )}
      {terminal && (
        <button
          className="delete"
          title="delete this generation + its files"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          🗑
        </button>
      )}
      {castable && done && onStar && !locked && (
        <button
          className={`star ${isHero ? "on" : ""}`}
          title={isHero ? "the hero ★ — click to un-star" : "star as hero (save into casting)"}
          onClick={(e) => {
            e.stopPropagation();
            onStar();
          }}
        >
          {isHero ? "★" : "☆"}
        </button>
      )}
      {prePass && (
        <span
          className="prelock"
          title={`pre-pass image — ${pendingPasses.join(" → ")} (the processed pass outputs appear as separate tiles; this one is still keepable)`}
        >
          {pendingPasses.join("→")}
        </span>
      )}
      {curable && (onKeep || onCull) && (
        <button
          className={`keep ${isKept ? "on" : ""}`}
          title={isKept ? "kept ✓ — click to cull from the ref set" : "keep ✓ into the curated ref set"}
          onClick={(e) => {
            e.stopPropagation();
            if (isKept) onCull?.();
            else onKeep?.();
          }}
        >
          {isKept ? "✓" : "+"}
        </button>
      )}
      {curable && !isRef && onReject && !isKept && (
        <button
          className={`reject ${isRejected ? "on" : ""}`}
          title={isRejected ? "rejected — click to un-reject (↩)" : "reject ✕ (persistent cull-from-view; reversible)"}
          onClick={(e) => {
            e.stopPropagation();
            onReject(!isRejected);
          }}
        >
          {isRejected ? "↩" : "✕"}
        </button>
      )}
      {curable && !isRef && onToggleBulk && (
        <button
          className={`bulksel ${bulkSelected ? "on" : ""}`}
          title="select for bulk keep/reject (or press space on the focused tile)"
          onClick={(e) => {
            e.stopPropagation();
            onToggleBulk();
          }}
        >
          {bulkSelected ? "■" : "□"}
        </button>
      )}
    </div>
  );
}

// M8 — L1 World authoring: world prose, the style fragment + global negative, and the
// story spine (premise + characters that materialize into stub AssetProfiles, R55).
function WorldWorkspace({ project, tab, onError, onStubCreated, onStylesChanged }: {
  project: ProjectInfo | null;
  tab: "styles" | "world" | "spine";   // M0b — which L1 sub-tab the rail selected
  onError: (e: string) => void;
  onStubCreated: () => void;
  onStylesChanged: () => void;    // tell the parent to reload styles (the L2 bar selector)
}) {
  const [bible, setBible] = useState<BibleInfo | null>(null);
  const [stylesData, setStylesData] = useState<StylesInfo | null>(null);
  const [worldDraft, setWorldDraft] = useState("");
  const [premiseDraft, setPremiseDraft] = useState("");
  const [newName, setNewName] = useState("");
  const [newSnippet, setNewSnippet] = useState("");
  const [newStyleName, setNewStyleName] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    if (!project?.open) { setBible(null); setStylesData(null); return; }
    try {
      const [b, st] = await Promise.all([getBible(), getStyles()]);
      setBible(b);
      setStylesData(st);
      setWorldDraft(b.world ?? "");
      setPremiseDraft(b.spine?.premise ?? "");
    } catch (e) { onError(String(e)); }
  };
  useEffect(() => { void refresh(); /* eslint-disable-next-line */ }, [project?.open, project?.id]);

  const guard = async (fn: () => Promise<BibleInfo>) => {
    setBusy(true);
    try { setBible(await fn()); } catch (e) { onError(String(e)); } finally { setBusy(false); }
  };
  // Style-collection mutations return the full StylesInfo; refresh local + notify the parent.
  const guardStyles = async (fn: () => Promise<StylesInfo>) => {
    setBusy(true);
    try { setStylesData(await fn()); onStylesChanged(); }
    catch (e) { onError(String(e)); } finally { setBusy(false); }
  };

  if (!project?.open) {
    return <div className="world"><p className="muted">Open a project to author its L1 World.</p></div>;
  }
  const chars = bible?.spine?.characters ?? [];
  return (
    <div className="world">
      {/* M0b — one L1 sub-tab at a time (the rail picks it), each with only its own controls
          and readable multi-line editors instead of one long cramped scroll. */}
      {tab === "styles" && (
      <section className="world-sec">
        <h3>Visual styles <span className="muted">(named snippets — pick one per generation; ★ = the project default, R104)</span></h3>
        <div className="style-add">
          <input className="prompt" placeholder="new style name (e.g. Noir, Watercolor)…"
                 value={newStyleName} onChange={(e) => setNewStyleName(e.target.value)} />
          <button className="proj-btn" disabled={busy || !newStyleName.trim()}
                  onClick={() => { void guardStyles(() => addStyle(newStyleName.trim()));
                                   setNewStyleName(""); }}>+ add style</button>
        </div>
        {(stylesData?.styles ?? []).map((st) => (
          <StyleRow key={st.id} st={st} busy={busy}
            isActive={stylesData?.active_style_id === st.id}
            canDelete={(stylesData?.styles.length ?? 0) > 1}
            onSave={(name, fragment, neg) => void guardStyles(() =>
              updateStyle(st.id, { name, fragment, global_negative: neg }))}
            onDelete={() => void guardStyles(() => deleteStyle(st.id))}
            onSetActive={() => void guardStyles(() => setActiveStyle(st.id))} />
        ))}
      </section>
      )}

      {tab === "world" && (
      <section className="world-sec">
        <h3>World <span className="muted">(long-form summary — authoring context, not injected)</span></h3>
        <textarea className="world-prose" rows={18} value={worldDraft}
                  onChange={(e) => setWorldDraft(e.target.value)}
                  placeholder="# The world…  (markdown)" />
        <button className="proj-btn" disabled={busy}
                onClick={() => void guard(() => setWorld(worldDraft))}>Save world</button>
      </section>
      )}

      {tab === "spine" && (
      <section className="world-sec">
        <h3>Story spine <span className="muted">(premise + characters → stub profiles, R55)</span></h3>
        <label className="world-field">Premise / arc / factions</label>
        <textarea className="world-prose" rows={8} value={premiseDraft}
                  onChange={(e) => setPremiseDraft(e.target.value)}
                  placeholder="premise / arc / factions…" />
        <button className="proj-btn" disabled={busy}
                onClick={() => void guard(() => setPremise(premiseDraft))}>Save premise</button>

        <div className="spine-list">
          {chars.map((c) => (
            <SpineRow key={c.id} ch={c} busy={busy}
              onSave={(name, snippet) => void guard(() =>
                upsertSpineCharacter({ character_id: c.id, name, snippet }))}
              onDelete={() => void guard(() => removeSpineCharacter(c.id))}
              onStub={async () => {
                setBusy(true);
                try { await createSpineStub(c.id); onStubCreated(); await refresh(); }
                catch (e) { onError(String(e)); } finally { setBusy(false); }
              }}
              onResync={async () => {
                setBusy(true);
                try { await resyncSpineStub(c.id); onStubCreated(); }
                catch (e) { onError(String(e)); } finally { setBusy(false); }
              }} />
          ))}
          {chars.length === 0 && <p className="muted">no spine characters yet</p>}
        </div>

        <div className="spine-add">
          <input className="prompt" placeholder="character name (e.g. Mara)…"
                 value={newName} onChange={(e) => setNewName(e.target.value)} />
          <textarea className="spine-snippet" rows={2}
                    placeholder="prompt-template snippet (the fixed identity clause)…"
                    value={newSnippet} onChange={(e) => setNewSnippet(e.target.value)} />
          <button className="proj-btn" disabled={busy || !newName.trim()}
                  onClick={() => void guard(async () => {
                    const b = await upsertSpineCharacter({ name: newName.trim(),
                                                           snippet: newSnippet.trim() });
                    setNewName(""); setNewSnippet("");
                    return b;
                  })}>+ character</button>
        </div>
      </section>
      )}
    </div>
  );
}

function SpineRow({ ch, busy, onSave, onDelete, onStub, onResync }: {
  ch: SpineCharacter; busy: boolean;
  onSave: (name: string, snippet: string) => void;
  onDelete: () => void; onStub: () => void; onResync: () => void;
}) {
  const [name, setName] = useState(ch.name);
  const [snippet, setSnippet] = useState(ch.snippet);
  const dirty = name !== ch.name || snippet !== ch.snippet;
  const linked = !!ch.linked_asset_id;
  return (
    <div className="spine-row">
      <div className="spine-row-head">
        <input className="prompt spine-name" value={name} onChange={(e) => setName(e.target.value)}
               placeholder="character name…" />
        <button className="ghost" disabled={busy || !dirty}
                onClick={() => onSave(name, snippet)} title="save name/snippet (does NOT touch a linked profile — R55)">save</button>
        {linked ? (
          <button className="ghost" disabled={busy} onClick={onResync}
                  title="push this snippet into the linked profile's active version (overwrites — manual, R55)">⟳ re-sync</button>
        ) : (
          <button className="ghost" disabled={busy} onClick={onStub}
                  title="materialize a stub AssetProfile seeded with this snippet (R112)">+ stub profile</button>
        )}
        <button className="ghost" disabled={busy} onClick={onDelete} title="remove from spine (a linked profile is left intact)">✕</button>
        {linked && <span className="muted spine-linked" title={ch.linked_asset_id!}>● linked</span>}
      </div>
      <textarea className="spine-snippet" rows={2} value={snippet}
                onChange={(e) => setSnippet(e.target.value)}
                placeholder="identity clause snippet — the fixed prompt-template text…" />
    </div>
  );
}

function StyleRow({ st, busy, isActive, canDelete, onSave, onDelete, onSetActive }: {
  st: StyleEntry; busy: boolean; isActive: boolean; canDelete: boolean;
  onSave: (name: string, fragment: string, global_negative: string) => void;
  onDelete: () => void; onSetActive: () => void;
}) {
  const [name, setName] = useState(st.name);
  const [fragment, setFragment] = useState(st.fragment);
  const [neg, setNeg] = useState(st.global_negative ?? "");
  const dirty = name !== st.name || fragment !== st.fragment || neg !== (st.global_negative ?? "");
  return (
    <div className={`style-row ${isActive ? "active" : ""}`}>
      <div className="style-row-head">
        <input className="prompt style-name" value={name} onChange={(e) => setName(e.target.value)}
               placeholder="style name…" />
        {isActive
          ? <span className="muted" title="the project default (used when a generation doesn't pick one)">★ default</span>
          : <button className="ghost" disabled={busy} onClick={onSetActive}
                    title="make this the project default">set default</button>}
        <button className="ghost" disabled={busy || !dirty} onClick={() => onSave(name, fragment, neg)}>save</button>
        <button className="ghost" disabled={busy || !canDelete} onClick={onDelete}
                title={canDelete ? "delete this style" : "can't delete the last style"}>✕</button>
      </div>
      <textarea rows={8} className="style-frag-edit" value={fragment}
                onChange={(e) => setFragment(e.target.value)}
                placeholder="style fragment — appended after the character prompt…" />
      <textarea rows={2} className="style-neg-edit" value={neg}
                onChange={(e) => setNeg(e.target.value)}
                placeholder="global negative — appended to every negative prompt…" />
    </div>
  );
}

// M0c — inline postprocess stack on a selected base image: build an ordered chain of
// Clean/Refine/custom (i2i) + Restore (GFPGAN) steps; each is configured, then queued
// independently, and records its source → output (the chain). Live status reads the job
// (the persisted record lags one poll); the "add" gate opens once the tail step is done.
function PostprocPanel({ stack, jobs, busy, onAdd, onQueue, onRemove, onView }: {
  stack: PostprocStack | undefined;
  jobs: Record<string, Job>;
  busy: boolean;
  onAdd: (preset: PostprocStep["preset"], backend: string | undefined,
          params: Record<string, unknown>) => void;
  onQueue: (stepId: string) => void;
  onRemove: (stepId: string) => void;
  onView: (output: string) => void;     // open a step's result in the full-res lightbox
}) {
  const [preset, setPreset] = useState<PostprocStep["preset"]>("clean");
  const [backend, setBackend] = useState("zimage");
  const [strength, setStrength] = useState("");
  const [prompt, setPrompt] = useState("");
  const [neg, setNeg] = useState("");
  const [blend, setBlend] = useState("");
  const steps = stack?.steps ?? [];
  const liveStatus = (st: PostprocStep): string =>
    (st.job_id ? jobs[st.job_id]?.status : undefined) ?? st.status;
  const last = steps[steps.length - 1];
  const canAdd = !last || last.output != null || liveStatus(last) === "done";
  const isI2i = preset !== "restore";

  const submit = () => {
    const params: Record<string, unknown> = {};
    if (isI2i) {
      if (strength.trim()) params.strength = Number(strength);
      if (prompt.trim()) params.prompt = prompt.trim();
      if (neg.trim()) params.negative_prompt = neg.trim();
    } else if (blend.trim()) {
      params.blend = Number(blend);
    }
    onAdd(preset, isI2i ? backend : undefined, params);
    setStrength(""); setPrompt(""); setNeg(""); setBlend("");
  };

  return (
    <div className="postproc">
      <div className="rail-head">POSTPROCESS <span className="muted">stack</span></div>
      {steps.length === 0 && (
        <div className="muted">No steps — stack a Clean / Refine / Restore over this image.</div>
      )}
      <ol className="pp-steps">
        {steps.map((st, i) => {
          const status = liveStatus(st);
          const p = st.params ?? {};
          // List every set attribute (not just strength) so a step's full recipe is visible.
          const attrs = [
            st.backend,
            typeof p.strength === "number" ? `str ${p.strength}` : null,
            typeof p.blend === "number" ? `blend ${p.blend}` : null,
            p.model_name ? String(p.model_name) : null,
            p.prompt ? `“${String(p.prompt).slice(0, 28)}”` : null,
            p.negative_prompt ? `neg “${String(p.negative_prompt).slice(0, 18)}”` : null,
          ].filter(Boolean).join(" · ");
          return (
            <li key={st.id} className="pp-step">
              <div className="pp-step-head">
                <span className="pp-num">{i + 1}</span>
                <span className="pp-preset">{st.preset}</span>
                <span className={`pp-status pp-${status}`}>{status}</span>
                {status === "done" && st.output && (
                  <button className="ghost" onClick={() => onView(st.output!)}
                          title="view the result (full resolution)">🔍</button>
                )}
                {status === "configured" && (
                  <button className="ghost" disabled={busy} onClick={() => onQueue(st.id)}
                          title="queue this step (uses GPU)">▶</button>
                )}
                {i === steps.length - 1 && status !== "queued" && status !== "running" && (
                  <button className="ghost" disabled={busy} onClick={() => onRemove(st.id)}
                          title="remove this (tail) step">✕</button>
                )}
              </div>
              {attrs && <div className="pp-step-attrs">{attrs}</div>}
            </li>
          );
        })}
      </ol>
      {canAdd ? (
        <div className="pp-add">
          <div className="pp-add-row">
            <select value={preset}
                    onChange={(e) => setPreset(e.target.value as PostprocStep["preset"])}>
              <option value="clean">Clean (i2i 0.5)</option>
              <option value="refine">Refine (i2i 0.25)</option>
              <option value="restore">Restore (GFPGAN)</option>
            </select>
            {isI2i && (
              <select value={backend} onChange={(e) => setBackend(e.target.value)}
                      title="img2img backend family">
                <option value="zimage">zimage</option>
                <option value="sd35">sd35</option>
              </select>
            )}
          </div>
          {isI2i ? (
            <>
              <input className="prompt" type="number" step="0.05" min="0" max="1"
                     placeholder={`strength (default ${preset === "clean" ? "0.5" : "0.25"})`}
                     value={strength} onChange={(e) => setStrength(e.target.value)} />
              <input className="prompt" placeholder="prompt (optional — defaults to the image's own)"
                     value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              <input className="prompt" placeholder="negative prompt (optional)"
                     value={neg} onChange={(e) => setNeg(e.target.value)} />
            </>
          ) : (
            <input className="prompt" type="number" step="0.05" min="0" max="1"
                   placeholder="blend (default 0.8)" value={blend}
                   onChange={(e) => setBlend(e.target.value)} />
          )}
          <button className="proj-btn" disabled={busy} onClick={submit}>+ add step</button>
        </div>
      ) : (
        <div className="muted pp-gate">Queue + finish the last step to stack another.</div>
      )}
    </div>
  );
}

function Inspector({ job, output }: { job: Job; output?: string }) {
  const r = job.result;
  const p = job.params ?? {};
  const cov = job.coverage_cell;
  // A multi-candidate tile carries its own output — show THAT image + the candidate's
  // pipeline/seed (parsed from its …/ideate/<pipeline>/seed_<n>/… path), not the pool's
  // first output (review 2026-06-10 #2). clean/polish outputs fall back to job-level.
  const m = output ? /\/ideate\/([^/]+)\/seed_(\d+)\//.exec("/" + output) : null;
  const candPipeline = m ? m[1] : null;
  const candSeed = m ? Number(m[2]) : null;
  const pass = output ? (/\/clean\//.test("/" + output) ? "clean"
    : /\/polish\//.test("/" + output) ? "polish" : null) : null;
  // Batch jobs: per-output meta (cell + seed) echoed from the worker's batch manifest.
  const ometa = output ? r?.output_meta?.[output] : undefined;
  const cell = ometa?.coverage_cell ?? cov;
  const file = output ?? r?.output_name;
  const showPreview = !!file && (r?.ok === true || job.status === "running");
  return (
    <div className="insp">
      {showPreview && <img className="preview" src={outputUrl(file!)} alt={job.id} />}
      <dl>
        <dt>job</dt><dd>{job.id}</dd>
        <dt>status</dt>
        <dd>{job.status}{job.status === "running" ? ` · ${Math.round(job.progress * 100)}%` : ""}</dd>
        <dt>pipeline</dt>
        <dd>
          {candPipeline ?? job.pipeline}/{job.mode}
          {pass ? ` · ${pass} pass` : ""}
          {job.pass ? ` · ${job.pass}⤴${job.chained_from ? ` of ${job.chained_from}` : ""}` : ""}
          {job.stage ? ` · stage ${job.stage}` : ""}
        </dd>
        <dt>model</dt><dd>{String(p.model_name ?? "default")}</dd>
        <dt>seed</dt><dd>{candSeed ?? ometa?.seed ?? r?.seed ?? (p.seed as number | undefined) ?? "—"}</dd>
        <dt>wall</dt><dd>{job.wall_s != null ? `${job.wall_s}s` : "—"}</dd>
        <dt>duration</dt><dd>{r?.duration_s != null ? `${r.duration_s}s` : "—"}</dd>
        <dt>file</dt><dd className="mono">{file ?? "—"}</dd>
      </dl>
      {job.note && <div className="banner insp-note">{job.note}</div>}
      {cell && (
        <div className="muted insp-cov">
          cell: {cell.shot_size} · {cell.angle} · {cell.expression}
          {cell.background ? ` · ${cell.background}` : ""}
        </div>
      )}
      {ometa && (ometa.identity || ometa.restore || ometa.anchor_cos != null) && (
        <div className="muted insp-cov">
          {ometa.identity ? `identity: ${ometa.identity}` : ""}
          {ometa.anchor_cos != null ? ` (anchor cos ${ometa.anchor_cos})` : ""}
          {ometa.identity && ometa.restore ? " · " : ""}
          {ometa.restore ? `restore: ${ometa.restore}` : ""}
          {ometa.faces != null ? ` (${ometa.faces} face${ometa.faces === 1 ? "" : "s"})` : ""}
        </div>
      )}
      {(ometa?.prompt || typeof p.prompt === "string") && (
        <details className="insp-details">
          <summary>resolved prompt (as run)</summary>
          {/* Prefer the per-OUTPUT prompt for a Stage-B batch image (its real cell prompt:
              angle + shot-size + expression + clause + style). The job-level p.prompt is
              only the `[dataset …]` summary label, shared by every cell. */}
          <div className="insp-prompt">{ometa?.prompt ?? (p.prompt as string)}</div>
        </details>
      )}
      <details className="insp-details">
        <summary>params (as run)</summary>
        <pre className="mono insp-params">{JSON.stringify(p, null, 1)}</pre>
      </details>
      {r?.error && <div className="error">⚠ {r.error}</div>}
      {job.status === "running" && job.log_tail && (
        <pre className="logtail">{job.log_tail}</pre>
      )}
      {(job.status === "failed" || job.status === "canceled") && job.log_tail && (
        <pre className="stderr">{job.log_tail}</pre>
      )}
    </div>
  );
}

// Fallback multi tunables for before GET /models loads — the live drawer uses
// catalog.multi.params (the full batch surface: clean/polish toggles + sub-params).
const MULTI_PARAM_SPECS: ParamSpec[] = [
  { name: "width", type: "int", default: 1024, min: 256, max: 2048, step: 16, note: "divisible by 16" },
  { name: "height", type: "int", default: 1024, min: 256, max: 2048, step: 16, note: "divisible by 16" },
  { name: "seed", type: "int", note: "random if unset; candidates derive per-seed" },
];

/** Catalog-driven parameter controls (review 2026-06-10, issue 2): renders every
 * non-advanced tunable for the pipeline+mode from GET /models — int/float as bounded
 * numbers, enum as a select (with the model_name variants), flag as a checkbox, str as
 * text. Unset (empty) = the worker/model default; nothing is sent for it. */
function ParamControls({
  specs,
  mode,
  values,
  onChange,
  exclude = [],
}: {
  specs: ParamSpec[];
  mode: string;
  values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
  exclude?: string[];
}) {
  const visible = specs.filter(
    (s) => !s.advanced && s.type !== "image" && !exclude.includes(s.name)
      && (!s.modes || s.modes.includes(mode)),
  );
  if (visible.length === 0) return <span className="muted">no tunables for this pipeline/mode</span>;
  // Grouped blocks (user request 2026-06-11): base model/generation tunables first, then
  // one block per post-pass family (clean / polish / future postproc) — the flat mix made
  // the drawer unreadable once the post-pass params joined every pipeline.
  const isFamily = (s: ParamSpec, fam: string) =>
    s.name === fam || s.name.startsWith(`${fam}_`);
  const groups: Array<[string, ParamSpec[]]> = [
    ["model / generation", visible.filter((s) => !s.post)],
    ["clean pass", visible.filter((s) => !!s.post && isFamily(s, "clean"))],
    ["polish pass", visible.filter((s) => !!s.post && isFamily(s, "polish"))],
    ["other postproc", visible.filter(
      (s) => !!s.post && !isFamily(s, "clean") && !isFamily(s, "polish"))],
  ];
  return (
    <>
      {groups.filter(([, g]) => g.length > 0).map(([label, g]) => (
        <div key={label} className="p-group">
          <span className="p-group-label">{label}</span>
          {g.map((s) => renderParamControl(s, values[s.name], onChange))}
        </div>
      ))}
    </>
  );
}

function renderParamControl(
  s: ParamSpec,
  v: unknown,
  onChange: (name: string, value: unknown) => void,
) {
  const title = s.note ?? "";
        if (s.type === "flag") {
          return (
            <label key={s.name} className="p-flag" title={title}>
              <input
                type="checkbox"
                checked={v === true}
                onChange={(e) => onChange(s.name, e.target.checked ? true : undefined)}
              />
              {s.name}
            </label>
          );
        }
        if (s.type === "enum") {
          return (
            <label key={s.name} title={title}>
              {s.name}
              <select
                value={(v as string) ?? ""}
                onChange={(e) => onChange(s.name, e.target.value || undefined)}
              >
                <option value="">default</option>
                {(s.choices ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          );
        }
        if (s.type === "int" || s.type === "float") {
          return (
            <label key={s.name} className="p-num" title={title}>
              {s.name}
              <input
                type="number"
                min={s.min}
                max={s.max}
                step={s.step ?? (s.type === "float" ? 0.05 : 1)}
                value={v === undefined ? "" : (v as number)}
                placeholder={s.default != null ? String(s.default) : "auto"}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") return onChange(s.name, undefined);
                  const n = s.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
                  onChange(s.name, Number.isNaN(n) ? undefined : n);
                }}
              />
            </label>
          );
        }
        return (
          <label key={s.name} className="p-str" title={title}>
            {s.name}
            <input
              type="text"
              value={(v as string) ?? ""}
              onChange={(e) => onChange(s.name, e.target.value || undefined)}
            />
          </label>
        );
}

function clamp(n: number, lo: number, hi: number): number {
  if (Number.isNaN(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}
