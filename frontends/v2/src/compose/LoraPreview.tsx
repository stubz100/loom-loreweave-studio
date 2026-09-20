// The LoRA preview form (kb-loom-ui.md §3.6): opened from a done trainer row in the dock, it
// lives in the composer's Train tab. A framing pick (the L1 pose icons; T-pose is out of
// vocabulary and says so), a prompt override, seed, size (blank = the trained resolution, the
// only one the adapter holds identity at), weight — Render, or A/B against the bare base at
// the same seed. Samples land on the Train canvas.
import { useEffect, useState } from "react";

import { getPreviewPoses, poseIconUrl, previewTrainedLora, type PreviewPose } from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

export function LoraPreview({ jobId }: { jobId: string }) {
  const assetId = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const offline = useApp((s) => s.offline);
  const job = useApp((s) => s.jobs[jobId]);
  const notify = useApp((s) => s.notify);
  const setPreviewJob = useApp((s) => s.setPreviewJob);
  const setStage = useApp((s) => s.setStage);
  const versionId = detail?.profile.active_version;
  const [poses, setPoses] = useState<PreviewPose[]>([]);
  const [pose, setPose] = useState("");
  const [prompt, setPrompt] = useState("");
  const [seed, setSeed] = useState("12345");
  const [size, setSize] = useState("");
  const [weight, setWeight] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getPreviewPoses(assetId ?? undefined, versionId).then((r) => { if (live) { setPoses(r.poses); setPose((p) => p || r.default); } }).catch(() => { /* the prompt field still works */ });
    return () => { live = false; };
  }, [assetId, versionId]);

  const picked = poses.find((p) => p.id === pose);
  const body = () => {
    const sz = size.trim() ? Math.round(Math.min(2048, Math.max(256, Number(size))) / 16) * 16 : undefined;
    return {
      pose: prompt.trim() ? undefined : (pose || undefined),
      prompt: prompt.trim() || undefined,
      seed: seed.trim() ? Math.max(0, Number(seed)) : undefined,
      width: sz, height: sz,
      lora_weight: weight.trim() ? Math.min(4, Math.max(0, Number(weight))) : undefined,
    };
  };
  const fire = async (ab: boolean) => {
    setBusy(true); setProblem(null);
    try {
      const r = await previewTrainedLora(jobId, body());
      if (ab) await previewTrainedLora(jobId, { ...body(), with_lora: false });
      notify("ok", ab ? `A/B queued at the same seed: ${r.job_id} with the adapter, and one with the bare base. Both land on the Train canvas.` : `Preview ${r.job_id} queued; it lands on the Train canvas.`);
      setStage("train");
    } catch (e) { setProblem(reasonOf(e)); } finally { setBusy(false); }
  };

  return (
    <>
      <div className="section-title">Preview the adapter</div>
      <p className="faint">Run {jobId}{job?.status && job.status !== "done" ? ` (${job.status})` : ""}. The character appears only when the trigger token is in the prompt; the pose prompts carry it.</p>
      {poses.length > 0 && (
        <div className="cells">
          {poses.map((p) => (
            <button key={p.id} className={`cell${pose === p.id && !prompt.trim() ? " on" : ""}`} onClick={() => setPose(p.id)} disabled={!!prompt.trim()} aria-pressed={pose === p.id}
                    title={`${p.label}: ${p.prompt}${p.in_vocabulary ? "" : ". Not in the training vocabulary: the base model supplies the pose, the adapter only the identity; expect it weakest."}`}>
              {p.has_icon ? <img src={poseIconUrl(p.pose_key)} alt="" /> : <span className="cell-text">{p.label}{p.in_vocabulary ? "" : " (out of vocabulary)"}</span>}
            </button>
          ))}
        </div>
      )}
      {picked && !prompt.trim() && <p className="faint">{picked.label}{picked.in_vocabulary ? "" : " is out of the training vocabulary"}: {picked.prompt}</p>}
      <label className="p-field">Prompt override<textarea rows={2} value={prompt} placeholder="leave empty to use the picked pose; include the trigger token if you type one" onChange={(e) => setPrompt(e.target.value)} /></label>
      <div className="jt-row">
        <label className="p-field">Seed<input inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} title="keep it constant to compare weights, or A against B" /></label>
        <label className="p-field">Size<input inputMode="numeric" value={size} placeholder="trained" onChange={(e) => setSize(e.target.value.replace(/[^0-9]/g, ""))} title="square, multiples of 16; blank = the trained resolution, where the adapter holds identity" /></label>
        <label className="p-field">Weight<input value={weight} placeholder="1.0" onChange={(e) => setWeight(e.target.value.replace(/[^0-9.]/g, ""))} title="0 to 4; raise to 1.2 or 1.5 if the identity is weak" /></label>
      </div>
      <p className="faint">Identity collapses at twice the trained size. Render at the trained resolution and upscale with a Resize or Scale pass.</p>
      {problem && <p className="form-error" role="alert">{problem}</p>}
      <div className="jt-row">
        <button onClick={() => setPreviewJob(null)}>Close</button>
        <span className="spacer" />
        <button onClick={() => void fire(true)} disabled={busy || offline} title="two samples at the same seed: with the adapter, and the bare base. If they look the same, the adapter carries no signal.">A/B vs base</button>
        <button className="primary" onClick={() => void fire(false)} disabled={busy || offline}>{busy ? "Queuing…" : "Render"}</button>
      </div>
    </>
  );
}
