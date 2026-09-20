// Inspector · Info (kb-loom-ui.md §3.5): how the selected tile was made — the image, the
// facts, the pose, identity, the resolved prompt, the params and the log — then the actions
// on it: hero star and anchor for a cast candidate, face portrait, re-run with new knobs.
import { useEffect, useState } from "react";

import {
  cullRef, deriveFacePortrait, outputUrl, refUrl, rerunJob, setAnchor, starCandidate, type Job,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp, type Selection } from "../store";

const humanize = (s?: string) => (s ?? "").replace(/_/g, " ").trim();

/** A durable curated ref (no job behind it: a copied version keeps its files). */
export function RefInfo({ refId }: { refId: string }) {
  const assetId = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const offline = useApp((s) => s.offline);
  const refreshAsset = useApp((s) => s.refreshAsset);
  const select = useApp((s) => s.select);
  const notify = useApp((s) => s.notify);
  const [confirm, setConfirm] = useState(false);
  const version = detail?.versions.find((v) => v.id === detail.profile.active_version);
  const ref = version?.ref_set.find((r) => r.id === refId);
  if (!assetId || !version || !ref) return <p className="muted">That curated ref is gone.</p>;
  const onCull = async () => {
    if (!confirm) { setConfirm(true); return; }
    try { await cullRef(assetId, ref.id, version.id); await refreshAsset(); select(null); notify("ok", "Removed from the curated set."); }
    catch (e) { notify("err", reasonOf(e)); }
  };
  return (
    <>
      <img className="preview" src={refUrl(assetId, ref.file, version.id)} alt="" />
      <dl className="facts">
        <dt>ref</dt><dd className="mono">{ref.id}</dd>
        <dt>file</dt><dd className="mono">{ref.file}</dd>
        <dt>pose</dt><dd>{humanize(ref.coverage_cell.angle)}, {humanize(ref.coverage_cell.shot_size)}, {humanize(ref.coverage_cell.expression)}</dd>
        {ref.pipeline && <><dt>made by</dt><dd>{ref.pipeline}{ref.method ? ` (${ref.method})` : ""}{ref.seed != null ? `, seed ${ref.seed}` : ""}</dd></>}
        {ref.style_id && <><dt>style</dt><dd className="mono">{ref.style_id}</dd></>}
      </dl>
      <p className="faint">A curated copy in the version's refs folder. Its source generation is not on this grid.</p>
      {!version.finalized && <button onClick={() => void onCull()} onBlur={() => setConfirm(false)} disabled={offline}>{confirm ? "Remove from the curated set?" : "Remove from the set"}</button>}
    </>
  );
}
const STEP_KEYS = ["num_steps", "num_inference_steps", "steps"];
const GUIDANCE_KEYS = ["guidance", "guidance_scale", "cfg"];
const presentKey = (params: Record<string, unknown>, keys: string[]) => keys.find((k) => params[k] != null) ?? keys[0];

export function InfoTab({ selection, job }: { selection: Selection; job: Job }) {
  const selectedAsset = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const offline = useApp((s) => s.offline);
  const refreshAsset = useApp((s) => s.refreshAsset);
  const notify = useApp((s) => s.notify);
  const [busy, setBusy] = useState<string | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);

  const output = selection.output;
  const r = job.result;
  const p = job.params ?? {};
  const ometa = output ? r?.output_meta?.[output] : undefined;
  const cell = ometa?.coverage_cell ?? job.coverage_cell;
  const m = output ? /\/ideate\/([^/]+)\/seed_(\d+)\//.exec("/" + output) : null;
  const file = output ?? r?.output_name;
  const imageDur = ometa?.duration_s ?? ((r?.outputs?.length ?? 0) === 1 ? r?.duration_s : null);
  useEffect(() => { setDims(null); }, [file]);
  const pw = typeof p.width === "number" ? p.width : undefined;
  const ph = typeof p.height === "number" ? p.height : undefined;

  const version = detail?.versions.find((v) => v.id === detail.profile.active_version);
  const ownedCast = !!selectedAsset && job.stage === "A" && job.status === "done" && job.requester_id === version?.id;
  const candidate = ownedCast ? version?.casting.find((c) => c.job_id === job.id && (output ? c.source_output === output : true)) ?? null : null;
  const isAnchor = !!version?.anchor && version.anchor.job_id === job.id && (!output || version.anchor.source_output === output);
  const terminal = ["done", "failed", "canceled"].includes(job.status);

  const act = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key);
    try { await fn(); await refreshAsset(); notify("ok", done); }
    catch (e) { notify("err", reasonOf(e)); }
    finally { setBusy(null); }
  };

  // re-run knobs
  const stepKey = presentKey(p, STEP_KEYS);
  const guidKey = presentKey(p, GUIDANCE_KEYS);
  const [seed, setSeed] = useState("");
  const [steps, setSteps] = useState("");
  const [guidance, setGuidance] = useState("");
  useEffect(() => {
    setSeed(p.seed != null ? String(p.seed) : "");
    setSteps(p[stepKey] != null ? String(p[stepKey]) : "");
    setGuidance(p[guidKey] != null ? String(p[guidKey]) : "");
  }, [job.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const onRerun = () => {
    const params: Record<string, unknown> = {};
    if (seed.trim()) params.seed = Number(seed);
    if (steps.trim()) params[stepKey] = Number(steps);
    if (guidance.trim()) params[guidKey] = Number(guidance);
    void act("rerun", () => rerunJob(job.id, params), "Re-run queued as a new job in the same batch.");
  };

  return (
    <>
      {file && (r?.ok === true || job.status === "running") && (
        <img className="preview" src={outputUrl(file)} alt="" onLoad={(e) => setDims({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
      )}
      <dl className="facts">
        <dt>job</dt><dd className="mono">{job.id}</dd>
        <dt>status</dt><dd>{job.status}{job.status === "running" ? ` ${Math.round(job.progress * 100)}%` : ""}</dd>
        <dt>pipeline</dt><dd>{m ? m[1] : job.pipeline} / {job.mode}{job.pass ? ` (${job.pass} pass)` : ""}{job.stage ? `, stage ${job.stage}` : ""}</dd>
        <dt>model</dt><dd>{String(p.model_name ?? "default")}</dd>
        <dt>size</dt><dd>{dims ? `${dims.w} × ${dims.h}` : pw && ph ? `${pw} × ${ph}` : "unknown"}</dd>
        <dt>seed</dt><dd>{m ? m[2] : ometa?.seed ?? r?.seed ?? (p.seed as number | undefined) ?? "unknown"}</dd>
        {job.wall_s != null && <><dt>wall</dt><dd>{Math.round(job.wall_s)} s</dd></>}
        {(r?.outputs?.length ?? 0) > 1
          ? <><dt>batch</dt><dd>{r?.duration_s != null ? `${r.duration_s} s` : "unknown"}</dd><dt>image</dt><dd>{imageDur != null ? `${imageDur} s` : "unknown"}</dd></>
          : r?.duration_s != null ? <><dt>duration</dt><dd>{r.duration_s} s</dd></> : null}
        {file && <><dt>file</dt><dd className="mono">{file}</dd></>}
        {job.chained_from && <><dt>from</dt><dd className="mono">{job.chained_from}</dd></>}
        {job.style_id && <><dt>style</dt><dd className="mono">{job.style_id}</dd></>}
        {cell && <><dt>pose</dt><dd>{humanize(cell.angle)}, {humanize(cell.shot_size)}, {humanize(cell.expression)}{cell.background ? `, ${humanize(cell.background)}` : ""}</dd></>}
        {ometa && (ometa.identity || ometa.restore || ometa.anchor_cos != null) && (
          <><dt>identity</dt><dd>{ometa.identity ?? ""}{ometa.anchor_cos != null ? ` (anchor cos ${ometa.anchor_cos})` : ""}{ometa.restore ? `${ometa.identity ? ", " : ""}restore ${ometa.restore}` : ""}{ometa.faces != null ? ` (${ometa.faces} face${ometa.faces === 1 ? "" : "s"})` : ""}</dd></>
        )}
      </dl>
      {job.note && <p className="muted">{job.note}</p>}

      {(ownedCast || (selectedAsset && job.status === "done" && output)) && (
        <>
          <div className="section-title">Actions</div>
          <div className="jt-row" style={{ flexWrap: "wrap" }}>
            {ownedCast && (
              <button onClick={() => void act("star", () => starCandidate(selectedAsset!, job.id, !(candidate?.starred ?? false), output), candidate?.starred ? "Hero star removed." : "Starred as the hero. Expand grows the dataset from it.")}
                      disabled={busy !== null || offline}>{candidate?.starred ? "Unstar hero" : "Star as hero"}</button>
            )}
            {selectedAsset && output && (
              <>
                <button onClick={() => void act("anchor", () => setAnchor(selectedAsset, job.id, output, version?.id), "Face anchor set. The identity pass can lock every cell to it.")}
                        disabled={busy !== null || offline || isAnchor} title={isAnchor ? "this image is the anchor" : "use this image's face as the version's identity anchor"}>{isAnchor ? "Anchor ✓" : "Set as face anchor"}</button>
                <button onClick={() => void act("portrait", () => deriveFacePortrait(selectedAsset, job.id, output, version?.id), "Face portrait queued: anchor the result when it lands.")}
                        disabled={busy !== null || offline} title="a restored 512² crop of the largest face: a better anchor than a small face in a full-body shot">Face portrait</button>
              </>
            )}
          </div>
        </>
      )}

      {terminal && job.pipeline !== "zimage_trainer" && (
        <details className="p-group">
          <summary className="section-title">Re-run</summary>
          <p className="faint">Queue a fresh copy of this job with the same prompt and cell. Blank keeps a value.</p>
          <div className="jt-row">
            <label className="p-field">Seed<input inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} /></label>
            <label className="p-field">{stepKey.replace(/_/g, " ")}<input inputMode="numeric" value={steps} onChange={(e) => setSteps(e.target.value.replace(/[^0-9]/g, ""))} /></label>
            <label className="p-field">{guidKey.replace(/_/g, " ")}<input inputMode="decimal" value={guidance} onChange={(e) => setGuidance(e.target.value.replace(/[^0-9.]/g, ""))} /></label>
          </div>
          <div className="jt-row"><span className="spacer" /><button className="primary" onClick={onRerun} disabled={busy !== null || offline}>Re-run</button></div>
        </details>
      )}

      {(ometa?.prompt || typeof p.prompt === "string") && (
        <details className="p-group" open>
          <summary className="section-title">Prompt as run</summary>
          <p className="muted" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{ometa?.prompt ?? (p.prompt as string)}</p>
        </details>
      )}
      <details className="p-group">
        <summary className="section-title">Params as run</summary>
        <pre className="mono argv">{JSON.stringify(p, null, 1)}</pre>
      </details>
      {job.status === "failed" && r?.error && (
        <>
          <div className="section-title">Error</div>
          <p className="mono" style={{ color: "var(--err)", overflowWrap: "anywhere" }}>{r.error}</p>
        </>
      )}
      {job.log_tail && (job.status === "running" || job.status === "failed" || job.status === "canceled") && (
        <details className="p-group" open={job.status !== "running"}>
          <summary className="section-title">Log</summary>
          <pre className="mono argv">{job.log_tail}</pre>
        </details>
      )}
    </>
  );
}
