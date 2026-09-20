// Inspector · Post (kb-loom-ui.md §3.5, migration step 4): the selected image's postprocess
// stack drawn as a TREE — the base at the root, each step under the image it reads, branches
// indented, tombstones struck through — and the add form laid out for a column: preset,
// backend, source (branch point), model, strength with the effective-steps readout, prompt or
// the flux.2-dev JSON tree, negative, style, the output-size row. v1 squeezed all of this into
// a 240 px wrapping row.
import { useEffect, useState } from "react";

import {
  emptyFlux2PromptTree, serializeFlux2PromptTree, type Flux2PromptTree, type Job, type PostprocStack,
  type PostprocStep,
} from "@loom/shared/api/orchestrator";

import { Flux2JsonTree } from "../compose/Flux2JsonTree";
import { useCompose } from "../compose/composeStore";
import { reasonOf } from "../lib/project";
import { useApp } from "../store";

// Mirrors MIN_EFFECTIVE_I2I_STEPS / I2I_EXACT_BACKENDS in orchestrator/model_catalog.py (the
// v1 contract test pins them there); an i2i pass walks only strength × num_steps.
const MIN_EFFECTIVE_I2I_STEPS = 4;
const I2I_EXACT_BACKENDS = new Set(["flux2"]);

type Preset = PostprocStep["preset"];
const PRESETS: { id: Preset; label: string; hint: string }[] = [
  { id: "clean", label: "Clean (img2img 0.5)", hint: "re-diffuse at half strength: fixes hands, faces, artefacts" },
  { id: "refine", label: "Refine (img2img 0.25)", hint: "a light polish that keeps the composition" },
  { id: "stylelock", label: "StyleLock (img2img 0.3)", hint: "re-render toward the L1 style: pushes a drifted flux2 cell back to the source look" },
  { id: "upscale", label: "Scale (SD3.5 tile)", hint: "tile-ControlNet re-render at a new size; structure-preserving" },
  { id: "resize", label: "Resize (Lanczos)", hint: "pure resample, no model: the way to downscale without a re-render" },
  { id: "restore", label: "Restore (GFPGAN)", hint: "face restoration blended over the source" },
];

/** Live status: the persisted step status lags the queue; a vanished job means canceled. */
export function liveStatus(st: PostprocStep, jobs: Record<string, Job>): string {
  if (st.deleted) return "deleted";
  if (!st.job_id) return st.status;
  const live = jobs[st.job_id]?.status;
  if (live) return live;
  return st.status === "queued" || st.status === "running" ? "canceled" : st.status;
}

/** The stack that holds `image` as its base or as one of its step outputs. */
export function stackOf(stacks: PostprocStack[], image: string): PostprocStack | undefined {
  return stacks.find((s) => s.base === image) ?? stacks.find((s) => s.steps.some((st) => st.output === image));
}

interface Node { step: PostprocStep; depth: number }
/** Depth-first order of the tree: a step hangs under the image it reads. */
function treeOrder(stack: PostprocStack): Node[] {
  const out: Node[] = [];
  const visit = (parentImage: string, depth: number, seen: Set<string>) => {
    for (const st of stack.steps) {
      if (st.source !== parentImage || seen.has(st.id)) continue;
      seen.add(st.id);
      out.push({ step: st, depth });
      if (st.output) visit(st.output, depth + 1, seen);
    }
  };
  const seen = new Set<string>();
  visit(stack.base, 0, seen);
  for (const st of stack.steps) if (!seen.has(st.id)) { seen.add(st.id); out.push({ step: st, depth: 0 }); }   // orphans (source gone)
  return out;
}

function attrsOf(st: PostprocStep): string {
  const p = st.params ?? {};
  return [
    st.backend,
    typeof p.strength === "number" ? `strength ${p.strength}` : null,
    typeof p.blend === "number" ? `blend ${p.blend}` : null,
    typeof p.scale === "number" ? `×${p.scale}` : null,
    typeof p.width === "number" && typeof p.height === "number" ? `${p.width}×${p.height}` : null,
    p.model_name ? String(p.model_name) : null,
    p.prompt ? `“${String(p.prompt).slice(0, 40)}${String(p.prompt).length > 40 ? "…" : ""}”` : null,
  ].filter(Boolean).join(", ");
}

export function PostTab({ image }: { image: string }) {
  const stacks = useApp((s) => s.stacks);
  const jobs = useApp((s) => s.jobs);
  const offline = useApp((s) => s.offline);
  const selectedAsset = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const stage = useApp((s) => s.stage);
  const select = useApp((s) => s.select);
  const notify = useApp((s) => s.notify);
  const addStep = useApp((s) => s.addStep);
  const queueStep = useApp((s) => s.queueStep);
  const removeStep = useApp((s) => s.removeStep);
  const catalog = useCompose((s) => s.catalog);
  const styles = useCompose((s) => s.styles);
  const loadCatalog = useCompose((s) => s.loadCatalog);
  const loadStyles = useCompose((s) => s.loadStyles);
  const projectId = useApp((s) => s.project?.id ?? null);
  // The Composer loads these on its own mount; the Post tab may open first.
  useEffect(() => { if (!offline && !catalog) void loadCatalog(); }, [offline, catalog, loadCatalog]);
  useEffect(() => { if (projectId && !styles) void loadStyles(); }, [projectId, styles, loadStyles]);

  const stack = stackOf(stacks, image);
  const base = stack?.base ?? image;
  const nodes = stack ? treeOrder(stack) : [];
  const selectedNode = stack?.steps.find((st) => st.output === image) ?? null;

  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  // the add form
  const [preset, setPreset] = useState<Preset>("clean");
  const [backend, setBackend] = useState("zimage");
  const [source, setSource] = useState<string>("");          // "" = the selected image (or continue the chain)
  const [restyle, setRestyle] = useState(false);
  const [styleId, setStyleId] = useState("");
  const [model, setModel] = useState("");
  const [strength, setStrength] = useState("");
  const [prompt, setPrompt] = useState("");
  const [neg, setNeg] = useState("");
  const [blend, setBlend] = useState("");
  const [scale, setScale] = useState("");
  const [outW, setOutW] = useState("");
  const [outH, setOutH] = useState("");
  const [cnScale, setCnScale] = useState("");
  const [tree, setTree] = useState<Flux2PromptTree>(emptyFlux2PromptTree);

  const isI2i = preset === "clean" || preset === "refine" || preset === "stylelock";
  const isUpscale = preset === "upscale";
  const isResize = preset === "resize";
  const isFlux2 = isI2i && backend === "flux2";
  const devJson = isFlux2 && model === "flux.2-dev";
  const sizeable = (isI2i && !isFlux2) || isUpscale || isResize;
  const variants = catalog?.[backend]?.variants ?? [];
  const defaultStrength = preset === "clean" ? 0.5 : preset === "stylelock" ? 0.3 : 0.25;

  const budget = (() => {
    if (!isI2i) return null;
    const st = Number(strength) || defaultStrength;
    const v = variants.find((x) => x.id === model) ?? variants[0];
    const steps = v?.defaults?.num_steps;
    if (!steps || st <= 0) return null;
    if (I2I_EXACT_BACKENDS.has(backend)) {
      return steps >= MIN_EFFECTIVE_I2I_STEPS ? { request: null, effective: steps } : { request: MIN_EFFECTIVE_I2I_STEPS, effective: MIN_EFFECTIVE_I2I_STEPS };
    }
    const plain = Math.floor(steps * st);
    if (plain >= MIN_EFFECTIVE_I2I_STEPS) return { request: null, effective: plain };
    const request = Math.min(60, Math.ceil(MIN_EFFECTIVE_I2I_STEPS / st));
    return { request, effective: Math.floor(request * st) };
  })();

  // Where the new step reads from: the selected image when it is a finished step output (a
  // branch off it), else the author's pick, else the chain's newest finished output (server).
  const effectiveSource = source || (selectedNode?.output && !selectedNode.deleted ? selectedNode.output : "");
  const branchable = stack ? [stack.base, ...stack.steps.filter((st) => st.output && !st.deleted && liveStatus(st, jobs) === "done").map((st) => st.output!)] : [];

  const onPickPreset = (p: Preset) => {
    setPreset(p);
    if (p === "stylelock" && backend === "flux2") setBackend("sd35");   // flux2 is the drift source (422 server-side)
    setScale(p === "resize" ? "0.5" : "");
  };
  const reset = () => {
    setModel(""); setStrength(""); setPrompt(""); setNeg(""); setBlend(""); setScale(""); setOutW(""); setOutH("");
    setCnScale(""); setStyleId(""); setSource(""); setRestyle(false); setTree(emptyFlux2PromptTree());
  };
  const onAdd = async () => {
    const params: Record<string, unknown> = {};
    if (isI2i) {
      if (model.trim()) params.model_name = model;
      if (strength.trim()) params.strength = Number(strength);
      if (devJson) { const json = serializeFlux2PromptTree(tree); if (json) params.prompt = json; }
      else if (prompt.trim()) params.prompt = prompt.trim();
      if (!isFlux2 && neg.trim()) params.negative_prompt = neg.trim();
      if (preset === "stylelock" && styleId) params.style_id = styleId;
      if (restyle) { params.apply_style = true; if (styleId) params.style_id = styleId; }
    } else if (isUpscale) {
      if (cnScale.trim()) params.cn_scale = cnScale.trim();
      if (prompt.trim()) params.prompt = prompt.trim();
    } else if (preset === "restore" && blend.trim()) {
      params.blend = Number(blend);
    }
    if (sizeable) {
      if (outW.trim() && outH.trim()) { params.width = Number(outW); params.height = Number(outH); }
      else if (scale.trim() && Number(scale) !== 1) params.scale = Number(scale);
    }
    setBusy("add"); setProblem(null);
    try {
      await addStep({ base, preset, backend: isI2i ? backend : undefined, params, source: effectiveSource || undefined });
      reset();
    } catch (e) { setProblem(reasonOf(e)); } finally { setBusy(null); }
  };
  const onQueue = async (st: PostprocStep) => {
    setBusy(st.id); setProblem(null);
    try {
      const requester = selectedAsset ? detail?.profile.active_version : undefined;
      const letter = selectedAsset ? (stage === "cast" ? "A" : stage === "train" ? "D" : "B") : undefined;
      await queueStep(st.id, requester, letter);
      notify("ok", `Queued the ${st.preset} step. Its tile lands in ${selectedAsset ? "this stage" : "the Sandbox"} when done.`);
    } catch (e) { setProblem(reasonOf(e)); } finally { setBusy(null); }
  };
  const onRemove = async (st: PostprocStep) => {
    if (st.output && confirmId !== st.id) { setConfirmId(st.id); return; }
    setConfirmId(null);
    setBusy(st.id); setProblem(null);
    try {
      await removeStep(st.id);
      if (st.output === image) select(null);     // the selected image is gone with its step
    } catch (e) { setProblem(reasonOf(e)); } finally { setBusy(null); }
  };
  const onView = (st: PostprocStep) => {
    if (st.job_id && st.output && jobs[st.job_id]) select({ jobId: st.job_id, output: st.output });
  };

  const tailIds = new Set(stack ? stack.steps.filter((st) => !stack.steps.some((o) => o.source === st.output)).map((st) => st.id) : []);

  return (
    <>
      <div className="section-title">Stack</div>
      <p className="faint">Every pass reads one image and writes one. Branch from any finished image; the tree keeps the lineage.</p>
      <ul className="pp-tree">
        <li className={`pp-node${base === image ? " selected" : ""}`} style={{ ["--depth" as string]: 0 }}>
          <span className="pp-name">base</span>
          <span className="pp-attrs mono">{base.split("/").pop()}</span>
        </li>
        {nodes.map(({ step: st, depth }) => {
          const status = liveStatus(st, jobs);
          const dead = status === "failed" || status === "canceled";
          const isTail = tailIds.has(st.id);
          return (
            <li key={st.id} className={`pp-node${st.output === image ? " selected" : ""}${st.deleted ? " tomb" : ""}`} style={{ ["--depth" as string]: depth + 1 }}>
              <span className="pp-name">{st.preset}</span>
              <span className={`pp-status s-${status}`}>{status}</span>
              <span className="pp-attrs">{attrsOf(st)}</span>
              <span className="pp-actions">
                {status === "done" && st.output && !st.deleted && st.job_id && jobs[st.job_id] && (
                  <button onClick={() => onView(st)} title="select this result">View</button>
                )}
                {(status === "configured" || dead) && (
                  <button className="primary" onClick={() => void onQueue(st)} disabled={busy !== null || offline}
                          title={status === "configured" ? "queue this step (GPU)" : "queue it again: its job ended"}>
                    {status === "configured" ? "Queue" : "Retry"}
                  </button>
                )}
                {isTail && !st.deleted && status !== "queued" && status !== "running" && (
                  <button onClick={() => void onRemove(st)} onBlur={() => setConfirmId(null)} disabled={busy !== null}
                          title={st.output ? "removes the step and deletes its image; anything built from it is kept" : "remove this step"}>
                    {confirmId === st.id ? "Delete image?" : "Remove"}
                  </button>
                )}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="section-title">Add a pass</div>
      <label className="p-field">Preset
        <select value={preset} onChange={(e) => onPickPreset(e.target.value as Preset)}>
          {PRESETS.map((p) => <option key={p.id} value={p.id} title={p.hint}>{p.label}</option>)}
        </select>
      </label>
      <p className="faint">{PRESETS.find((p) => p.id === preset)?.hint}</p>
      {isI2i && (
        <label className="p-field">Backend
          <select value={backend} onChange={(e) => { setBackend(e.target.value); setModel(""); }}>
            <option value="zimage">zimage</option>
            <option value="sd35">sd35</option>
            {preset !== "stylelock" && <option value="flux2">flux2 (JSON prompting on flux.2-dev)</option>}
          </select>
        </label>
      )}
      {stack && stack.steps.length > 0 && (
        <label className="p-field">Reads
          <select value={effectiveSource} onChange={(e) => setSource(e.target.value)}>
            {!selectedNode && <option value="">the chain's newest finished image</option>}
            {branchable.map((img) => {
              const st = stack.steps.find((x) => x.output === img);
              return <option key={img} value={img}>{img === stack.base ? "the base image" : `the ${st?.preset ?? "step"} result ${img === image ? "(selected)" : ""}`}</option>;
            })}
          </select>
        </label>
      )}
      {isI2i && (
        <>
          <label className="p-field">Model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">{backend} default</option>
              {variants.map((v) => <option key={v.id} value={v.id} title={v.note}>{v.id}</option>)}
            </select>
          </label>
          <label className="p-field">Strength
            <input type="number" step={0.05} min={0} max={1} placeholder={`default ${defaultStrength}`} value={strength} onChange={(e) => setStrength(e.target.value)} />
          </label>
          {budget && (
            <p className="faint" title="num_steps is the full schedule; an img2img pass walks only strength × that">
              About {budget.effective} effective step{budget.effective === 1 ? "" : "s"}{budget.request ? ` (loom asks for ${budget.request} so at least ${MIN_EFFECTIVE_I2I_STEPS} run)` : ""}.
            </p>
          )}
          {devJson ? (
            <Flux2JsonTree value={tree} onChange={setTree} angleDirectives={catalog?.flux2?.angle_directives ?? {}} />
          ) : (
            <label className="p-field">Prompt
              <textarea rows={2} placeholder="optional: defaults to the image's own" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            </label>
          )}
          {isFlux2 && !devJson && <p className="faint">Pick flux.2-dev for the JSON prompt tree.</p>}
          {!isFlux2 && (
            <label className="p-field">Negative prompt
              <input type="text" placeholder="optional" value={neg} onChange={(e) => setNeg(e.target.value)} />
            </label>
          )}
          {preset !== "stylelock" && (
            <label className="p-flag" title="apply an L1 style and strip the one the source was made under; off = the source keeps its baked-in style">
              <input type="checkbox" checked={restyle} onChange={(e) => setRestyle(e.target.checked)} />Restyle this pass
            </label>
          )}
          {(preset === "stylelock" || restyle) && (
            <label className="p-field">Style
              <select value={styleId} onChange={(e) => setStyleId(e.target.value)}>
                <option value="">the active style</option>
                {styles?.styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          )}
        </>
      )}
      {isUpscale && (
        <>
          <p className="faint">SD3.5 Tile ControlNet on sd3.5-medium: re-renders detail at the target size.</p>
          <label className="p-field">ControlNet scale<input type="number" step={0.05} min={0} max={2} placeholder="default 0.6, lower = more creative" value={cnScale} onChange={(e) => setCnScale(e.target.value)} /></label>
          <label className="p-field">Prompt<input type="text" placeholder="optional: defaults to the image's own" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
        </>
      )}
      {preset === "restore" && (
        <label className="p-field">Blend<input type="number" step={0.05} min={0} max={1} placeholder="default 0.8" value={blend} onChange={(e) => setBlend(e.target.value)} /></label>
      )}
      {sizeable && (
        <div className="p-field">Output size
          <div className="jt-row">
            <select value={scale} onChange={(e) => setScale(e.target.value)} disabled={outW.trim() !== "" || outH.trim() !== ""}>
              <option value="0.5">×0.5</option>
              <option value="0.75">×0.75</option>
              {!isResize && <option value="">source size</option>}
              <option value="1.5">×1.5</option>
              <option value="2">×2</option>
              <option value="4">×4</option>
            </select>
            <input type="number" step={16} min={256} max={2048} placeholder="W" value={outW} onChange={(e) => setOutW(e.target.value)} />
            <span className="muted">×</span>
            <input type="number" step={16} min={256} max={2048} placeholder="H" value={outH} onChange={(e) => setOutH(e.target.value)} />
          </div>
          <span className="faint">A factor over the source, or an explicit width × height (both, multiples of 16).</span>
        </div>
      )}
      {problem && <p className="form-error" role="alert">{problem}</p>}
      <div className="jt-row">
        <span className="spacer" />
        <button className="primary" onClick={() => void onAdd()} disabled={busy !== null || offline}>{busy === "add" ? "Adding…" : "Add step"}</button>
      </div>
    </>
  );
}
