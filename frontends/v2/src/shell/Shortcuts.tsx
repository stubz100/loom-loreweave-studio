// One keyboard dispatcher for the frame (kb-loom-ui.md §3.4): Tab hides/shows both side
// panels, 1–4 pick the stage, arrows walk the canvas by VISUAL row (the column count comes
// from the view that is on screen), k / x / space act on the selected tile, Delete twice
// deletes it, Enter opens the loupe, c pins a compare, ? shows this list, Escape closes
// what is open.
import { useEffect } from "react";

import { tileActions } from "../canvas/actions";
import { editEscape } from "../canvas/editState";
import { loupeStep } from "../canvas/Loupe";
import { getCanvas } from "../canvas/registry";
import { selectionOf, tileKey } from "../canvas/tiles";
import { useApp, type Stage } from "../store";

const STAGE_KEYS: Record<string, Stage> = { "1": "cast", "2": "expand", "3": "curate", "4": "train" };

function inField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

export function Shortcuts() {
  const helpOpen = useApp((s) => s.helpOpen);
  const setHelpOpen = useApp((s) => s.setHelpOpen);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useApp.getState();
      if (e.key === "Escape") {
        if (s.dialog) { s.setDialog(null); e.preventDefault(); return; }
        if (s.helpOpen) { s.setHelpOpen(false); e.preventDefault(); return; }
        if (s.menuOpen) { s.setMenuOpen(false); e.preventDefault(); return; }
        if (s.modelsOpen) { s.closeModels(); e.preventDefault(); return; }
        if (s.pendingDelete) { s.setPendingDelete(null); e.preventDefault(); return; }
        if (s.view === "edit") { if (!editEscape()) s.closeEdit(); e.preventDefault(); return; }
        if (s.view === "loupe") { s.closeLoupe(); e.preventDefault(); return; }
        if (s.bulk.length) { s.setBulk([]); e.preventDefault(); return; }
        if (s.selection) { s.select(null); e.preventDefault(); return; }
        return;
      }
      if (inField(e.target)) return;
      if (s.view === "edit") return;      // the painter owns the keys while it is open
      if (e.key === "Tab" && !e.ctrlKey && !e.altKey && !e.metaKey) { s.hideAllPanels(); e.preventDefault(); return; }
      if (e.key === "?") { s.setHelpOpen(!s.helpOpen); e.preventDefault(); return; }
      if (STAGE_KEYS[e.key] && s.workspace === "assets" && s.selectedAsset) { s.setStage(STAGE_KEYS[e.key]); e.preventDefault(); return; }

      // the canvas
      const ctx = getCanvas();
      const order = ctx?.order ?? [];
      const cols = ctx?.cols ?? 1;
      if (s.view === "loupe") {
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") { loupeStep(-1); e.preventDefault(); return; }
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { loupeStep(1); e.preventDefault(); return; }
        if (e.key === "c") { s.setCompare(s.compare ? null : s.selection); e.preventDefault(); return; }
        if (e.key === "Enter") { s.closeLoupe(); e.preventDefault(); return; }
      }
      if (order.length === 0) return;
      const idx = s.selection ? order.findIndex((t) => t.key === tileKey(s.selection!)) : -1;
      let next = -1;
      if (e.key === "ArrowRight") next = Math.min(idx + 1, order.length - 1);
      else if (e.key === "ArrowLeft") next = Math.max(idx - 1, 0);
      else if (e.key === "ArrowDown") next = idx < 0 ? 0 : Math.min(idx + cols, order.length - 1);
      else if (e.key === "ArrowUp") next = Math.max(idx - cols, 0);
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = order.length - 1;
      if (next !== -1 && s.view !== "loupe") {
        s.select(selectionOf(order[next]));
        document.querySelector(`[data-tile="${CSS.escape(order[next].key)}"]`)?.scrollIntoView({ block: "nearest" });
        e.preventDefault();
        return;
      }
      const cur = idx >= 0 ? order[idx] : undefined;
      if (!cur) return;
      const curable = !!s.selectedAsset && s.stage === "curate" && !cur.ref && cur.job?.status === "done";
      const version = s.assetDetail?.versions.find((v) => v.id === s.assetDetail?.profile.active_version);
      const locked = !!version?.finalized;
      if (e.key === "Enter" && s.view !== "loupe") { s.openLoupe(); e.preventDefault(); return; }
      if (e.key === "e" && cur.job?.status === "done" && !cur.ref) { s.openEdit(); e.preventDefault(); return; }
      if (e.key === " " && curable) { s.toggleBulk(cur.key); e.preventDefault(); return; }
      if (locked) return;
      if (e.key === "k" && (curable || cur.ref)) {
        const kept = !!cur.ref || (!!cur.output && !!version?.ref_set.some((r) => r.source_output === cur.output));
        void (kept ? tileActions.cull(cur) : tileActions.keep(cur));
        e.preventDefault(); return;
      }
      if (e.key === "x" && curable) {
        const rejected = !!cur.output && !!version?.rejected?.includes(cur.output);
        void tileActions.reject(cur, !rejected);
        e.preventDefault(); return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && cur.job && ["done", "failed", "canceled"].includes(cur.job.status)) {
        if (s.pendingDelete === cur.key) void tileActions.remove(cur); else s.setPendingDelete(cur.key);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!helpOpen) return null;
  return (
    <div className="help" onClick={() => setHelpOpen(false)} role="dialog" aria-label="Keyboard shortcuts">
      <div className="card" onClick={(e) => e.stopPropagation()}>
        <h2>Keyboard</h2>
        <dl>
          <dt>Tab</dt><dd>hide or show both side panels</dd>
          <dt>1 2 3 4</dt><dd>Cast, Expand, Curate, Train</dd>
          <dt>← → ↑ ↓</dt><dd>move the selection by tile and by row; Home, End</dd>
          <dt>Enter</dt><dd>open the selected tile in the loupe, and back</dd>
          <dt>c</dt><dd>in the loupe: pin this image to compare against the others</dd>
          <dt>e</dt><dd>open the selected image in Edit mode to paint an inpaint mask</dd>
          <dt>k</dt><dd>keep the selected tile into the curated set, or remove it (Curate)</dd>
          <dt>x</dt><dd>reject the selected tile, or un-reject it (Curate)</dd>
          <dt>space</dt><dd>mark the selected tile for a bulk action (Curate)</dd>
          <dt>Del</dt><dd>delete the selected image: press twice</dd>
          <dt>Esc</dt><dd>close what is open: a dialog, this list, a pending delete, Edit mode, the loupe, the marks, the selection</dd>
          <dt>?</dt><dd>this list</dd>
        </dl>
      </div>
    </div>
  );
}
