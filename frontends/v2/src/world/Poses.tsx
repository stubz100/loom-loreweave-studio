// Pose icons (M2.11, kb-loom-ui.md §3.8): one 256² flux2 render per pose of a recipe on a
// neutral subject; the Expand cell picker and the LoRA preview use them. Generate the missing
// ones or all, redo one with a fresh seed, delete one. Generation jobs close through the one
// poller (v1 polled them with its own timer).
import { useCallback, useEffect, useState } from "react";

import {
  deletePoseIcon, generatePoseIcons, getPoseCells, poseIconUrl, recipePresets, setPoseIcon, type PoseCell,
} from "@loom/shared/api/orchestrator";

import { nice } from "../lib/coverage";
import { reasonOf } from "../lib/project";
import { useApp } from "../store";

const DEFAULT_SUBJECT = "a simple wooden mannequin figure, plain light grey background";

export function Poses() {
  const preset = useApp((s) => s.poseSet);
  const setPoseSet = useApp((s) => s.setPoseSet);
  const jobs = useApp((s) => s.jobs);
  const offline = useApp((s) => s.offline);
  const notify = useApp((s) => s.notify);
  const [subject, setSubject] = useState(DEFAULT_SUBJECT);
  const [turbo, setTurbo] = useState(true);
  const [cells, setCells] = useState<PoseCell[] | null>(null);
  const [pending, setPending] = useState<{ key: string; job_id: string }[]>([]);
  const [cacheKey, setCacheKey] = useState(String(Date.now()));
  const [confirmAll, setConfirmAll] = useState(false);
  const [confirmDrop, setConfirmDrop] = useState<string | null>(null);

  const refresh = useCallback(async (p: string) => {
    try { setCells((await getPoseCells(p)).cells); setCacheKey(String(Date.now())); }
    catch (e) { notify("err", reasonOf(e)); }
  }, [notify]);
  useEffect(() => { setCells(null); void refresh(preset); }, [preset, refresh]);

  // A finished generation pins its output as the icon; the poller feeds `jobs`.
  useEffect(() => {
    if (!pending.length) return;
    const still: typeof pending = [];
    const finished: typeof pending = [];
    for (const p of pending) {
      const j = jobs[p.job_id];
      if (j && (j.status === "queued" || j.status === "running")) still.push(p); else finished.push(p);
    }
    if (!finished.length) return;
    setPending(still);
    void (async () => {
      for (const p of finished) {
        const j = jobs[p.job_id];
        const out = j?.result?.output_name;
        if (j?.status === "done" && out) { try { await setPoseIcon(p.key, out); } catch (e) { notify("err", reasonOf(e)); } }
        else if (j?.status === "failed") notify("err", `Icon ${nice(p.key)} failed: ${j.result?.error ?? "see the dock"}`);
      }
      await refresh(preset);
    })();
  }, [jobs, pending, preset, refresh, notify]);

  const fire = async (force: boolean, keys?: string[]) => {
    setConfirmAll(false);
    try {
      const r = await generatePoseIcons({ preset, subject: subject.trim() || undefined, turbo, force, keys, ...(keys ? { seed: Math.floor(Math.random() * 2 ** 31) } : {}) });
      if (r.count === 0) { notify("info", "Every pose already has an icon."); return; }
      setPending((prev) => [...prev, ...r.jobs]);
      notify("ok", `${r.count} icon${r.count === 1 ? "" : "s"} queued.`);
    } catch (e) { notify("err", reasonOf(e)); }
  };
  const drop = async (key: string) => {
    if (confirmDrop !== key) { setConfirmDrop(key); return; }
    setConfirmDrop(null);
    try { await deletePoseIcon(key); await refresh(preset); } catch (e) { notify("err", reasonOf(e)); }
  };

  const have = cells?.filter((c) => c.icon).length ?? 0;
  return (
    <div className="editor">
      <div className="editor-head">
        <h2>Pose icons</h2>
        <span className="faint">one small render per pose on a neutral subject; a wrong-looking icon is a directive bug caught cheap</span>
      </div>
      <div className="jt-row" style={{ flexWrap: "wrap" }}>
        <label className="p-field">Recipe
          <select value={preset} onChange={(e) => setPoseSet(e.target.value)}>{recipePresets.map((p) => <option key={p} value={p}>{nice(p)}</option>)}</select>
        </label>
        <label className="p-field" style={{ flex: 2 }}>Subject<input type="text" value={subject} onChange={(e) => setSubject(e.target.value)} title="the neutral stand-in rendered in every pose" /></label>
        <label className="p-flag" title="Turbo LoRA few-step rendering: icons are tiny, this makes the set fast"><input type="checkbox" checked={turbo} onChange={(e) => setTurbo(e.target.checked)} />turbo</label>
      </div>
      <div className="jt-row">
        <span className="muted">{cells ? `${have} of ${cells.length} icons` : "Loading…"}{pending.length ? `, ${pending.length} generating` : ""}</span>
        <span className="spacer" />
        <button className="primary" onClick={() => void fire(false)} disabled={offline || !cells} title="icons for the poses that have none yet">Generate missing</button>
        <button className={confirmAll ? "danger" : ""} onBlur={() => setConfirmAll(false)} onClick={() => { if (confirmAll) void fire(true); else setConfirmAll(true); }} disabled={offline || !cells} title="re-render every pose of this recipe, replacing existing icons">{confirmAll ? "Replace all icons?" : "Redo all"}</button>
      </div>
      {cells && (
        <div className="pose-grid">
          {cells.map((c) => {
            const label = `${nice(c.coverage_cell.shot_size)}, ${nice(c.coverage_cell.angle)}, ${nice(c.coverage_cell.expression)}`;
            const busy = pending.some((p) => p.key === c.key);
            return (
              <div key={c.index} className={`pose-wrap${busy ? " busy" : ""}`}>
                <div className="cell on" title={label}>
                  {c.icon ? <img src={poseIconUrl(c.key, cacheKey)} alt="" loading="lazy" /> : <span className="cell-text">{label}</span>}
                  <span className="cap">{c.index}</span>
                </div>
                <div className="pose-acts">
                  <button onClick={() => void fire(false, [c.key])} disabled={busy || offline} title="redo just this icon with a fresh seed">{busy ? "…" : "Redo"}</button>
                  {c.icon && <button className={confirmDrop === c.key ? "danger" : ""} onBlur={() => setConfirmDrop(null)} onClick={() => void drop(c.key)} disabled={busy || offline}>{confirmDrop === c.key ? "Delete?" : "Delete"}</button>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
