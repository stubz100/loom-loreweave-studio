// The Dock: one line when collapsed (the running job, its progress as the amber line across the
// full width, the counts), a resizable Jobs pane when expanded. Later the L3/L5 timeline zone.
import { cancelJob, stopJob, unpauseQueue, pauseQueue } from "@loom/shared/api/orchestrator";

import { pendingJobs, runningJob, useApp } from "../store";
import { Resizer } from "./Resizer";

export function Dock() {
  const jobs = useApp((s) => s.jobs);
  const counts = useApp((s) => s.counts);
  const paused = useApp((s) => s.paused);
  const pauseReason = useApp((s) => s.pauseReason);
  const offline = useApp((s) => s.offline);
  const open = useApp((s) => s.dockOpen);
  const height = useApp((s) => s.dockHeight);
  const toggleDock = useApp((s) => s.toggleDock);
  const setDockHeight = useApp((s) => s.setDockHeight);
  const notify = useApp((s) => s.notify);

  const running = runningJob(jobs);
  const pending = pendingJobs(jobs);
  const queued = counts?.queued ?? 0;
  const headline = offline ? "Queue unavailable"
    : running ? `Running ${running.pipeline}/${running.mode}${running.pass ? ` (${running.pass})` : ""}${running.note ? ` — ${running.note}` : ""}`
    : paused ? `Paused${pauseReason === "resume" ? " since the project reopened" : ""}, ${queued} waiting`
    : queued ? `${queued} queued, starting` : "Queue idle";
  const progress = running ? Math.max(0, Math.min(1, running.progress)) : 0;

  const act = (label: string, fn: () => Promise<unknown>) => {
    fn().catch((e) => notify("err", `${label} failed: ${String(e)}`));
  };

  return (
    <footer className="dock" style={open ? { height } : undefined}>
      {open && <Resizer edge="top" size={height} onResize={setDockHeight} />}
      <div className="dock-progress" style={{ width: `${progress * 100}%` }} />
      <div className="dock-line">
        <span className={`dot ${offline ? "err" : running ? "amber" : paused ? "warn" : ""}`} />
        <span className="headline">{headline}</span>
        {running && <span className="muted">{Math.round(progress * 100)}%</span>}
        <span className="spacer" />
        {counts && <span className="muted">{counts.done} done, {counts.failed} failed, {counts.canceled} canceled</span>}
        {!offline && (paused
          ? <button onClick={() => act("Resume", unpauseQueue)}>Resume</button>
          : <button onClick={() => act("Pause", pauseQueue)} disabled={!running && !queued}>Pause</button>)}
        <button onClick={toggleDock} aria-expanded={open}>{open ? "Hide jobs" : "Jobs"}</button>
      </div>
      {open && (
        <div className="dock-body">
          {pending.length === 0 && <div className="faint">Nothing queued or running.</div>}
          {pending.map((j) => (
            <div className="job-row" key={j.id}>
              <span className={`dot ${j.status === "running" ? "amber" : ""}`} />
              <span className="mono" title={j.id}>{j.id.slice(0, 12)}</span>
              <span className="note" title={j.note || ""}>
                {j.pipeline}/{j.mode}{j.pass ? ` (${j.pass})` : ""}{j.stage ? `, stage ${j.stage}` : ""}{j.note ? ` — ${j.note}` : ""}
              </span>
              <span className="muted">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : "queued"}</span>
              <span>
                {j.status === "running" && Array.isArray(j.params.batch_items) && (
                  <button onClick={() => act("Stop", () => stopJob(j.id))} title="finish the current item, then stop (completed images stay)">Stop</button>
                )}
                <button onClick={() => { if (window.confirm(`Cancel ${j.id}? A running job's partial output is discarded.`)) act("Cancel", () => cancelJob(j.id)); }}>Cancel</button>
              </span>
            </div>
          ))}
        </div>
      )}
    </footer>
  );
}
