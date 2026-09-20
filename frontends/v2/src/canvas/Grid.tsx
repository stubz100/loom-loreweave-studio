// The flat grid: responsive columns from the zoom slider, uniform tiles, and the column count
// measured from the element so the keyboard moves by VISUAL row (v1 moved by a fixed 5).
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useApp } from "../store";
import { setCanvas } from "./registry";
import { Tile } from "./Tile";
import { tileKey, type CanvasModel, type Tile as TileModel } from "./tiles";

const GAP = 8;

export function useTileFlags(model: CanvasModel) {
  const selection = useApp((s) => s.selection);
  const compare = useApp((s) => s.compare);
  const bulk = useApp((s) => s.bulk);
  const stage = useApp((s) => s.stage);
  const selectedAsset = useApp((s) => s.selectedAsset);
  const pendingDelete = useApp((s) => s.pendingDelete);
  const selKey = selection ? tileKey(selection) : null;
  const cmpKey = compare ? tileKey(compare) : null;
  const marked = new Set(bulk);
  return (t: TileModel) => ({
    selected: t.key === selKey,
    compared: t.key === cmpKey,
    bulk: marked.has(t.key),
    hero: !!t.output && model.starred.has(t.output),
    kept: !!t.ref || (!!t.output && model.kept.has(t.output)),
    rejected: !!t.output && model.rejected.has(t.output),
    castable: !!selectedAsset && stage === "cast" && !model.locked,
    curable: !!selectedAsset && stage === "curate" && !model.locked,
    pendingDelete: t.key === pendingDelete,
  });
}

export function Grid({ model, assetId, versionId }: { model: CanvasModel; assetId: string | null; versionId: string | null }) {
  const zoom = useApp((s) => s.zoom);
  const fit = useApp((s) => s.fit);
  const flagsOf = useTileFlags(model);
  const ref = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(1);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setCols(Math.max(1, Math.floor((el.clientWidth + GAP) / (zoom + GAP))));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [zoom]);
  useEffect(() => { setCanvas({ order: model.tiles, cols }); }, [model.tiles, cols]);
  useEffect(() => () => setCanvas(null), []);

  return (
    <div ref={ref} className={`grid${fit === "fill" ? " fill" : ""}`} style={{ ["--tile" as string]: `${zoom}px` }}>
      {model.tiles.map((t) => <Tile key={t.key} tile={t} flags={flagsOf(t)} assetId={assetId} versionId={versionId} />)}
    </div>
  );
}
