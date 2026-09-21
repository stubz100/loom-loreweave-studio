// v2 state — one store, two halves: LIVE state fed by the poller (health, project, jobs) and
// LAYOUT state the author owns (persisted per machine, kb-loom-ui.md §4 "Layout memory").
// Selection and UI toggles live here too so every zone reads the same truth.
import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  addPostprocStep, getAsset, getBible, getPostprocStacks, getStagedTraining, queuePostprocStep, removePostprocStep,
  type AssetDetail, type BibleInfo, type DiskStatus, type Health, type Job, type JobStatus, type JobsResponse,
  type PauseReason, type PostprocStack, type PostprocStep, type ProjectInfo, type StagedTraining,
} from "@loom/shared/api/orchestrator";

export type Workspace = "world" | "assets" | "shots" | "flow" | "episode";
export type Stage = "cast" | "expand" | "curate" | "train";
export type View = "flat" | "grouped" | "captions" | "loupe" | "edit";
export type PanelTab = "library" | "compose" | "train";
export type InspectorTab = "info" | "post" | "readiness" | "version" | "muse";
export type NoticeKind = "info" | "ok" | "warn" | "err";
export type DockFilter = "active" | "training" | "recent";
export type WorldTab = "styles" | "world" | "spine" | "poses";

export interface Notice { id: number; kind: NoticeKind; text: string; at: number }
/** A tile: a job output, a job placeholder (no output yet), or a durable curated ref. */
export interface Selection { jobId?: string; output?: string; refId?: string }
export interface Filters { shot: string; angle: string; expression: string; showRejected: boolean }
export type Dialog = "new" | "open" | null;

/** The stage letters the backend records on jobs (A/B/C/D) ↔ the verbs the strip shows. */
export const STAGE_LETTER: Record<Stage, "A" | "B" | "C" | "D"> = {
  cast: "A", expand: "B", curate: "C", train: "D",
};

interface LayoutSlice {
  workspace: Workspace;
  stage: Stage;
  view: View;
  panelOpen: boolean;
  panelWidth: number;
  panelTab: PanelTab;
  inspectorOpen: boolean;
  inspectorWidth: number;
  inspectorTab: InspectorTab;
  dockOpen: boolean;
  dockHeight: number;
  dockFilter: DockFilter;
  worldTab: WorldTab;
  poseSet: string;                       // the recipe whose pose icons the World shows
  zoom: number;
  fit: "fit" | "fill";
}

interface LiveSlice {
  health: Health | null;
  offline: boolean;
  lastError: string | null;
  project: ProjectInfo | null;
  jobs: Record<string, Job>;
  counts: Record<JobStatus, number> | null;
  paused: boolean;
  pauseReason: PauseReason;
  disk: DiskStatus | null;
  notices: Notice[];
  selectedAsset: string | null;
  assetDetail: AssetDetail | null;
  stacks: PostprocStack[];               // project-level postprocess stacks (any image)
  selection: Selection | null;
  compare: Selection | null;             // the loupe's pinned image
  bulk: string[];                        // tile keys marked for a bulk action
  filters: Filters;                      // the Curate filters
  pendingDelete: string | null;          // a tile key (or "__bulk__") awaiting its second click
  staged: StagedTraining[];              // staged (not queued) training runs, project-wide
  masks: Record<string, string[]>;       // image → masks painted for it this session (newest last)
  bible: BibleInfo | null;               // the L1 record: world prose, spine
  styleSel: string | null;               // the style the World editor shows (null = the project default)
  previewJobId: string | null;           // a done trainer job whose preview form is open in the Train tab
  viewBeforeLoupe: View;
  helpOpen: boolean;
  menuOpen: boolean;
  dialog: Dialog;
}

interface Actions {
  setWorkspace: (w: Workspace) => void;
  setStage: (s: Stage) => void;
  setView: (v: View) => void;
  togglePanel: (tab?: PanelTab) => void;
  setPanelWidth: (w: number) => void;
  toggleInspector: (tab?: InspectorTab) => void;
  setInspectorWidth: (w: number) => void;
  toggleDock: () => void;
  openDock: (filter?: DockFilter) => void;
  setDockFilter: (f: DockFilter) => void;
  setWorldTab: (t: WorldTab) => void;
  setPoseSet: (p: string) => void;
  setStyleSel: (id: string | null) => void;
  refreshBible: () => Promise<void>;
  setDockHeight: (h: number) => void;
  setZoom: (z: number) => void;
  setFit: (f: "fit" | "fill") => void;
  hideAllPanels: () => void;
  setOnline: (h: Health) => void;
  setOffline: (err: string) => void;
  setProject: (p: ProjectInfo | null) => void;
  applyJobs: (r: JobsResponse) => void;
  notify: (kind: NoticeKind, text: string) => void;
  dismiss: (id: number) => void;
  selectAsset: (id: string | null) => void;
  refreshAsset: () => Promise<void>;
  refreshStacks: () => Promise<void>;
  addStep: (body: { base: string; preset?: PostprocStep["preset"]; backend?: string; params?: Record<string, unknown>; source?: string; mask?: string; requires_mask?: boolean }) => Promise<void>;
  queueStep: (stepId: string, requesterId?: string, stage?: string) => Promise<void>;
  removeStep: (stepId: string) => Promise<void>;
  refreshStaged: () => Promise<void>;
  setPreviewJob: (id: string | null) => void;
  select: (s: Selection | null) => void;
  setCompare: (s: Selection | null) => void;
  toggleBulk: (key: string) => void;
  setBulk: (keys: string[]) => void;
  setFilters: (p: Partial<Filters>) => void;
  setPendingDelete: (key: string | null) => void;
  openLoupe: () => void;
  closeLoupe: () => void;
  openEdit: () => void;
  closeEdit: () => void;
  addMask: (image: string, mask: string) => void;
  setHelpOpen: (open: boolean) => void;
  setMenuOpen: (open: boolean) => void;
  setDialog: (d: Dialog) => void;
}

export type AppState = LayoutSlice & LiveSlice & Actions;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
let noticeSeq = 1;

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      // layout (persisted)
      workspace: "assets",
      stage: "cast",
      view: "flat",
      panelOpen: true,
      panelWidth: 320,
      panelTab: "library",
      inspectorOpen: true,
      inspectorWidth: 340,
      inspectorTab: "info",
      dockOpen: false,
      dockHeight: 220,
      dockFilter: "active",
      worldTab: "styles",
      poseSet: "full_coverage",
      zoom: 180,
      fit: "fit",
      // live
      health: null,
      offline: true,
      lastError: null,
      project: null,
      jobs: {},
      counts: null,
      paused: false,
      pauseReason: null,
      disk: null,
      notices: [],
      selectedAsset: null,
      assetDetail: null,
      stacks: [],
      selection: null,
      compare: null,
      bulk: [],
      filters: { shot: "", angle: "", expression: "", showRejected: false },
      pendingDelete: null,
      viewBeforeLoupe: "flat",
      staged: [],
      masks: {},
      bible: null,
      styleSel: null,
      previewJobId: null,
      helpOpen: false,
      menuOpen: false,
      dialog: null,

      setWorkspace: (workspace) => set({ workspace, selection: null }),
      setStage: (stage) => set((s) => ({
        stage, selection: null, compare: null, bulk: [], pendingDelete: null,
        view: s.view === "loupe" || s.view === "edit" || (s.view === "captions" && stage !== "train") ? "flat" : s.view,
      })),
      setView: (view) => { if (view === "loupe") get().openLoupe(); else if (view === "edit") get().openEdit(); else set({ view, compare: null }); },
      togglePanel: (tab) => set((s) => {
        if (tab && tab !== s.panelTab) return { panelTab: tab, panelOpen: true };
        return { panelOpen: !s.panelOpen };
      }),
      setPanelWidth: (w) => set({ panelWidth: clamp(w, 260, 420) }),
      toggleInspector: (tab) => set((s) => {
        if (tab && tab !== s.inspectorTab) return { inspectorTab: tab, inspectorOpen: true };
        return { inspectorOpen: !s.inspectorOpen };
      }),
      setInspectorWidth: (w) => set({ inspectorWidth: clamp(w, 280, 480) }),
      toggleDock: () => set((s) => ({ dockOpen: !s.dockOpen })),
      openDock: (filter) => set((s) => ({ dockOpen: true, dockFilter: filter ?? s.dockFilter })),
      setDockFilter: (dockFilter) => set({ dockFilter }),
      setWorldTab: (worldTab) => set({ worldTab }),
      setPoseSet: (poseSet) => set({ poseSet }),
      setStyleSel: (styleSel) => set({ styleSel }),
      refreshBible: async () => {
        if (!get().project) { if (get().bible) set({ bible: null }); return; }
        try {
          const bible = await getBible();
          if (JSON.stringify(bible) !== JSON.stringify(get().bible)) set({ bible });
        } catch { /* refetched on the next action */ }
      },
      setDockHeight: (h) => set({ dockHeight: clamp(h, 120, 480) }),
      setZoom: (z) => set({ zoom: clamp(z, 120, 360) }),
      setFit: (fit) => set({ fit }),
      hideAllPanels: () => set((s) => {
        const anyOpen = s.panelOpen || s.inspectorOpen;
        return { panelOpen: !anyOpen, inspectorOpen: !anyOpen };
      }),

      setOnline: (health) => set((s) => (s.offline
        ? { health, offline: false, lastError: null }
        : { health })),
      setOffline: (err) => set({ health: null, offline: true, lastError: err }),
      setProject: (project) => set((s) => {
        // a project change resets what depends on it
        const changed = (project?.id ?? null) !== (s.project?.id ?? null);
        return changed ? { project, selectedAsset: null, assetDetail: null, stacks: [], staged: [], previewJobId: null, selection: null, compare: null, bulk: [], pendingDelete: null, bible: null, styleSel: null } : { project };
      }),
      applyJobs: (r) => set({
        jobs: r.jobs, counts: r.counts, paused: r.paused,
        pauseReason: r.pause_reason ?? null, disk: r.disk ?? get().disk,
      }),
      notify: (kind, text) => set((s) => ({
        notices: [...s.notices.slice(-19), { id: noticeSeq++, kind, text, at: Date.now() }],
      })),
      dismiss: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
      selectAsset: (selectedAsset) => {
        set((s) => ({ selectedAsset, selection: null, compare: null, bulk: [], pendingDelete: null, assetDetail: null, previewJobId: null, view: s.view === "loupe" || s.view === "edit" || s.view === "captions" ? "flat" : s.view }));
        void get().refreshAsset();
      },
      refreshAsset: async () => {
        const id = get().selectedAsset;
        if (!id) { set({ assetDetail: null }); return; }
        try {
          const d = await getAsset(id);
          if (get().selectedAsset !== id) return;
          if (JSON.stringify(d) !== JSON.stringify(get().assetDetail)) set({ assetDetail: d });
        } catch { /* the poller retries on its next tick */ }
      },
      refreshStacks: async () => {
        if (!get().project) { if (get().stacks.length) set({ stacks: [] }); return; }
        try {
          const stacks = await getPostprocStacks();
          if (JSON.stringify(stacks) !== JSON.stringify(get().stacks)) set({ stacks });
        } catch { /* the poller retries on its next tick */ }
      },
      addStep: async (body) => set({ stacks: await addPostprocStep(body) }),
      queueStep: async (stepId, requesterId, stage) => set({ stacks: await queuePostprocStep(stepId, requesterId, stage) }),
      removeStep: async (stepId) => set({ stacks: await removePostprocStep(stepId) }),
      refreshStaged: async () => {
        if (!get().project) { if (get().staged.length) set({ staged: [] }); return; }
        try {
          const staged = (await getStagedTraining()).staged;
          if (JSON.stringify(staged) !== JSON.stringify(get().staged)) set({ staged });
        } catch { /* refetched on the next action */ }
      },
      setPreviewJob: (previewJobId) => set({ previewJobId }),
      select: (selection) => set({ selection, pendingDelete: null }),
      setCompare: (compare) => set({ compare }),
      toggleBulk: (key) => set((s) => ({ bulk: s.bulk.includes(key) ? s.bulk.filter((k) => k !== key) : [...s.bulk, key] })),
      setBulk: (bulk) => set({ bulk, pendingDelete: null }),
      setFilters: (p) => set((s) => ({ filters: { ...s.filters, ...p } })),
      setPendingDelete: (pendingDelete) => set({ pendingDelete }),
      openLoupe: () => set((s) => (s.selection && s.view !== "loupe" ? { view: "loupe", viewBeforeLoupe: s.view } : {})),
      closeLoupe: () => set((s) => (s.view === "loupe" ? { view: s.viewBeforeLoupe === "loupe" || s.viewBeforeLoupe === "edit" ? "flat" : s.viewBeforeLoupe, compare: null } : {})),
      openEdit: () => set((s) => (s.selection && s.view !== "edit" ? { view: "edit", viewBeforeLoupe: s.view === "loupe" ? s.viewBeforeLoupe : s.view, compare: null } : {})),
      closeEdit: () => set((s) => (s.view === "edit" ? { view: s.viewBeforeLoupe === "loupe" || s.viewBeforeLoupe === "edit" ? "flat" : s.viewBeforeLoupe } : {})),
      addMask: (image, mask) => set((s) => ({ masks: { ...s.masks, [image]: [...(s.masks[image] ?? []).filter((m) => m !== mask), mask] } })),
      setHelpOpen: (helpOpen) => set({ helpOpen }),
      setMenuOpen: (menuOpen) => set({ menuOpen }),
      setDialog: (dialog) => set({ dialog, menuOpen: false }),
    }),
    {
      name: "loom.v2.layout",
      partialize: (s) => ({
        workspace: s.workspace, stage: s.stage, view: s.view,
        panelOpen: s.panelOpen, panelWidth: s.panelWidth, panelTab: s.panelTab,
        inspectorOpen: s.inspectorOpen, inspectorWidth: s.inspectorWidth, inspectorTab: s.inspectorTab,
        dockOpen: s.dockOpen, dockHeight: s.dockHeight, dockFilter: s.dockFilter, worldTab: s.worldTab, poseSet: s.poseSet, zoom: s.zoom, fit: s.fit,
      }),
    },
  ),
);

/** The one running job, if any (the dock headline + progress line read it). */
export function runningJob(jobs: Record<string, Job>): Job | undefined {
  return Object.values(jobs).find((j) => j.status === "running");
}

/** Queued + running, oldest first — what the expanded dock lists. */
export function pendingJobs(jobs: Record<string, Job>): Job[] {
  return Object.values(jobs)
    .filter((j) => j.status === "queued" || j.status === "running")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}
