// M2.11 — pose icons + the expansion CellPicker (spec §12 "M2.11").
//
// Two surfaces over the same data (GET /bible/poses — index-aligned with Stage-B):
//   • PosesPanel — the L1 · Poses sub-tab: generate the per-cell icon set (flux2-dev
//     advanced JSON @256², neutral mannequin subject, NO L1 style/refs) and watch it fill.
//     Icons are cell-keyed + durable bible-side (the L1-styles sample pattern).
//   • CellPicker — the Stage-B subset selector: toggle cells, fire only those indices.
// Dedicated component file (pre-M1 review #4: new feature families stay OUT of App.tsx).

import { useCallback, useEffect, useRef, useState } from "react";
import {
  PoseCell,
  deletePoseIcon,
  generatePoseIcons,
  getJob,
  getPoseCells,
  poseIconUrl,
  setPoseIcon,
} from "./lib/orchestrator";

const PRESETS = ["npc_lite", "portrait_heavy", "full_body", "full_coverage", "comprehensive"];
const DEFAULT_SUBJECT = "a simple wooden mannequin figure, plain light grey background";

function chip(c: PoseCell): string {
  const cc = c.coverage_cell;
  return `${cc.shot_size} · ${cc.angle} · ${cc.expression}`.replace(/_/g, " ");
}

/** One pose tile: the generated icon when it exists, else a text chip. */
function PoseTile({ cell, cacheKey, selected, onClick, title }: {
  cell: PoseCell; cacheKey: string;
  selected?: boolean; onClick?: () => void; title?: string;
}) {
  return (
    <button className={`pose-tile ${selected === undefined ? "" : selected ? "on" : "off"}`}
            onClick={onClick} title={title ?? chip(cell)}>
      {cell.icon
        ? <img src={poseIconUrl(cell.key, cacheKey)} alt={chip(cell)} />
        : <span className="pose-chip">{chip(cell)}</span>}
      <span className="pose-cap">{cell.index}</span>
    </button>
  );
}

// --- L1 · Poses: generate + browse the icon set ---------------------------------------

export function PosesPanel({ onError }: { onError: (m: string) => void }) {
  const [preset, setPreset] = useState(PRESETS[0]);
  const [subject, setSubject] = useState(DEFAULT_SUBJECT);
  const [turbo, setTurbo] = useState(true);
  const [cells, setCells] = useState<PoseCell[]>([]);
  const [pending, setPending] = useState<{ key: string; job_id: string }[]>([]);
  const [cacheKey, setCacheKey] = useState(String(Date.now()));
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async (p: string) => {
    try {
      setCells((await getPoseCells(p)).cells);
      setCacheKey(String(Date.now()));   // bust <img> caches after icons change
    } catch (e) { onError(String(e)); }
  }, [onError]);
  useEffect(() => { void refresh(preset); }, [preset, refresh]);

  // watch generation jobs: when one finishes, persist its output as the icon (the
  // L1-styles pattern — the client closes the loop), then refresh the grid.
  useEffect(() => {
    if (!pending.length) return;
    pollRef.current = window.setInterval(async () => {
      const still: typeof pending = [];
      for (const p of pending) {
        try {
          const j = await getJob(p.job_id);
          if (j && (j.status === "queued" || j.status === "running")) { still.push(p); continue; }
          const out = j?.result?.output_name;
          if (j?.status === "done" && out) await setPoseIcon(p.key, out);
        } catch { /* drop it — a re-generate can redo the key */ }
      }
      if (still.length !== pending.length) {
        setPending(still);
        void refresh(preset);
      }
    }, 3000);
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [pending, preset, refresh]);

  const fire = async (force: boolean) => {
    try {
      const r = await generatePoseIcons({ preset, subject: subject.trim() || undefined,
                                          turbo, force });
      if (r.count === 0) { onError("nothing to generate — every pose already has an icon"); return; }
      setPending((prev) => [...prev, ...r.jobs]);
    } catch (e) { onError(String(e)); }
  };
  // per-icon re-run (author 2026-07-05: batch fills leave odd characters): ONE key, with a
  // FRESH random seed — the set's fixed seed would reproduce the same odd render.
  const fireOne = async (key: string) => {
    try {
      const r = await generatePoseIcons({ preset, subject: subject.trim() || undefined,
                                          turbo, keys: [key],
                                          seed: Math.floor(Math.random() * 2 ** 31) });
      setPending((prev) => [...prev, ...r.jobs]);
    } catch (e) { onError(String(e)); }
  };
  const dropOne = async (key: string) => {
    try {
      await deletePoseIcon(key);
      void refresh(preset);
    } catch (e) { onError(String(e)); }
  };

  const have = cells.filter((c) => c.icon).length;
  return (
    <div className="poses-panel">
      <p className="muted">
        POSE ICONS — one 256² flux2 render per pose of a recipe (M0d directive prompts on a
        neutral subject, no L1 style). The Stage-B cell picker uses them; recipes sharing a
        pose share its icon. A wrong-looking icon = a directive bug caught cheap.
      </p>
      <div className="poses-bar">
        <select value={preset} onChange={(e) => setPreset(e.target.value)}>
          {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <input className="pose-subject" value={subject}
               onChange={(e) => setSubject(e.target.value)}
               title="the neutral stand-in rendered in every pose (mannequin / stickman / santa…)" />
        <label className="p-flag" title="Turbo LoRA few-step (dev) — icons are tiny, this makes the set fast">
          <input type="checkbox" checked={turbo} onChange={(e) => setTurbo(e.target.checked)} />
          turbo
        </label>
        <button className="ghost" onClick={() => void fire(false)}
                title="generate icons for poses that don't have one yet">
          ⚙ Generate missing
        </button>
        <button className="ghost" onClick={() => void fire(true)}
                title="re-generate EVERY pose of this recipe (overwrites existing icons)">
          ↻ All
        </button>
        <span className="muted">
          {have}/{cells.length} icons{pending.length ? ` · ${pending.length} generating…` : ""}
        </span>
      </div>
      <div className="pose-grid">
        {cells.map((c) => (
          <div className="pose-wrap" key={c.index}>
            <PoseTile cell={c} cacheKey={cacheKey} />
            <div className="pose-acts">
              <button className="ghost" onClick={() => void fireOne(c.key)}
                      disabled={pending.some((p) => p.key === c.key)}
                      title="re-generate JUST this icon with a fresh random seed (the batch seed would reproduce the same render)">
                ↻
              </button>
              {c.icon && (
                <button className="ghost" onClick={() => void dropOne(c.key)}
                        title="delete this icon (back to a text chip; ↻ to redo it)">
                  🗑
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Stage-B CellPicker: fire a SUBSET of the recipe -----------------------------------

export function CellPicker({ preset, sel, onChange, onClose }: {
  preset: string;
  sel: number[] | null;                       // null = all cells (no filter)
  onChange: (sel: number[] | null) => void;
  onClose: () => void;
}) {
  const [cells, setCells] = useState<PoseCell[]>([]);
  const [cacheKey] = useState(String(Date.now()));
  useEffect(() => {
    getPoseCells(preset).then((r) => setCells(r.cells)).catch(() => setCells([]));
  }, [preset]);
  const selected = new Set(sel ?? cells.map((c) => c.index));
  const toggle = (i: number) => {
    const n = new Set(selected);
    if (n.has(i)) n.delete(i); else n.add(i);
    onChange(n.size === cells.length ? null : [...n].sort((a, b) => a - b));
  };
  return (
    <div className="cell-picker">
      <div className="poses-bar">
        <b>CELLS — {selected.size}/{cells.length}</b>
        <button className="ghost" onClick={() => onChange(null)}>all</button>
        <button className="ghost" onClick={() => onChange([])}>none</button>
        <span className="muted">
          subset cells are byte-identical to the same cells of a full sweep (same seed rule)
        </span>
        <span className="spacer" />
        <button className="ghost" onClick={onClose}>✕ close</button>
      </div>
      <div className="pose-grid">
        {cells.map((c) => (
          <PoseTile key={c.index} cell={c} cacheKey={cacheKey}
                    selected={selected.has(c.index)} onClick={() => toggle(c.index)}
                    title={`${chip(c)} — click to ${selected.has(c.index) ? "exclude" : "include"}`} />
        ))}
      </div>
    </div>
  );
}
