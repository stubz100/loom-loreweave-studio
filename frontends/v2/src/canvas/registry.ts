// The keyboard's view of the canvas: the visible tiles in their visual order and the column
// count, registered by whichever view is on screen, read by the one dispatcher (Shortcuts).
// Module state on purpose — it changes every render and belongs to no persisted store.
import type { Tile } from "./tiles";

export interface CanvasCtx {
  order: Tile[];
  cols: number;
}

let current: CanvasCtx | null = null;

export function setCanvas(ctx: CanvasCtx | null): void { current = ctx; }
export function getCanvas(): CanvasCtx | null { return current; }
