// The Loupe (kb-loom-ui.md §3.4, D7): replaces the grid, keeps the inspector live. Prev / next
// walk the view's tile order, a click toggles 1:1, the facts strip names the image, and
// Compare pins one image on the left while prev / next move the right one — the review
// loop is "zoom, compare, decide".
import { useEffect, useState } from "react";

import { useApp } from "../store";
import { getCanvas } from "./registry";
import { coverageOf, imageUrl, selectionOf, tileKey, type CanvasModel, type Tile } from "./tiles";
import { nice } from "../lib/coverage";

function facts(t: Tile): string {
  const j = t.job;
  const cov = coverageOf(t);
  const ometa = t.output ? j?.result?.output_meta?.[t.output] : undefined;
  const parts = [
    t.ref ? "curated ref" : j ? `${j.pass ?? j.pipeline}${j.params.model_name ? ` · ${String(j.params.model_name)}` : ""}` : "",
    ometa?.seed != null ? `seed ${ometa.seed}` : j?.result?.seed != null ? `seed ${j.result.seed}` : "",
    cov ? `${nice(cov.shot_size)}, ${nice(cov.angle)}, ${nice(cov.expression)}` : "",
    t.output ?? t.ref?.file ?? "",
  ].filter(Boolean);
  return parts.join("  ·  ");
}

export function Loupe({ model, assetId, versionId }: { model: CanvasModel; assetId: string | null; versionId: string | null }) {
  const selection = useApp((s) => s.selection);
  const compare = useApp((s) => s.compare);
  const select = useApp((s) => s.select);
  const setCompare = useApp((s) => s.setCompare);
  const closeLoupe = useApp((s) => s.closeLoupe);
  const [one, setOne] = useState(false);   // 1:1

  const order = model.tiles;
  const idx = selection ? order.findIndex((t) => t.key === tileKey(selection)) : -1;
  const cur = idx >= 0 ? order[idx] : null;
  const pinned = compare ? order.find((t) => t.key === tileKey(compare)) ?? null : null;
  const go = (d: number) => { const n = idx + d; if (n >= 0 && n < order.length) select(selectionOf(order[n])); };
  useEffect(() => { if (!cur) closeLoupe(); }, [cur, closeLoupe]);
  if (!cur) return null;

  const pane = (t: Tile, label: string) => {
    const src = imageUrl(t, assetId, versionId);
    return (
      <figure className={`loupe-pane${one ? " one" : ""}`} onClick={() => setOne((v) => !v)} title={one ? "click: fit" : "click: 1:1"}>
        {src ? <img src={src} alt="" draggable={false} /> : <div className="empty"><p>No image for this tile.</p></div>}
        <figcaption>{label ? `${label}: ` : ""}{facts(t)}</figcaption>
      </figure>
    );
  };

  return (
    <div className="loupe">
      <div className="loupe-bar">
        <button onClick={closeLoupe} title="back to the grid (Esc)">← Grid</button>
        <button onClick={() => go(-1)} disabled={idx <= 0} title="previous (←)">‹</button>
        <span className="muted">{idx + 1} of {order.length}</span>
        <button onClick={() => go(1)} disabled={idx >= order.length - 1} title="next (→)">›</button>
        <span className="spacer" />
        <button className={pinned ? "on" : ""} onClick={() => setCompare(pinned ? null : selectionOf(cur))}
                title={pinned ? "unpin the compared image (c)" : "pin this image on the left and walk the others beside it (c)"}>
          {pinned ? "Unpin" : "Compare"}
        </button>
        <button onClick={() => setOne((v) => !v)} className={one ? "on" : ""} title="1:1 pixels">1:1</button>
      </div>
      <div className={`loupe-body${pinned ? " two" : ""}`}>
        {pinned && pane(pinned, "pinned")}
        {pane(cur, pinned ? "current" : "")}
      </div>
    </div>
  );
}

/** Prev/next for the keyboard dispatcher, in the current view's order. */
export function loupeStep(d: number): void {
  const s = useApp.getState();
  const ctx = getCanvas();
  if (!ctx || !s.selection) return;
  const idx = ctx.order.findIndex((t) => t.key === tileKey(s.selection!));
  const n = idx + d;
  if (n >= 0 && n < ctx.order.length) s.select(selectionOf(ctx.order[n]));
}
