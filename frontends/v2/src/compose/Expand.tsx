// Expand mode (Stage B): the recipe as a column — hero + anchor at the head, the recipe and its
// cell picker, the model, identity with its rules stated in words, the character clause (per
// asset), style, sketch, advanced — and Generate dataset pinned at the foot. Was a fourteen-
// control wrapping bar in v1 (kb-loom-ui.md §2).
import { useEffect, useState } from "react";

import {
  anchorUrl, castingUrl, getPoseCells, matteHero, poseIconUrl, recipePresets, sketchHero, stageB,
  stageBPreview, styleSampleUrl, type PoseCell, type StageBPreview, type StageBRequest,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { buildStageB, effectiveModel, useCompose } from "./composeStore";
import { ParamControls } from "./ParamControls";

const SHOTS = ["face_closeup", "portrait", "waist_up", "full_body"];
const ANGLES = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"];
const EXPRESSIONS = ["neutral", "smile", "serious", "sad", "surprised"];
const nice = (s: string) => s.replace(/_/g, " ");

export function ExpandComposer() {
  const project = useApp((s) => s.project);
  const offline = useApp((s) => s.offline);
  const disk = useApp((s) => s.disk);
  const assetId = useApp((s) => s.selectedAsset)!;
  const detail = useApp((s) => s.assetDetail);
  const jobs = useApp((s) => s.jobs);
  const notify = useApp((s) => s.notify);
  const setStage = useApp((s) => s.setStage);
  const c = useCompose();
  const e = c.expand;
  const clause = c.clauses[assetId] ?? "";

  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const versionId = version?.id;
  const hero = version?.casting.find((x) => x.starred) ?? null;
  const anchor = version?.anchor ?? null;
  const anchorVerified = !!anchor && (!!anchor.verified_at
    || Object.values(jobs).some((j) => j.pipeline === "identity" && j.status === "done" && j.requester_id === versionId));
  const bgMask = (() => {
    const done = Object.values(jobs)
      .filter((j) => j.pipeline === "birefnet" && j.status === "done" && j.requester_id === versionId)
      .sort((a, b) => (b.finished_at ?? "").localeCompare(a.finished_at ?? ""));
    for (const j of done) {
      const r = j.result;
      const m = (r?.output_names ?? []).find((n) => r?.output_meta?.[n]?.role === "bgmask");
      if (m) return m;
    }
    return null;
  })();
  const matteQueued = Object.values(jobs).some((j) => j.pipeline === "birefnet" && (j.status === "queued" || j.status === "running") && j.requester_id === versionId);

  useEffect(() => { if (!offline) void c.loadCatalog(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [offline]);
  useEffect(() => { if (project?.id) void c.loadStyles(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [project?.id]);

  const [cells, setCells] = useState<PoseCell[] | null>(null);
  useEffect(() => {
    let alive = true;
    setCells(null);
    getPoseCells(e.preset).then((r) => { if (alive) setCells(r.cells); }).catch(() => { if (alive) setCells([]); });
    return () => { alive = false; };
  }, [e.preset]);

  const pipe = c.catalog?.[e.pipeline];
  const model = effectiveModel({ pipeline: e.pipeline, params: e.params, catalog: c.catalog });
  const variant = pipe?.variants.find((v) => v.id === model);
  const isFlux2 = e.pipeline === "flux2";
  const dev = isFlux2 && model === "flux.2-dev";
  const sizeDefaults = variant?.defaults && (variant.defaults.width != null || variant.defaults.height != null)
    ? { width: variant.defaults.width, height: variant.defaults.height } : undefined;
  const styles = c.styles;
  const chosenStyle = styles?.styles.find((s) => s.id === (e.styleId || styles.active_style_id));

  const identityAuto = !!anchor && anchorVerified && !isFlux2;
  const identityChecked = e.identity ?? identityAuto;
  const identityText = isFlux2
    ? "flux2's reference conditioning already carries identity. Ticking this adds an extra face-lock pass on top."
    : !anchor ? "Set a face anchor first: select a face image and use the Info tab's anchor action."
    : anchorVerified ? "Every cell's face is swapped to the anchor after generation; cells without a face pass through."
    : "The anchor is not verified yet. Tick to run identity now; the first run verifies it, then it stays on by default.";

  const selectedCount = e.cells === null ? (cells?.length ?? 0) : e.cells.length;
  const blocked = !project ? "Open a project first." : offline ? "The orchestrator is offline." : disk?.state === "hard" ? "Disk hard-stop: free space first."
    : !hero ? "Star a hero in Cast first." : e.cells !== null && e.cells.length === 0 ? "Pick at least one cell." : null;

  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ report: StageBPreview; body: StageBRequest } | null>(null);
  const [sketchOpen, setSketchOpen] = useState(false);

  const body = () => buildStageB(e, clause, bgMask);
  const fire = async (b: StageBRequest) => {
    setBusy("generate"); setProblem(null);
    try {
      const r = await stageB(assetId, b);
      notify("ok", `Queued the ${e.preset.replace(/_/g, " ")} sweep: ${r.items ?? "?"} cells in ${r.count} job${r.count === 1 ? "" : "s"}; aim to keep ${r.kept_target.join(" to ")}.`);
      setPreview(null);
      setStage("curate");
    } catch (err) {
      setProblem(reasonOf(err));
    } finally {
      setBusy(null);
    }
  };
  const onPreview = async () => {
    setBusy("preview"); setProblem(null);
    try {
      const b = body();
      setPreview({ report: await stageBPreview(assetId, b), body: b });
    } catch (err) {
      setProblem(reasonOf(err));
    } finally {
      setBusy(null);
    }
  };
  const onMatte = async () => {
    setBusy("matte"); setProblem(null);
    try {
      await matteHero(assetId, versionId);
      notify("ok", "Matte queued: the hero's subject matte, cutout and background mask.");
    } catch (err) {
      setProblem(reasonOf(err));
    } finally {
      setBusy(null);
    }
  };
  const onSketch = async () => {
    setBusy("sketch"); setProblem(null);
    try {
      const r = await sketchHero(assetId, {
        version_id: versionId, shot_size: e.sketch.shot, angle: e.sketch.angle, expression: e.sketch.expression,
        motion_prompt: e.sketch.motion.trim() || null, character_clause: clause.trim() || null,
        every: e.sketch.every, max_frames: e.sketch.frames, apply_style: e.applyStyle,
        ...(e.applyStyle && e.styleId ? { style_id: e.styleId } : {}),
      });
      notify("ok", `Sketch queued for ${nice(r.cell.shot_size)}, ${nice(r.cell.angle)}, ${nice(r.cell.expression)}.`);
    } catch (err) {
      setProblem(reasonOf(err));
    } finally {
      setBusy(null);
    }
  };
  const toggleCell = (i: number) => {
    const all = cells?.map((x) => x.index) ?? [];
    const cur = e.cells === null ? all : e.cells;
    const next = cur.includes(i) ? cur.filter((x) => x !== i) : [...cur, i].sort((a, b) => a - b);
    c.patchExpand({ cells: next.length === all.length ? null : next });
  };

  return (
    <>
      <div className="panel-body compose">
        <p className="faint">Expand {detail?.profile.name ?? ""}: one sweep of the coverage recipe from the hero, stage B.</p>

        <div className="hero-strip">
          {hero && versionId ? <img src={castingUrl(assetId, hero.file, versionId)} alt="hero" title="the hero ★" />
                              : <div className="hero-empty">no hero ★ yet</div>}
          {anchor && versionId ? <img src={anchorUrl(assetId, versionId)} alt="anchor" title={`face anchor${anchorVerified ? ", verified" : ", not verified yet"}`} />
                                : <div className="hero-empty">no anchor</div>}
          <div className="hero-text">
            <div>{hero ? "Hero ★ from Cast" : "Star a candidate in Cast first"}</div>
            <div className="faint">{anchor ? (anchorVerified ? "Anchor verified" : "Anchor set, not verified yet") : "No face anchor"}</div>
          </div>
        </div>

        <div className="section-title">Recipe</div>
        <label className="p-field">Preset
          <select value={e.preset} onChange={(ev) => c.patchExpand({ preset: ev.target.value as typeof e.preset })}>
            {recipePresets.map((p) => <option key={p} value={p}>{nice(p)}</option>)}
          </select>
        </label>
        <div className="cells-head">
          <span className="muted">{cells ? `${selectedCount} of ${cells.length} cells` : "Loading cells…"}</span>
          <button onClick={() => c.patchExpand({ cells: null })} disabled={e.cells === null}>All</button>
          <button onClick={() => c.patchExpand({ cells: [] })} disabled={e.cells !== null && e.cells.length === 0}>None</button>
        </div>
        {cells && (
          <div className="cells">
            {cells.map((cell) => {
              const on = e.cells === null || e.cells.includes(cell.index);
              const label = `${nice(cell.coverage_cell.shot_size)}, ${nice(cell.coverage_cell.angle)}, ${nice(cell.coverage_cell.expression)}`;
              return (
                <button key={cell.index} className={`cell${on ? " on" : ""}`} onClick={() => toggleCell(cell.index)} title={label} aria-pressed={on}>
                  {cell.icon ? <img src={poseIconUrl(cell.key)} alt="" loading="lazy" /> : <span className="cell-text">{label}</span>}
                </button>
              );
            })}
          </div>
        )}

        <div className="section-title">Model</div>
        <label className="p-field">Pipeline
          <select value={e.pipeline} onChange={(ev) => c.patchExpand({ pipeline: ev.target.value as typeof e.pipeline })}>
            <option value="zimage">Z-Image (img2img)</option>
            <option value="sd35">SD3.5 (img2img)</option>
            <option value="flux2">FLUX.2 (reference-conditioned, identity-preserving)</option>
          </select>
        </label>
        {pipe && (
          <label className="p-field">Variant
            <select value={model ?? ""} onChange={(ev) => c.setExpandParam("model_name", ev.target.value || undefined)}>
              {pipe.variants.map((v) => <option key={v.id} value={v.id} title={v.note}>{v.id}{v.gated ? " (gated)" : ""}</option>)}
            </select>
          </label>
        )}
        {isFlux2 && pipe?.sampling_presets?.length ? (
          <label className="p-field">Sampling
            <select value={e.sampling} onChange={(ev) => c.setExpandSampling(ev.target.value)}>
              <option value="">Custom (advanced fields)</option>
              {pipe.sampling_presets.map((p) => <option key={p.id} value={p.id} title={p.note}>{p.label}{p.recommended ? " ★" : ""}</option>)}
            </select>
          </label>
        ) : null}
        {isFlux2 && (
          <label className="p-flag" title="build each cell prompt from explicit camera and pose directives instead of the flat coverage phrase">
            <input type="checkbox" checked={e.advancedPrompt} onChange={(ev) => c.patchExpand({ advancedPrompt: ev.target.checked })} />
            Directive-led prompting{dev ? " (structured JSON on flux.2-dev)" : ""}
          </label>
        )}
        {!isFlux2 && (
          <>
            <label className="p-field">Strength
              <input type="number" min={0.1} max={1} step={0.05} value={e.strength} onChange={(ev) => c.patchExpand({ strength: Number(ev.target.value) })} />
            </label>
            <label className="p-field">Realize
              <select value={e.realize} onChange={(ev) => c.patchExpand({ realize: ev.target.value as "img2img" | "mixed" })}>
                <option value="img2img">img2img (every cell re-diffuses the hero)</option>
                <option value="mixed" disabled={!bgMask}>mixed (inpaint cells repaint the background around the held subject)</option>
              </select>
            </label>
            <div className="jt-row">
              <span className="muted">{bgMask ? "Background mask ready" : matteQueued ? "Matte job running…" : "Mixed needs the hero's background mask"}</span>
              <button onClick={() => void onMatte()} disabled={!hero || busy !== null || matteQueued}>{bgMask ? "Re-matte hero" : "Matte hero"}</button>
            </div>
          </>
        )}

        <div className="section-title">Identity</div>
        <label className="p-flag">
          <input type="checkbox" checked={identityChecked} disabled={!anchor && !isFlux2} onChange={(ev) => c.patchExpand({ identity: ev.target.checked })} />
          {isFlux2 ? "Extra face-lock pass" : "Lock every cell's face to the anchor"}{e.identity === null ? " (automatic)" : ""}
        </label>
        <p className="faint">{identityText}</p>

        <div className="section-title">Character clause</div>
        <textarea rows={3} value={clause} placeholder="who this is, in one clause; woven into every cell prompt" onChange={(ev) => c.setClause(assetId, ev.target.value)} />

        <div className="section-title">Style</div>
        <div className="style-row">
          {chosenStyle?.sample?.file && <img className="style-chip" src={styleSampleUrl(chosenStyle.id)} alt="" />}
          <select value={e.styleId} onChange={(ev) => c.patchExpand({ styleId: ev.target.value })} disabled={!styles}>
            <option value="">{styles ? "Project default" : "No project"}</option>
            {styles?.styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <label className="p-flag" title="off by default: the hero already carries the style (flux2 takes it from the reference, zimage/sd35 re-diffuse the styled source)">
            <input type="checkbox" checked={e.applyStyle} onChange={(ev) => c.patchExpand({ applyStyle: ev.target.checked })} />apply
          </label>
        </div>

        <details className="p-group" open={sketchOpen} onToggle={(ev) => setSketchOpen((ev.target as HTMLDetailsElement).open)}>
          <summary className="section-title">Video sketch</summary>
          <p className="faint">A short i2v motion sketch from the hero for one cell; stills that carry the cell are harvested for curation. Reaches poses img2img cannot.</p>
          <div className="jt-row">
            <label className="p-field">Shot<select value={e.sketch.shot} onChange={(ev) => c.patchExpand({ sketch: { ...e.sketch, shot: ev.target.value } })}>{SHOTS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></label>
            <label className="p-field">Angle<select value={e.sketch.angle} onChange={(ev) => c.patchExpand({ sketch: { ...e.sketch, angle: ev.target.value } })}>{ANGLES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></label>
            <label className="p-field">Expression<select value={e.sketch.expression} onChange={(ev) => c.patchExpand({ sketch: { ...e.sketch, expression: ev.target.value } })}>{EXPRESSIONS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></label>
          </div>
          <label className="p-field">Motion<input type="text" value={e.sketch.motion} placeholder="what moves, optional" onChange={(ev) => c.patchExpand({ sketch: { ...e.sketch, motion: ev.target.value } })} /></label>
          <div className="jt-row">
            <label className="p-field">Every nth frame<input type="number" min={1} max={30} value={e.sketch.every} onChange={(ev) => c.patchExpand({ sketch: { ...e.sketch, every: Number(ev.target.value) || 1 } })} /></label>
            <label className="p-field">Max stills<input type="number" min={1} max={32} value={e.sketch.frames} onChange={(ev) => c.patchExpand({ sketch: { ...e.sketch, frames: Number(ev.target.value) || 1 } })} /></label>
          </div>
          <button onClick={() => void onSketch()} disabled={!!blocked || busy !== null}>{busy === "sketch" ? "Queuing…" : "Sketch"}</button>
        </details>

        <details className="p-group">
          <summary className="section-title">Advanced{Object.keys(e.params).filter((k) => k !== "model_name").length ? ` (${Object.keys(e.params).filter((k) => k !== "model_name").length} set)` : ""}</summary>
          {pipe ? (
            <>
              <ParamControls specs={pipe.params} mode="img2img" values={e.params} onChange={c.setExpandParam}
                             exclude={["model_name", "prompt", "strength", "init_image", "mask_image"]} sizeDefaults={sizeDefaults} modelName={model} />
              <button onClick={() => c.patchExpand({ params: {}, sampling: "" })}>Reset to defaults</button>
            </>
          ) : <p className="faint">Catalog not loaded.</p>}
        </details>
      </div>

      <div className="panel-foot">
        {problem && <span className="form-error" role="alert">{problem}</span>}
        {blocked && !problem && <span className="faint">{blocked}</span>}
        <span className="spacer" />
        <button onClick={() => void onPreview()} disabled={!!blocked || busy !== null} title="dry-run: planned jobs, the hero, the first cell's prompt">
          {busy === "preview" ? "Checking…" : "Preview"}
        </button>
        <button className="primary" onClick={() => void fire(body())} disabled={!!blocked || busy !== null}>
          {busy === "generate" ? "Queuing…" : "Generate dataset"}
        </button>
      </div>

      {preview && (
        <div className="modal-backdrop" onClick={() => setPreview(null)} role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-label="Preview" onClick={(ev) => ev.stopPropagation()}>
            <h2>Before it runs</h2>
            <dl className="facts">
              <dt>recipe</dt><dd>{nice(preview.report.preset)} on {preview.report.pipeline}{preview.report.realize === "mixed" ? ", mixed" : ""}</dd>
              <dt>work</dt><dd>{preview.report.items ?? "?"} cells in {preview.report.planned_jobs} job{preview.report.planned_jobs === 1 ? "" : "s"}{preview.report.split ? ` (${Object.entries(preview.report.split).map(([k, v]) => `${v} ${k}`).join(", ")})` : ""}; aim to keep {preview.report.kept_target.join(" to ")}</dd>
              <dt>hero</dt><dd className="mono">{preview.report.hero}</dd>
              <dt>first cell</dt><dd>{nice(preview.report.first_cell.coverage_cell.shot_size)}, {nice(preview.report.first_cell.coverage_cell.angle)}, {nice(preview.report.first_cell.coverage_cell.expression)} ({preview.report.first_cell.method}, seed {preview.report.first_cell.seed})</dd>
              <dt>its prompt</dt><dd style={{ whiteSpace: "pre-wrap" }}>{preview.report.first_cell.prompt}</dd>
              {preview.report.post_passes?.length ? <><dt>then</dt><dd>{preview.report.post_passes.map((p) => (p as { pass?: string }).pass ?? "pass").join(", ")}</dd></> : null}
            </dl>
            <div className="modal-actions">
              <button onClick={() => setPreview(null)}>Close</button>
              <button className="primary" onClick={() => void fire(preview.body)} disabled={busy !== null}>Run this</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
