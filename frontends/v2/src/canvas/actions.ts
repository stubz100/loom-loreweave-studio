// The actions a tile offers (kb-loom-ui.md §3.4): star, keep, cull, reject, cancel, delete,
// and their bulk forms. One place, used by the tile overlays, the selection bar and the
// keyboard dispatcher alike — so a key and a click can never disagree.
import {
  cancelJob, cullRef, deleteJob, deleteOutput, keepRef, rejectOutput, starCandidate, type Job,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { tileKey, type Tile } from "./tiles";

function versionOf() {
  const s = useApp.getState();
  return s.assetDetail?.versions.find((v) => v.id === s.assetDetail?.profile.active_version) ?? null;
}

async function guarded(fn: () => Promise<unknown>, done?: string): Promise<boolean> {
  const s = useApp.getState();
  try {
    await fn();
    if (done) s.notify("ok", done);
    return true;
  } catch (e) {
    s.notify("err", reasonOf(e));
    return false;
  }
}

export const tileActions = {
  async star(t: Tile) {
    const s = useApp.getState();
    const v = versionOf();
    if (!s.selectedAsset || !t.job || !v) return;
    const current = v.casting.find((c) => c.job_id === t.job!.id && (t.output ? c.source_output === t.output : true));
    const makeHero = !(current?.starred ?? false);
    if (await guarded(() => starCandidate(s.selectedAsset!, t.job!.id, makeHero, t.output), makeHero ? "Starred as the hero." : "Hero star removed.")) await s.refreshAsset();
  },
  async keep(t: Tile) {
    const s = useApp.getState();
    if (!s.selectedAsset || !t.job) return;
    if (await guarded(() => keepRef(s.selectedAsset!, t.job!.id, t.output))) await s.refreshAsset();
  },
  async cull(t: Tile) {
    const s = useApp.getState();
    const v = versionOf();
    const refId = t.ref?.id ?? (t.output ? v?.ref_set.find((r) => r.source_output === t.output)?.id : undefined);
    if (!s.selectedAsset || !refId) return;
    if (await guarded(() => cullRef(s.selectedAsset!, refId))) {
      await s.refreshAsset();
      if (t.ref && s.selection?.refId === t.ref.id) s.select(null);   // the durable tile is gone
    }
  },
  async reject(t: Tile, flag: boolean) {
    const s = useApp.getState();
    if (!s.selectedAsset || !t.job) return;
    if (await guarded(() => rejectOutput(s.selectedAsset!, t.job!.id, t.output, flag))) await s.refreshAsset();
  },
  async cancel(t: Tile) {
    if (!t.job) return;
    await guarded(() => cancelJob(t.job!.id), "Canceled.");
  },
  /** Deletes one image of a multi-output job, else the whole generation. The caller has
   *  confirmed (two clicks or two key presses); this never asks. */
  async remove(t: Tile) {
    const s = useApp.getState();
    if (!t.job) return;
    const names = t.job.result?.output_names;
    const perImage = !!names && names.length > 1 && !!t.output;
    const ok = await guarded(() => (perImage ? deleteOutput(t.job!.id, t.output!) : deleteJob(t.job!.id)),
                             perImage ? "Image deleted; the rest of the batch stays." : "Generation deleted with its files.");
    if (!ok) return;
    const key = t.key;
    if (s.selection && tileKey(s.selection) === key) s.select(null);
    if (s.compare && tileKey(s.compare) === key) s.setCompare(null);
    s.setBulk(s.bulk.filter((k) => k !== key));
    s.setPendingDelete(null);
    await s.refreshStacks();      // a deleted image may be a stack's base or a step's output
  },
  /** Every job of an operation group (the grouped view's group delete). */
  async removeGroup(jobs: Job[]) {
    const s = useApp.getState();
    let ok = 0; const failed: string[] = [];
    for (const j of jobs) {
      try { await deleteJob(j.id); ok += 1; } catch { failed.push(j.id.slice(0, 10)); }
    }
    s.select(null); s.setBulk([]); s.setCompare(null); s.setPendingDelete(null);
    if (failed.length) s.notify("err", `${ok} deleted, ${failed.length} could not be (cancel running jobs first): ${failed.slice(0, 5).join(", ")}`);
    else s.notify("ok", `${ok} generation${ok === 1 ? "" : "s"} deleted.`);
    await s.refreshStacks();
  },
  /** Bulk keep / reject over the marked tiles: per-item isolation, one refresh at the end. */
  async bulk(action: "keep" | "reject", tiles: Tile[]) {
    const s = useApp.getState();
    if (!s.selectedAsset) return;
    const errs: string[] = [];
    for (const t of tiles) {
      if (!t.job) continue;
      try {
        if (action === "keep") await keepRef(s.selectedAsset, t.job.id, t.output);
        else await rejectOutput(s.selectedAsset, t.job.id, t.output, true);
      } catch (e) { errs.push(reasonOf(e)); }
    }
    await s.refreshAsset();
    s.setBulk([]);
    if (errs.length) s.notify("err", `${errs.length} item${errs.length === 1 ? "" : "s"} failed. First: ${errs[0]}`);
    else s.notify("ok", `${tiles.length} ${action === "keep" ? "kept" : "rejected"}.`);
  },
  async bulkDelete(tiles: Tile[]) {
    const s = useApp.getState();
    let ok = 0; const failed: string[] = [];
    for (const t of tiles) {
      if (!t.job) continue;
      const names = t.job.result?.output_names;
      const perImage = !!names && names.length > 1 && !!t.output;
      try { await (perImage ? deleteOutput(t.job.id, t.output!) : deleteJob(t.job.id)); ok += 1; }
      catch { failed.push(t.key.slice(0, 10)); }
    }
    s.select(null); s.setBulk([]); s.setCompare(null); s.setPendingDelete(null);
    if (failed.length) s.notify("err", `${ok} deleted, ${failed.length} failed: ${failed.slice(0, 5).join(", ")}`);
    else s.notify("ok", `${ok} deleted.`);
    await s.refreshStacks();
  },
};
