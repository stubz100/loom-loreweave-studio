// The Composer's own state (kb-loom-ui.md §3.3): what the author is about to generate, in
// three modes — Cast (t2i / multi), Expand (the Stage-B recipe) and Train (the staging form).
// Persisted per machine so a half-written prompt, tree or clause survives a reload — losing
// authored text is the one thing a tool must never do. The character clause is kept PER
// ASSET (v1 carried one clause across assets, so character B's sweep could be built with A's).
import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  emptyFlux2PromptTree, getModels, getStyles, serializeFlux2PromptTree,
  type Flux2PromptTree, type GenerateRequest, type ModelCatalog, type RecipePreset,
  type StageBRequest, type StylesInfo,
} from "@loom/shared/api/orchestrator";

export type Pipeline = "multi" | "zimage" | "sd35" | "flux2" | "krea2";
export const PIPELINES: { id: Pipeline; label: string; hint: string }[] = [
  { id: "multi", label: "Cast (multi)", hint: "one prompt → a pool of candidates across flux2, sd35 and zimage" },
  { id: "flux2", label: "FLUX.2", hint: "klein for speed, flux.2-dev for JSON prompting and reference-conditioned expansion" },
  { id: "sd35", label: "SD3.5", hint: "creative, fast to iterate, the postprocess workhorse" },
  { id: "zimage", label: "Z-Image", hint: "accurate prompting; turbo is quick, base is slow" },
  { id: "krea2", label: "Krea 2 Turbo", hint: "a fourth t2i look" },
];
export type ExpandPipeline = "zimage" | "sd35" | "flux2";

/** Request fields the server takes at the top level; everything else rides `params`. */
export const TOP_LEVEL = new Set(["width", "height", "seed", "num_steps", "guidance_scale", "negative_prompt", "model_name"]);

export interface ExpandState {
  preset: RecipePreset;
  cells: number[] | null;                  // null = the whole recipe
  pipeline: ExpandPipeline;
  params: Record<string, unknown>;         // incl. model_name for the variant
  sampling: string;
  strength: number;
  realize: "img2img" | "mixed";
  identity: boolean | null;                // null = auto (on when the anchor is verified)
  advancedPrompt: boolean;
  applyStyle: boolean;                     // expansion defaults the L1 style OFF (M2.10)
  styleId: string;
  sketch: { shot: string; angle: string; expression: string; motion: string; every: number; frames: number };
}

export interface TrainState {
  family: "zimage" | "sd35";
  init: "from_base" | "seed_parent";
  trigger: string;
  steps: string;
  rank: string;
  alpha: string;
  lr: string;
  res: string;
}

interface ComposeState {
  // cast
  pipeline: Pipeline;
  prompt: string;
  tree: Flux2PromptTree;
  treeOpen: boolean;
  count: number;
  candidates: number;
  ideation: "fast" | "refined";
  sampling: string;
  params: Record<string, unknown>;
  applyStyle: boolean;
  styleId: string;
  advancedOpen: boolean;
  // expand + train
  expand: ExpandState;
  clauses: Record<string, string>;         // asset id → character clause
  train: TrainState;
  // shared reference data
  catalog: ModelCatalog | null;
  styles: StylesInfo | null;
  catalogError: string | null;

  setPipeline: (p: Pipeline) => void;
  setPrompt: (v: string) => void;
  setTree: (t: Flux2PromptTree) => void;
  setTreeOpen: (v: boolean) => void;
  setCount: (n: number) => void;
  setCandidates: (n: number) => void;
  setIdeation: (m: "fast" | "refined") => void;
  setSampling: (id: string) => void;
  setParam: (name: string, value: unknown) => void;
  resetParams: () => void;
  setApplyStyle: (v: boolean) => void;
  setStyleId: (id: string) => void;
  setAdvancedOpen: (v: boolean) => void;
  patchExpand: (p: Partial<ExpandState>) => void;
  setExpandParam: (name: string, value: unknown) => void;
  setExpandSampling: (id: string) => void;
  setClause: (assetId: string, v: string) => void;
  patchTrain: (p: Partial<TrainState>) => void;
  loadCatalog: () => Promise<void>;
  loadStyles: () => Promise<void>;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

const DEFAULT_EXPAND: ExpandState = {
  preset: "full_coverage", cells: null, pipeline: "zimage", params: {}, sampling: "", strength: 0.55,
  realize: "img2img", identity: null, advancedPrompt: false, applyStyle: false, styleId: "",
  sketch: { shot: "waist_up", angle: "three_quarter_left", expression: "neutral", motion: "", every: 6, frames: 8 },
};
const DEFAULT_TRAIN: TrainState = { family: "zimage", init: "from_base", trigger: "", steps: "", rank: "", alpha: "", lr: "", res: "" };

function setParamIn(params: Record<string, unknown>, name: string, value: unknown): Record<string, unknown> {
  const next = { ...params };
  if (value === undefined || value === null || value === "") delete next[name]; else next[name] = value;
  return next;
}

export const useCompose = create<ComposeState>()(
  persist(
    (set, get) => ({
      pipeline: "flux2",
      prompt: "",
      tree: emptyFlux2PromptTree(),
      treeOpen: true,
      count: 1,
      candidates: 2,
      ideation: "fast",
      sampling: "",
      params: {},
      applyStyle: true,
      styleId: "",
      advancedOpen: false,
      expand: DEFAULT_EXPAND,
      clauses: {},
      train: DEFAULT_TRAIN,
      catalog: null,
      styles: null,
      catalogError: null,

      setPipeline: (pipeline) => set({ pipeline, params: {}, sampling: "" }),
      setPrompt: (prompt) => set({ prompt }),
      setTree: (tree) => set({ tree }),
      setTreeOpen: (treeOpen) => set({ treeOpen }),
      setCount: (n) => set({ count: clamp(Math.round(n) || 1, 1, 8) }),
      setCandidates: (n) => set({ candidates: clamp(Math.round(n) || 1, 1, 5) }),
      setIdeation: (ideation) => set({ ideation }),
      setSampling: (id) => {
        const preset = get().catalog?.flux2?.sampling_presets?.find((p) => p.id === id);
        set(preset
          ? { sampling: id, params: { ...get().params, model_name: preset.model_name, num_steps: preset.num_steps, guidance: preset.guidance } }
          : { sampling: "" });
      },
      setParam: (name, value) => set((s) => ({
        params: setParamIn(s.params, name, value),
        sampling: name === "model_name" || name === "num_steps" || name === "guidance" ? "" : s.sampling,
      })),
      resetParams: () => set({ params: {}, sampling: "" }),
      setApplyStyle: (applyStyle) => set({ applyStyle }),
      setStyleId: (styleId) => set({ styleId }),
      setAdvancedOpen: (advancedOpen) => set({ advancedOpen }),

      patchExpand: (p) => set((s) => ({ expand: { ...s.expand, ...p, ...(p.preset && p.preset !== s.expand.preset ? { cells: null } : {}), ...(p.pipeline && p.pipeline !== s.expand.pipeline ? { params: {}, sampling: "" } : {}) } })),
      setExpandParam: (name, value) => set((s) => ({ expand: {
        ...s.expand, params: setParamIn(s.expand.params, name, value),
        sampling: name === "model_name" || name === "num_steps" || name === "guidance" ? "" : s.expand.sampling,
      } })),
      setExpandSampling: (id) => {
        const preset = get().catalog?.flux2?.sampling_presets?.find((p) => p.id === id);
        set((s) => ({ expand: preset
          ? { ...s.expand, sampling: id, params: { ...s.expand.params, model_name: preset.model_name, num_steps: preset.num_steps, guidance: preset.guidance } }
          : { ...s.expand, sampling: "" } }));
      },
      setClause: (assetId, v) => set((s) => ({ clauses: { ...s.clauses, [assetId]: v } })),
      patchTrain: (p) => set((s) => ({ train: { ...s.train, ...p } })),

      loadCatalog: async () => {
        try { set({ catalog: await getModels(), catalogError: null }); }
        catch (e) { set({ catalogError: String(e) }); }
      },
      loadStyles: async () => {
        try { set({ styles: await getStyles() }); }
        catch { set({ styles: null }); }
      },
    }),
    {
      name: "loom.v2.compose",
      partialize: (s) => ({
        pipeline: s.pipeline, prompt: s.prompt, tree: s.tree, treeOpen: s.treeOpen, count: s.count,
        candidates: s.candidates, ideation: s.ideation, sampling: s.sampling, params: s.params,
        applyStyle: s.applyStyle, styleId: s.styleId, advancedOpen: s.advancedOpen,
        expand: s.expand, clauses: s.clauses, train: s.train,
      }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<ComposeState>;
        return { ...current, ...p, expand: { ...DEFAULT_EXPAND, ...(p.expand ?? {}), sketch: { ...DEFAULT_EXPAND.sketch, ...(p.expand?.sketch ?? {}) } }, train: { ...DEFAULT_TRAIN, ...(p.train ?? {}) } };
      },
    },
  ),
);

/** The effective model id: the params channel's `model_name`, else the catalog default. */
export function effectiveModel(s: { pipeline: string; params: Record<string, unknown>; catalog: ModelCatalog | null }): string | undefined {
  const fromParams = typeof s.params.model_name === "string" ? s.params.model_name : undefined;
  if (fromParams) return fromParams;
  const def = s.catalog?.[s.pipeline]?.params?.find((p) => p.name === "model_name")?.default;
  return typeof def === "string" ? def : undefined;
}

export function isDevSelected(s: { pipeline: string; params: Record<string, unknown>; catalog: ModelCatalog | null }): boolean {
  return s.pipeline === "flux2" && effectiveModel(s) === "flux.2-dev";
}

function splitTop(params: Record<string, unknown>) {
  const top: Record<string, unknown> = {};
  const channel: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    (TOP_LEVEL.has(k) ? top : channel)[k] = v;
  }
  return { top, channel };
}

/** Assemble the cast request exactly the way v1 did (buildGenerateReq), so the server sees no change. */
export function buildRequest(
  s: Pick<ComposeState, "pipeline" | "prompt" | "tree" | "count" | "candidates" | "ideation" | "params" | "applyStyle" | "styleId" | "catalog">,
  scope: { asset_id: string } | null,
): { req: GenerateRequest } | { problem: string } {
  const dev = isDevSelected(s);
  const jsonPrompt = dev ? serializeFlux2PromptTree(s.tree) : "";
  const text = s.prompt.trim();
  if (!text && !jsonPrompt) return { problem: dev ? "Write a prompt or fill the JSON tree." : "Write a prompt." };
  const { top, channel } = splitTop(s.params);
  const common = {
    ...(scope ? { asset_id: scope.asset_id, stage: "A" as const } : {}),
    apply_style: s.applyStyle,
    ...(s.styleId ? { style_id: s.styleId } : {}),
    ...(Object.keys(channel).length ? { params: channel } : {}),
  };
  if (s.pipeline === "multi") {
    return { req: {
      pipeline: "multi", prompt: text, num_candidates: s.candidates, ideation_mode: s.ideation, ...common,
      ...(top.width !== undefined ? { width: top.width as number } : {}),
      ...(top.height !== undefined ? { height: top.height as number } : {}),
      ...(top.seed !== undefined ? { seed: top.seed as number } : {}),
    } };
  }
  return { req: { pipeline: s.pipeline, prompt: jsonPrompt || text, count: s.count, ...common, ...top } };
}

/** Assemble the Stage-B body exactly the way v1 did (buildStageBBody). */
export function buildStageB(e: ExpandState, clause: string, bgMask: string | null): StageBRequest {
  const { top, channel } = splitTop(e.params);
  const model = typeof e.params.model_name === "string" ? e.params.model_name : undefined;
  const paramsOut = {
    ...(top.num_steps !== undefined ? { num_steps: top.num_steps } : {}),
    ...(top.guidance_scale !== undefined ? { guidance_scale: top.guidance_scale } : {}),
    ...(top.negative_prompt !== undefined ? { negative_prompt: top.negative_prompt } : {}),
    ...channel,
  };
  return {
    preset: e.preset,
    pipeline: e.pipeline,
    model_name: model || undefined,
    strength: e.strength,
    realize: e.realize,
    ...(e.realize === "mixed" ? { bg_mask: bgMask ?? undefined } : {}),
    ...(e.identity !== null ? { identity: e.identity } : {}),
    ...(e.pipeline === "flux2" && e.advancedPrompt ? { advanced_prompt: true } : {}),
    character_clause: clause.trim() || undefined,
    ...(e.cells !== null ? { cells: e.cells } : {}),
    apply_style: e.applyStyle,
    ...(e.applyStyle && e.styleId ? { style_id: e.styleId } : {}),
    ...(top.width !== undefined ? { width: top.width as number } : {}),
    ...(top.height !== undefined ? { height: top.height as number } : {}),
    ...(top.seed !== undefined ? { base_seed: top.seed as number } : {}),
    ...(Object.keys(paramsOut).length ? { params: paramsOut } : {}),
  };
}
