// Stage D · Train — the P2/M2 staged-LoRA panel (M2.9a; the M2-owed "[Train LoRA]" surface).
//
// R118 staged-job semantics, surfaced literally: [⚙ Stage] materializes captions/policy/
// context/dataset/train.yaml + a durable `jobs/staged.json` record but NEVER queues;
// [▶ Add to queue] is the explicit transition — the first moment GPU work can start.
// Promote-on-success is M6; this panel is stage → queue → watch (cancel like any job).
// Dedicated component (pre-M1 review #4: new feature families stay OUT of the App.tsx
// monolith).

import { useCallback, useEffect, useState } from "react";
import {
  Job,
  StagedTraining,
  deleteStagedTraining,
  getStagedTraining,
  queueStagedTraining,
  stageZimageLora,
} from "./lib/orchestrator";

interface TrainPanelProps {
  assetId: string;
  assetName: string;
  versionId: string;
  versionName?: string;
  versionLocked: boolean;
  refCount: number;                     // curated refs (Stage C) — the training corpus
  trainJobs: Job[];                     // zimage_trainer jobs for THIS version
  onCancelJob: (jobId: string) => void;
  onError: (msg: string | null) => void;
}

export default function TrainPanel({
  assetId, assetName, versionId, versionName, versionLocked, refCount,
  trainJobs, onCancelJob, onError,
}: TrainPanelProps) {
  const [staged, setStaged] = useState<StagedTraining[]>([]);
  const [trigger, setTrigger] = useState("");
  const [steps, setSteps] = useState("");   // blank → the M1-accepted default (500)
  // "advanced ⚙" (M5 pull-forward slice): the four knobs the staging endpoint already takes.
  // Blank = the M1-accepted preset value; everything else stays preset-pinned until M5
  // (quantize/low_vram/optimizer are rig-safety-sensitive on 16 GB — deliberately not here).
  const [adv, setAdv] = useState(false);
  const [rank, setRank] = useState("");     // 1–256 (preset 16)
  const [alpha, setAlpha] = useState("");   // 1–256 (preset 16)
  const [lr, setLr] = useState("");         // (0, 1] (preset 0.0001)
  const [res, setRes] = useState("");       // 256–2048 ÷16 (preset 512)
  // "stage" or a staged id — only the in-flight action's controls lock, not the whole panel
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStaged((await getStagedTraining()).staged);
    } catch {
      /* transient — the panel re-fetches on the next action */
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh, assetId, versionId]);

  const mine = staged.filter((s) => s.version_id === versionId);
  const canStage = !versionLocked && refCount > 0 && busy !== "stage";

  const clampSteps = () => {   // backend bound is 1–10000; keep what's shown = what's sent
    if (steps.trim()) setSteps(String(Math.min(10000, Math.max(1, Number(steps)))));
  };
  // Advanced-knob clamps mirror the backend bounds (keep what's shown = what's sent).
  const clampInt = (v: string, set: (s: string) => void, lo: number, hi: number) => {
    if (v.trim()) set(String(Math.min(hi, Math.max(lo, Number(v)))));
  };
  const clampRes = () => {     // 256–2048, snapped to /16 (the catalog dimension rule)
    if (res.trim()) {
      const n = Math.min(2048, Math.max(256, Number(res)));
      setRes(String(Math.round(n / 16) * 16));
    }
  };
  const clampLr = () => {      // (0, 1] float; junk or ≤0 clears back to the preset
    if (!lr.trim()) return;
    const n = Number(lr);
    if (!Number.isFinite(n) || n <= 0) setLr("");
    else if (n > 1) setLr("1");
  };
  const onStage = async () => {
    setBusy("stage"); onError(null);
    try {
      await stageZimageLora(assetId, {
        version_id: versionId,
        trigger_token: trigger.trim() || undefined,
        steps: steps.trim() ? Math.min(10000, Math.max(1, Number(steps))) : undefined,
        rank: rank.trim() ? Number(rank) : undefined,
        alpha: alpha.trim() ? Number(alpha) : undefined,
        learning_rate: lr.trim() ? Number(lr) : undefined,
        resolution: res.trim() ? Number(res) : undefined,
      });
      setTrigger(""); setSteps("");
      setRank(""); setAlpha(""); setLr(""); setRes("");
      await refresh();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onQueue = async (stagedId: string) => {
    setBusy(stagedId); onError(null);
    try {
      await queueStagedTraining(stagedId);   // the job appears via App's normal poll
      await refresh();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onDelete = async (stagedId: string) => {
    if (!window.confirm("Delete this staged training run? (Its temp dataset/config stay in _temp/ until cleanup.)")) return;
    setBusy(stagedId); onError(null);
    try {
      await deleteStagedTraining(stagedId);
      await refresh();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };

  return (
    <div className="train-panel">
      <div className="train-head">
        <b>STAGE D · TRAIN</b>
        <span className="muted">
          {" "}— character LoRA for {assetName}{versionName ? ` · ${versionName}` : ""} (zimage;
          preset: 500 steps · rank/alpha 16/16 · 512px · bf16 · qfloat8 · AdamW 1e-4 — the
          M1-accepted default). Staging writes captions + context + dataset; queueing is explicit
          (R118) and the job is resumable (R88).
        </span>
      </div>

      {versionLocked && (
        <p className="muted">🔒 This version is finalized — unlock it (or duplicate to a new
          version) to stage training.</p>
      )}
      {!versionLocked && refCount === 0 && (
        <p className="muted">No curated refs yet — keep ✓ Stage-B candidates in Stage C first
          (the ref set is the training corpus).</p>
      )}

      <div className="train-stage-form">
        <label>
          trigger token
          <input
            value={trigger}
            placeholder="(auto from name)"
            onChange={(e) => setTrigger(e.target.value)}
            disabled={!canStage}
            title="unique per character/version; letters/digits/underscores, starts with a letter (blank = derived from the character name, e.g. mara_lw)"
          />
        </label>
        <label>
          steps
          <input
            value={steps}
            placeholder="500"
            inputMode="numeric"
            onChange={(e) => setSteps(e.target.value.replace(/[^0-9]/g, ""))}
            onBlur={clampSteps}
            disabled={!canStage}
            title="total training steps, 1–10000 (M1 accepted 500 for the fixed-set spike)"
          />
        </label>
        <button className="ghost" onClick={() => setAdv(!adv)}
                title="rank/alpha, learning rate, resolution — blank = the M1-accepted preset. The rig-safety knobs (quantize/low_vram/optimizer) stay preset-pinned until M5.">
          {adv ? "▾" : "▸"} advanced
        </button>
        {adv && (
          <>
            <label className="sm">
              rank
              <input value={rank} placeholder="16" inputMode="numeric"
                     onChange={(e) => setRank(e.target.value.replace(/[^0-9]/g, ""))}
                     onBlur={() => clampInt(rank, setRank, 1, 256)}
                     disabled={!canStage}
                     title="LoRA rank (network.linear), 1–256 — adapter capacity; 16 = the M1 preset. Higher learns more detail but risks overfitting a small ref set." />
            </label>
            <label className="sm">
              alpha
              <input value={alpha} placeholder="16" inputMode="numeric"
                     onChange={(e) => setAlpha(e.target.value.replace(/[^0-9]/g, ""))}
                     onBlur={() => clampInt(alpha, setAlpha, 1, 256)}
                     disabled={!canStage}
                     title="LoRA alpha (network.linear_alpha), 1–256 — effective scale = alpha/rank; the usual convention is alpha = rank (M1 preset 16/16)." />
            </label>
            <label className="sm">
              learning rate
              <input value={lr} placeholder="0.0001"
                     onChange={(e) => setLr(e.target.value.replace(/[^0-9.eE-]/g, ""))}
                     onBlur={clampLr}
                     disabled={!canStage}
                     title="optimizer learning rate, (0, 1] — 1e-4 = the M1-accepted AdamW preset. The single most impactful knob: lower = slower/safer, higher = faster/less stable." />
            </label>
            <label className="sm">
              resolution
              <input value={res} placeholder="512" inputMode="numeric"
                     onChange={(e) => setRes(e.target.value.replace(/[^0-9]/g, ""))}
                     onBlur={clampRes}
                     disabled={!canStage}
                     title="training resolution, 256–2048 (÷16) — 512 = the M1 preset validated on the 16 GB rig; higher squares the memory/time cost." />
            </label>
          </>
        )}
        <button className="ghost" onClick={() => void onStage()} disabled={!canStage}
                title="materialize captions/policy/context/dataset/train.yaml + a staged record — does NOT queue (R118)">
          ⚙ Stage · Train LoRA ({refCount} ref{refCount === 1 ? "" : "s"})
        </button>
      </div>

      {mine.length > 0 && (
        <div className="train-staged">
          <div className="muted">STAGED (not queued — GPU starts only on ▶):</div>
          {mine.map((s) => (
            <div className="train-row" key={s.id}>
              <span className="train-row-main">
                <b>{s.trigger_token}</b>
                <span className="muted">
                  {" "}· {s.caption_count} caption{s.caption_count === 1 ? "" : "s"}
                  {" "}· {String((s.settings ?? {}).steps ?? "?")} steps
                  {" "}· r{String((s.settings ?? {}).rank ?? "?")}/a{String((s.settings ?? {}).alpha ?? "?")}
                  {" "}· {String((s.settings ?? {}).resolution ?? "?")}px
                  {" "}· lr {String((s.settings ?? {}).learning_rate ?? "?")}
                  {" "}· {s.id}
                  {s.context_digest ? ` · ctx ${s.context_digest.slice(0, 8).toLowerCase()}` : ""}
                </span>
              </span>
              <button className="ghost" onClick={() => void onQueue(s.id)} disabled={busy === s.id}
                      title="add to the GPU queue as a resumable trainer job (the explicit R118 transition)">
                ▶ Add to queue
              </button>
              <button className="ghost" onClick={() => void onDelete(s.id)} disabled={busy === s.id}
                      title="drop the staged record (no queued/running job is touched)">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {trainJobs.length > 0 && (
        <div className="train-jobs">
          <div className="muted">TRAINING JOBS (this version):</div>
          {trainJobs.map((j) => (
            <div className="train-row" key={j.id}>
              <span className="train-row-main">
                {j.status === "done" ? "✅" : j.status === "failed" ? "❌"
                  : j.status === "canceled" ? "🚫" : j.status === "running" ? "⏳" : "🕐"}{" "}
                <b>{j.id}</b>
                <span className="muted">
                  {" "}· {j.status}
                  {j.status === "running" ? ` · ${Math.round((j.progress || 0) * 100)}%` : ""}
                  {j.note ? ` · ${j.note}` : ""}
                  {j.result?.error && j.status === "failed" ? ` · ${j.result.error}` : ""}
                </span>
              </span>
              {(j.status === "queued" || j.status === "running") && (
                <button className="ghost" onClick={() => onCancelJob(j.id)}
                        title="cancel the trainer job (resumable jobs recover their checkpoint on a re-queue)">
                  ✕ cancel
                </button>
              )}
            </div>
          ))}
          <p className="muted">
            A finished run leaves its adapter in the run dir; <b>promote into the version is M6</b>
            {" "}(not wired yet) — the artifact path is on the job result.
          </p>
        </div>
      )}
    </div>
  );
}
