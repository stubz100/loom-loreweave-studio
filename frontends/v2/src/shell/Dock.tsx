// The Dock (kb-loom-ui.md §3.8, migration step 6): one line when collapsed (the running job,
// its progress as the amber line across the full width, the counts); expanded, a Jobs pane
// with three filters — Active (queued + running), Training (the version's staged runs and
// trainer jobs with step / loss / ETA, Preview, Promote, Cleanup, Remove), Recent (what
// finished). Every destructive button is a second click. Later the L3/L5 timeline zone.
import { useEffect, useState } from "react";

import {
  cancelJob, cleanupTrainingRun, deleteJob, deleteStagedTraining, pauseQueue, promoteTrainedLora,
  queueStagedTraining, stopJob, unpauseQueue, type Job,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { pendingJobs, runningJob, useApp, type DockFilter } from "../store";
import { Resizer } from "./Resizer";

const FILTERS: { id: DockFilter; label: string }[] = [
  { id: "active", label: "Active" }, { id: "training", label: "Training" }, { id: "recent", label: "Recent" },
];

export function Dock() {
  const jobs = useApp((s) => s.jobs);
  const counts = useApp((s) => s.counts);
  const paused = useApp((s) => s.paused);
  const pauseReason = useApp((s) => s.pauseReason);
  const offline = useApp((s) => s.offline);
  const open = useApp((s) => s.dockOpen);
  const height = useApp((s) => s.dockHeight);
  const filter = useApp((s) => s.dockFilter);
  const setDockFilter = useApp((s) => s.setDockFilter);
  const toggleDock = useApp((s) => s.toggleDock);
  const setDockHeight = useApp((s) => s.setDockHeight);
  const notify = useApp((s) => s.notify);
  const staged = useApp((s) => s.staged);
  const refreshStaged = useApp((s) => s.refreshStaged);
  const refreshAsset = useApp((s) => s.refreshAsset);
  const selectedAsset = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const setPreviewJob = useApp((s) => s.setPreviewJob);
  const togglePanel = useApp((s) => s.togglePanel);
  const [confirm, setConfirm] = useState<string | null>(null);   // "<action>:<id>" awaiting its second click
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const running = runningJob(jobs);
  const pending = pendingJobs(jobs);
  const queued = counts?.queued ?? 0;
  const headline = offline ? "Queue unavailable"
    : running ? `Running ${running.pipeline}/${running.mode}${running.pass ? ` (${running.pass})` : ""}${running.note ? ` — ${running.note}` : ""}`
    : paused ? `Paused${pauseReason === "resume" ? " since the project reopened" : ""}, ${queued} waiting`
    : queued ? `${queued} queued, starting` : "Queue idle";
  const progress = running ? Math.max(0, Math.min(1, running.progress)) : 0;

  useEffect(() => { if (open && filter === "training" && !offline) void refreshStaged(); }, [open, filter, offline, refreshStaged]);

  const act = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key); setConfirm(null);
    try { await fn(); if (done) notify("ok", done); }
    catch (e) { notify("err", reasonOf(e)); }
    finally { setBusy(null); }
  };
  /** A destructive action: the first click arms it, the second runs it. */
  const twice = (key: string, label: string, armed: string, fn: () => Promise<unknown>, done?: string, disabled = false) => (
    <button className={confirm === key ? "danger" : ""} disabled={disabled || busy !== null || offline} onBlur={() => { if (confirm === key) setConfirm(null); }}
            onClick={() => { if (confirm === key) void act(key, fn, done); else setConfirm(key); }}>
      {confirm === key ? armed : label}
    </button>
  );

  const trainJobs = Object.values(jobs)
    .filter((j) => j.pipeline === "zimage_trainer" && (!version || j.profile_version_id === version.id))
    .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  const stagedHere = staged.filter((s) => (!version || s.version_id === version.id) && (!selectedAsset || s.asset_id === selectedAsset));
  const recent = Object.values(jobs)
    .filter((j) => j.status === "done" || j.status === "failed" || j.status === "canceled")
    .sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at))
    .slice(0, 40);

  const jobRow = (j: Job, extra?: React.ReactNode) => (
    <div className={`job-row s-${j.status}`} key={j.id}>
      <span className={`dot ${j.status === "running" ? "amber" : j.status === "done" ? "ok" : j.status === "failed" ? "err" : ""}`} />
      <span className="mono" title={j.id}>{j.id.slice(0, 12)}</span>
      <span className="note" title={j.note || j.result?.error || ""}>
        {j.pipeline}/{j.mode}{j.pass ? ` (${j.pass})` : ""}{j.stage ? `, stage ${j.stage}` : ""}{j.note ? ` — ${j.note}` : ""}{j.status === "failed" && j.result?.error ? ` — ${j.result.error}` : ""}{notes[j.id] ? ` — ${notes[j.id]}` : ""}
      </span>
      <span className="muted">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : j.status === "done" && j.wall_s != null ? `${Math.round(j.wall_s)} s` : j.status}</span>
      <span className="job-acts">
        {j.status === "running" && Array.isArray(j.params.batch_items) && (
          <button onClick={() => void act(`stop:${j.id}`, () => stopJob(j.id))} disabled={busy !== null || offline} title="finish the current item, then stop; completed images stay">Stop</button>
        )}
        {(j.status === "queued" || j.status === "running") && twice(`cancel:${j.id}`, "Cancel", "Cancel?", () => cancelJob(j.id), undefined)}
        {extra}
      </span>
    </div>
  );

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
          ? <button onClick={() => void act("resume", unpauseQueue)}>Resume</button>
          : <button onClick={() => void act("pause", pauseQueue)} disabled={!running && !queued}>Pause</button>)}
        <button onClick={toggleDock} aria-expanded={open}>{open ? "Hide jobs" : "Jobs"}</button>
      </div>
      {open && (
        <div className="dock-body">
          <div className="tabs dock-tabs" role="tablist">
            {FILTERS.map((f) => <button key={f.id} role="tab" aria-selected={filter === f.id} className={`tab${filter === f.id ? " active" : ""}`} onClick={() => setDockFilter(f.id)}>{f.label}</button>)}
            {filter === "training" && <span className="faint">{version ? `${detail?.profile.name}, ${version.name}` : "every character"}</span>}
          </div>
          {filter === "active" && (
            <>
              {pending.length === 0 && <div className="faint">Nothing queued or running.</div>}
              {pending.map((j) => jobRow(j))}
            </>
          )}
          {filter === "training" && (
            <>
              {stagedHere.length === 0 && trainJobs.length === 0 && <div className="faint">No staged runs and no trainer jobs{version ? " for this version" : ""}. Stage one from the Train tab.</div>}
              {stagedHere.map((s) => (
                <div className="job-row s-staged" key={s.id}>
                  <span className="dot" />
                  <span className="mono" title={s.id}>staged</span>
                  <span className="note" title={s.id}>
                    <b>{s.trigger_token}</b> {s.base_family ?? "zimage"}{s.train_init === "seed_parent" ? " seeded from the parent" : ""}, {s.caption_count} captions, {String(s.settings?.steps ?? "?")} steps, rank {String(s.settings?.rank ?? "?")}/{String(s.settings?.alpha ?? "?")}, {String(s.settings?.resolution ?? "?")} px, lr {String(s.settings?.learning_rate ?? "?")}{!version ? `, ${s.asset_name ?? s.asset_id}` : ""}
                  </span>
                  <span className="muted">not queued</span>
                  <span className="job-acts">
                    <button className="primary" onClick={() => void act(`queue:${s.id}`, async () => { await queueStagedTraining(s.id); await refreshStaged(); }, "Training queued. Progress shows here.")} disabled={busy !== null || offline}>Add to queue</button>
                    {twice(`unstage:${s.id}`, "Remove", "Remove the staged run?", async () => { await deleteStagedTraining(s.id); await refreshStaged(); })}
                  </span>
                </div>
              ))}
              {trainJobs.map((j) => jobRow(j, (
                <>
                  {j.status === "done" && (
                    <>
                      <button onClick={() => { setPreviewJob(j.id); togglePanel("train"); }} disabled={offline} title="sample the fresh adapter before promoting; opens the form in the Train tab">Preview</button>
                      <button onClick={() => void act(`promote:${j.id}`, async () => { await promoteTrainedLora(j.id); await refreshAsset(); }, "Adapter promoted into the version.")}
                              disabled={busy !== null || offline || !!version?.finalized} title="copy the adapter into the version with its manifest; the temp dir stays until Cleanup">Promote</button>
                      {twice(`clean:${j.id}`, "Cleanup", "Delete the temp dir?", async () => { const r = await cleanupTrainingRun(j.id); setNotes((n) => ({ ...n, [j.id]: r.cleaned ? "temp cleaned" : "already clean" })); })}
                    </>
                  )}
                  {(j.status === "failed" || j.status === "canceled") && (
                    <>
                      {twice(`clean:${j.id}`, "Cleanup", "Delete the temp dir?", async () => { const r = await cleanupTrainingRun(j.id); setNotes((n) => ({ ...n, [j.id]: r.cleaned ? "temp cleaned" : "already clean" })); })}
                      {twice(`remove:${j.id}`, "Remove", "Remove the run?", async () => { try { await cleanupTrainingRun(j.id); } catch { /* temp may be gone */ } await deleteJob(j.id); })}
                    </>
                  )}
                </>
              )))}
              {version?.lora && <div className="faint">Promoted adapter: {version.lora.file}, {version.lora.base_family}, {new Date(version.lora.promoted_at).toLocaleString()}.</div>}
            </>
          )}
          {filter === "recent" && (
            <>
              {recent.length === 0 && <div className="faint">Nothing has finished yet.</div>}
              {recent.map((j) => jobRow(j))}
            </>
          )}
        </div>
      )}
    </footer>
  );
}
