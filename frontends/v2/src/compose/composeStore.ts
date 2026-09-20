// The Composer's own state (kb-loom-ui.md §3.3): what the author is about to generate.
// Persisted per machine so a half-written prompt or JSON tree survives a reload — losing
// authored text is the one thing a tool must never do. The catalog + styles are loaded here
// too, once, because every compose control reads them.
import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  emptyFlux2PromptTree, getModels, getStyles, serializeFlux2PromptTree,
  type Flux2PromptTree, type GenerateRequest, type ModelCatalog, type StylesInfo,
} from "@loom/shared/api/orchestrator";

export type Pipeline = "multi" | "zimage" | "sd35" | "flux2" | "krea2";
export const PIPELINES: { id: Pipeline; label: string; hint: string }[] = [
  { id: "multi", label: "Cast (multi)", hint: "one prompt → a pool of candidates across flux2, sd35 and zimage" },
  { id: "flux2", label: "FLUX.2", hint: "klein for speed, flux.2-dev for JSON prompting and reference-conditioned expansion" },
  { id: "sd35", label: "SD3.5", hint: "creative, fast to iterate, the postprocess workhorse" },
  { id: "zimage", label: "Z-Image", hint: "accurate prompting; turbo is quick, base is slow" },
  { id: "krea2", label: "Krea 2 Turbo", hint: "a fourth t2i look" },
];

/** Request fields the server takes at the top level; everything else rides `params`. */
export const TOP_LEVEL = new Set(["width", "height", "seed", "num_steps", "guidance_scale", "negative_prompt", "model_name"]);

interface ComposeState {
  pipeline: Pipeline;
  prompt: string;
  tree: Flux2PromptTree;
  treeOpen: boolean;
  count: number;
  candidates: number;
  ideation: "fast" | "refined";
  sampling: string;                       // flux2 sampling preset id ("" = custom)
  params: Record<string, unknown>;         // advanced tunables incl. model_name
  applyStyle: boolean;
  styleId: string;                         // "" = the project's active style
  advancedOpen: boolean;
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
  loadCatalog: () => Promise<void>;
  loadStyles: () => Promise<void>;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

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
      setParam: (name, value) => set((s) => {
        const params = { ...s.params };
        if (value === undefined || value === null || value === "") delete params[name]; else params[name] = value;
        // hand-editing a preset's fields makes it custom again
        const sampling = name === "model_name" || name === "num_steps" || name === "guidance" ? "" : s.sampling;
        return { params, sampling };
      }),
      resetParams: () => set({ params: {}, sampling: "" }),
      setApplyStyle: (applyStyle) => set({ applyStyle }),
      setStyleId: (styleId) => set({ styleId }),
      setAdvancedOpen: (advancedOpen) => set({ advancedOpen }),
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
      }),
    },
  ),
);

/** The effective model id: the params channel's `model_name`, else the catalog default. */
export function effectiveModel(s: Pick<ComposeState, "pipeline" | "params" | "catalog">): string | undefined {
  const fromParams = typeof s.params.model_name === "string" ? s.params.model_name : undefined;
  if (fromParams) return fromParams;
  const def = s.catalog?.[s.pipeline]?.params?.find((p) => p.name === "model_name")?.default;
  return typeof def === "string" ? def : undefined;
}

export function isDevSelected(s: Pick<ComposeState, "pipeline" | "params" | "catalog">): boolean {
  return s.pipeline === "flux2" && effectiveModel(s) === "flux.2-dev";
}

/** Assemble the request exactly the way v1 did (buildGenerateReq), so the server sees no change. */
export function buildRequest(
  s: Pick<ComposeState, "pipeline" | "prompt" | "tree" | "count" | "candidates" | "ideation" | "params" | "applyStyle" | "styleId" | "catalog">,
  scope: { asset_id: string } | null,
): { req: GenerateRequest } | { problem: string } {
  const dev = isDevSelected(s);
  const jsonPrompt = dev ? serializeFlux2PromptTree(s.tree) : "";
  const text = s.prompt.trim();
  if (!text && !jsonPrompt) return { problem: dev ? "Write a prompt or fill the JSON tree." : "Write a prompt." };
  const top: Record<string, unknown> = {};
  const channel: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(s.params)) {
    if (v === undefined || v === null || v === "") continue;
    (TOP_LEVEL.has(k) ? top : channel)[k] = v;
  }
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
