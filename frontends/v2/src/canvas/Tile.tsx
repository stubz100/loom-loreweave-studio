// One tile (kb-loom-ui.md §3.4): a uniform box (fit or fill), the image or its status, and
// the affordances — visible on hover AND on the selected tile, keyboard-reachable, and the
// destructive one (Delete) always a second click away. The same component in every view.
import { memo } from "react";

import { useApp } from "../store";
import { tileActions } from "./actions";
import { coverageOf, imageUrl, isVideo, selectionOf, type Tile as TileModel } from "./tiles";
import { nice } from "../lib/coverage";

export interface TileFlags {
  selected: boolean;
  compared: boolean;
  bulk: boolean;
  hero: boolean;
  kept: boolean;
  rejected: boolean;
  castable: boolean;      // Cast stage of a character: the star is offered
  curable: boolean;       // Curate stage, unlocked: keep / reject / bulk are offered
  pendingDelete: boolean;
}

export const Tile = memo(function Tile({ tile, flags, assetId, versionId }: {
  tile: TileModel; flags: TileFlags; assetId: string | null; versionId: string | null;
}) {
  const select = useApp((s) => s.select);
  const toggleBulk = useApp((s) => s.toggleBulk);
  const setPendingDelete = useApp((s) => s.setPendingDelete);
  const openLoupe = useApp((s) => s.openLoupe);
  const offline = useApp((s) => s.offline);

  const job = tile.job;
  const status = tile.ref ? "done" : job?.status ?? "queued";
  const name = tile.output ?? job?.result?.output_name;
  const video = isVideo(name);
  const src = imageUrl(tile, assetId, versionId);
  const active = !tile.ref && (status === "queued" || status === "running");
  const terminal = status === "done" || status === "failed" || status === "canceled";
  const done = status === "done";
  const passes = (job?.post_passes ?? []).map((p) => (p as { pass?: string }).pass ?? "pass");
  const cov = coverageOf(tile);
  const curable = flags.curable && (done || !!tile.ref) && !video;
  const cap = tile.ref ? "curated ref" : job ? (job.pass ?? job.pipeline) + (cov ? `, ${nice(cov.angle)} ${nice(cov.shot_size)}` : "") : "";

  const cls = ["tile",
    flags.selected ? "selected" : "", flags.compared ? "compared" : "", flags.bulk ? "marked" : "",
    flags.hero ? "hero" : "", flags.kept ? "kept" : "", flags.rejected ? "rejected" : "",
    tile.interim ? "interim" : "", `s-${status}`].filter(Boolean).join(" ");

  return (
    <div className={cls} data-tile={tile.key} role="button" tabIndex={-1} aria-selected={flags.selected}
         onClick={() => select(flags.selected ? null : selectionOf(tile))}
         onDoubleClick={() => { if (src) { select(selectionOf(tile)); openLoupe(); } }}
         title={name ?? job?.id}>
      {src ? <img src={src} alt="" loading="lazy" draggable={false} /> : (
        <span className="tile-status">
          {status === "queued" && "queued"}
          {status === "running" && (tile.interim ? "" : `generating ${Math.round((job?.progress ?? 0) * 100)}%`)}
          {status === "failed" && "failed"}
          {status === "canceled" && "canceled"}
          {status === "done" && (video ? "video sketch: its frames follow" : "no image")}
        </span>
      )}
      {status === "running" && !tile.interim && <div className="tile-progress"><div style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} /></div>}
      {cap && <span className="cap">{cap}{passes.length && done ? ` · then ${passes.join(", ")}` : ""}</span>}
      {flags.hero && <span className="badge hero-badge" title="the hero">★</span>}
      {flags.kept && <span className="badge kept-badge" title="in the curated set">✓</span>}
      {flags.rejected && <span className="badge rejected-badge" title="rejected">✕</span>}
      <div className="acts" onClick={(e) => e.stopPropagation()}>
        {flags.castable && done && !tile.ref && (
          <button className={flags.hero ? "on" : ""} onClick={() => void tileActions.star(tile)} disabled={offline} title={flags.hero ? "remove the hero star" : "star as the hero"}>{flags.hero ? "★" : "☆"}</button>
        )}
        {curable && (
          <button className={flags.kept ? "on" : ""} onClick={() => void (flags.kept ? tileActions.cull(tile) : tileActions.keep(tile))} disabled={offline}
                  title={flags.kept ? "remove from the curated set" : "keep into the curated set (k)"}>{flags.kept ? "✓" : "+"}</button>
        )}
        {curable && !tile.ref && !flags.kept && (
          <button className={flags.rejected ? "on" : ""} onClick={() => void tileActions.reject(tile, !flags.rejected)} disabled={offline}
                  title={flags.rejected ? "un-reject" : "reject (x)"}>{flags.rejected ? "↩" : "✕"}</button>
        )}
        {curable && !tile.ref && (
          <button className={flags.bulk ? "on" : ""} onClick={() => toggleBulk(tile.key)} title="mark for a bulk action (space)">{flags.bulk ? "■" : "□"}</button>
        )}
        {src && <button onClick={() => { select(selectionOf(tile)); openLoupe(); }} title="open in the loupe (Enter)">⤢</button>}
        {active && !tile.interim && <button onClick={() => void tileActions.cancel(tile)} disabled={offline} title="cancel this job">Cancel</button>}
        {terminal && !tile.ref && !flags.kept && (
          <button className={flags.pendingDelete ? "danger" : ""} onBlur={() => { if (flags.pendingDelete) setPendingDelete(null); }}
                  onClick={() => { if (flags.pendingDelete) void tileActions.remove(tile); else setPendingDelete(tile.key); }} disabled={offline}
                  title={flags.pendingDelete ? "click again to delete" : "delete this image (Del, twice)"}>{flags.pendingDelete ? "Delete?" : "🗑"}</button>
        )}
      </div>
    </div>
  );
});
