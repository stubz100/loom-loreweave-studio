// Inspector ↻ re-run affordance (user 2026-07-04): a failed/NaN-black cell of a generated
// expansion set can't be re-created from the recipe bar — only the JOB record holds its
// exact prompt + coverage cell — so the recovery path is "clone this job as a new queue
// entry, optionally with new knobs (in case the same config fails again)". Dedicated
// component (pre-M1 review #4: new feature families stay OUT of the App.tsx monolith).

import { useEffect, useState } from "react";
import { Job } from "./lib/orchestrator";

interface RerunPanelProps {
  job: Job;                                            // terminal (done/failed/canceled)
  busy: boolean;
  onRerun: (params: Record<string, unknown>) => void;  // {} = exact re-roll, same knobs
}

// Different pipelines name their knobs differently — prefill from (and write back to)
// whichever alias the source job's params actually carry.
const STEP_KEYS = ["num_steps", "num_inference_steps", "steps"];
const GUIDANCE_KEYS = ["guidance", "guidance_scale", "cfg"];

function presentKey(params: Record<string, unknown>, keys: string[]): string {
  return keys.find((k) => params[k] !== undefined && params[k] !== null) ?? keys[0];
}

export default function RerunPanel({ job, busy, onRerun }: RerunPanelProps) {
  const params = (job.params ?? {}) as Record<string, unknown>;
  const stepKey = presentKey(params, STEP_KEYS);
  const guidKey = presentKey(params, GUIDANCE_KEYS);
  const [seed, setSeed] = useState("");
  const [steps, setSteps] = useState("");
  const [guidance, setGuidance] = useState("");

  useEffect(() => {   // re-prefill when the selection moves to another tile
    setSeed(params.seed != null ? String(params.seed) : "");
    setSteps(params[stepKey] != null ? String(params[stepKey]) : "");
    setGuidance(params[guidKey] != null ? String(params[guidKey]) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id]);

  const fire = () => {
    const p: Record<string, unknown> = {};
    if (seed.trim()) p.seed = Number(seed);
    if (steps.trim()) p[stepKey] = Number(steps);
    if (guidance.trim()) p[guidKey] = Number(guidance);
    onRerun(p);
  };

  return (
    <div className="rerun-panel">
      <div className="muted">
        ↻ RE-RUN — queue a fresh copy of this cell (same prompt/coverage cell; blank = keep):
      </div>
      <div className="rerun-form">
        <label>
          seed
          <input value={seed} inputMode="numeric"
                 onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))}
                 title="a sweep shares ONE seed — bump it here for just this cell (the usual fix for a NaN-black tile)" />
        </label>
        <label>
          {stepKey}
          <input value={steps} inputMode="numeric"
                 onChange={(e) => setSteps(e.target.value.replace(/[^0-9]/g, ""))} />
        </label>
        <label>
          {guidKey}
          <input value={guidance} inputMode="decimal"
                 onChange={(e) => setGuidance(e.target.value.replace(/[^0-9.]/g, ""))} />
        </label>
        <button className="ghost" disabled={busy} onClick={fire}
                title="submit as a NEW job in the same batch/stage (meta.rerun_of provenance) — the original tile stays until you delete it">
          ↻ Re-run
        </button>
      </div>
    </div>
  );
}
