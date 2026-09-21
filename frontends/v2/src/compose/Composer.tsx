// The Composer (kb-loom-ui.md §3.3, migration steps 3a/3b): Cast / Sandbox generation as a
// column — model, prompt (text or the dev JSON tree), how many, style, advanced — with Preview
// and Generate pinned at the foot; Expand (Stage B) takes the same column from stage B on.
import { useEffect, useState } from "react";

import {
  generate, generatePreview, styleSampleUrl, type GeneratePreview, type GenerateRequest,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { buildRequest, effectiveModel, isDevSelected, PIPELINES, useCompose } from "./composeStore";
import { ExpandComposer } from "./Expand";
import { Flux2JsonTree } from "./Flux2JsonTree";
import { Problem, VariantWeights } from "../models/cacheUi";
import { ParamControls } from "./ParamControls";

/** The Composer follows the stage: Cast for the Sandbox or stage A, Expand from B on (a sweep
 *  can be re-fired while curating or training). Train mode is the Panel's own Train tab. */
export function Composer() {
  const selectedAsset = useApp((s) => s.selectedAsset);
  const stage = useApp((s) => s.stage);
  if (!selectedAsset || stage === "cast") return <CastComposer />;
  return <ExpandComposer />;
}

function CastComposer() {
  const project = useApp((s) => s.project);
  const offline = useApp((s) => s.offline);
  const disk = useApp((s) => s.disk);
  const selectedAsset = useApp((s) => s.selectedAsset);
  const stage = useApp((s) => s.stage);
  const notify = useApp((s) => s.notify);
  const setStage = useApp((s) => s.setStage);

  const c = useCompose();
  const catalog = c.catalog;
  const projectId = project?.id ?? null;

  useEffect(() => { if (!offline) void c.loadCatalog(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [offline]);
  useEffect(() => { if (projectId) void c.loadStyles(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [projectId]);

  const [busy, setBusy] = useState<"preview" | "generate" | null>(null);
  const [preview, setPreview] = useState<{ report: GeneratePreview; req: GenerateRequest } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const pipe = catalog?.[c.pipeline];
  const model = effectiveModel(c);
  const variant = pipe?.variants.find((v) => v.id === model);
  const dev = isDevSelected(c);
  const guidanceFixed = c.pipeline === "flux2" && !!variant && (variant as { guidance_fixed?: boolean }).guidance_fixed === true;
  const sizeDefaults = variant?.defaults && (variant.defaults.width != null || variant.defaults.height != null)
    ? { width: variant.defaults.width, height: variant.defaults.height } : undefined;
  const scope = selectedAsset ? { asset_id: selectedAsset } : null;
  const scopeLabel = selectedAsset ? "Cast for the selected character, stage A" : "Sandbox, unscoped";
  const styles = c.styles;
  const activeStyle = styles?.styles.find((s) => s.id === (c.styleId || styles.active_style_id));

  const blocked = !project ? "Open a project first." : offline ? "The orchestrator is offline." : disk?.state === "hard" ? "Disk hard-stop: free space first." : null;

  const fire = async (req: GenerateRequest) => {
    setBusy("generate"); setProblem(null);
    try {
      const res = await generate(req);
      notify("ok", `Queued ${res.job_ids.length} job${res.job_ids.length === 1 ? "" : "s"} (${req.pipeline}${model ? ` · ${model}` : ""}).`);
      setPreview(null);
      if (selectedAsset && stage !== "cast") setStage("cast");
    } catch (e) {
      setProblem(reasonOf(e));
    } finally {
      setBusy(null);
    }
  };
  const onGenerate = () => {
    const built = buildRequest(c, scope);
    if ("problem" in built) { setProblem(built.problem); return; }
    void fire(built.req);
  };
  const onPreview = async () => {
    const built = buildRequest(c, scope);
    if ("problem" in built) { setProblem(built.problem); return; }
    setBusy("preview"); setProblem(null);
    try {
      setPreview({ report: await generatePreview(built.req), req: built.req });
    } catch (e) {
      setProblem(reasonOf(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="panel-body compose">
        <p className="faint">{scopeLabel}</p>

        <div className="section-title">Model</div>
        <label className="p-field">Pipeline
          <select value={c.pipeline} onChange={(e) => c.setPipeline(e.target.value as typeof c.pipeline)}>
            {PIPELINES.map((p) => <option key={p.id} value={p.id} title={p.hint}>{p.label}</option>)}
          </select>
        </label>
        {c.pipeline !== "multi" && pipe && (
          <label className="p-field">Variant
            <select value={model ?? ""} onChange={(e) => c.setParam("model_name", e.target.value || undefined)}>
              {pipe.variants.map((v) => <option key={v.id} value={v.id} title={v.note}>{v.id}{v.gated ? " (gated)" : ""}</option>)}
            </select>
          </label>
        )}
        {c.pipeline !== "multi" && <VariantWeights pipeline={c.pipeline} model={model} />}
        {c.pipeline === "flux2" && pipe?.sampling_presets?.length ? (
          <label className="p-field">Sampling
            <select value={c.sampling} onChange={(e) => c.setSampling(e.target.value)}>
              <option value="">Custom (advanced fields)</option>
              {pipe.sampling_presets.map((p) => <option key={p.id} value={p.id} title={p.note}>{p.label}{p.recommended ? " ★" : ""}</option>)}
            </select>
          </label>
        ) : null}
        {guidanceFixed && <p className="faint">This variant is step-distilled: guidance has no effect. Pick a -base variant or flux.2-dev for guidance to bite.</p>}
        {c.catalogError && <p className="form-error">Could not load the model catalog: {c.catalogError}</p>}

        <div className="section-title">Prompt</div>
        <textarea value={c.prompt} rows={dev && c.treeOpen ? 2 : 5} spellCheck={false}
                  placeholder={dev ? "Plain text, used only when the JSON tree below is empty" : "What to make"}
                  onChange={(e) => c.setPrompt(e.target.value)} />
        {dev && (
          <>
            <label className="p-flag">
              <input type="checkbox" checked={c.treeOpen} onChange={(e) => c.setTreeOpen(e.target.checked)} />
              Author as a JSON prompt tree (flux.2-dev parses it precisely)
            </label>
            {c.treeOpen && <Flux2JsonTree value={c.tree} onChange={c.setTree} angleDirectives={pipe?.angle_directives ?? {}} />}
          </>
        )}

        <div className="section-title">How many</div>
        {c.pipeline === "multi" ? (
          <div className="jt-row">
            <label className="p-field">Candidates
              <input type="number" min={1} max={5} value={c.candidates} onChange={(e) => c.setCandidates(Number(e.target.value))} />
            </label>
            <label className="p-field">Mode
              <select value={c.ideation} onChange={(e) => c.setIdeation(e.target.value as "fast" | "refined")}>
                <option value="fast">Fast</option><option value="refined">Refined</option>
              </select>
            </label>
          </div>
        ) : (
          <label className="p-field">Images
            <input type="number" min={1} max={8} value={c.count} onChange={(e) => c.setCount(Number(e.target.value))} />
          </label>
        )}

        <div className="section-title">Style</div>
        <div className="style-row">
          {activeStyle?.sample?.file && <img className="style-chip" src={styleSampleUrl(activeStyle.id)} alt="" />}
          <select value={c.styleId} onChange={(e) => c.setStyleId(e.target.value)} disabled={!styles}>
            <option value="">{styles ? `Project default${activeStyle ? ` (${styles.styles.find((s) => s.id === styles.active_style_id)?.name ?? ""})` : ""}` : "No project"}</option>
            {styles?.styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <label className="p-flag" title="prepend the style fragment to the prompt and apply its global negative">
            <input type="checkbox" checked={c.applyStyle} onChange={(e) => c.setApplyStyle(e.target.checked)} />apply
          </label>
        </div>

        <details className="p-group" open={c.advancedOpen} onToggle={(e) => c.setAdvancedOpen((e.target as HTMLDetailsElement).open)}>
          <summary className="section-title">Advanced{Object.keys(c.params).length ? ` (${Object.keys(c.params).length} set)` : ""}</summary>
          {pipe ? (
            <>
              <ParamControls specs={pipe.params} mode={c.pipeline === "multi" ? "ideate" : "t2i"} values={c.params}
                             onChange={c.setParam} exclude={["model_name", "prompt"]} sizeDefaults={sizeDefaults} modelName={model} />
              <button onClick={c.resetParams}>Reset to defaults</button>
            </>
          ) : <p className="faint">Catalog not loaded.</p>}
        </details>
      </div>

      <div className="panel-foot">
        <Problem text={problem} />
        {blocked && !problem && <span className="faint">{blocked}</span>}
        <span className="spacer" />
        <button onClick={() => void onPreview()} disabled={!!blocked || busy !== null} title="dry-run: see the resolved prompt and the worker command before it runs">
          {busy === "preview" ? "Checking…" : "Preview"}
        </button>
        <button className="primary" onClick={onGenerate} disabled={!!blocked || busy !== null}>
          {busy === "generate" ? "Queuing…" : selectedAsset ? "Cast" : "Generate"}
        </button>
      </div>

      {preview && (
        <div className="modal-backdrop" onClick={() => setPreview(null)} role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-label="Preview" onClick={(e) => e.stopPropagation()}>
            <h2>Before it runs</h2>
            <dl className="facts">
              <dt>pipeline</dt><dd>{preview.report.pipeline}{preview.report.num_candidates ? `, ${preview.report.num_candidates} candidates` : `, ${preview.report.count} job${preview.report.count === 1 ? "" : "s"}`}</dd>
              <dt>prompt</dt><dd style={{ whiteSpace: "pre-wrap" }}>{preview.report.prompt}</dd>
              {preview.report.post_passes?.length ? <><dt>then</dt><dd>{preview.report.post_passes.map((p) => (p as { pass?: string }).pass ?? "pass").join(", ")}</dd></> : null}
              <dt>output</dt><dd className="mono">{preview.report.output_dir}</dd>
            </dl>
            <details><summary className="faint">worker command</summary><pre className="mono argv">{preview.report.argv.join(" ")}</pre></details>
            <div className="modal-actions">
              <button onClick={() => setPreview(null)}>Close</button>
              <button className="primary" onClick={() => void fire(preview.req)} disabled={busy !== null}>Run this</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
