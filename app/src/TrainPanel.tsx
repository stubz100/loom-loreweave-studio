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
  const onStage = async () => {
    setBusy("stage"); onError(null);
    try {
      await stageZimageLora(assetId, {
        version_id: versionId,
        trigger_token: trigger.trim() || undefined,
        steps: steps.trim() ? Math.min(10000, Math.max(1, Number(steps))) : undefined,
      });
      setTrigger(""); setSteps("");
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
