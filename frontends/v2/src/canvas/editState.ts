// Edit mode's working mask lives outside React so leaving the view (Escape, a stage switch)
// and coming back keeps the strokes; only a different image starts fresh. Also the one hook
// the keyboard dispatcher asks before closing the view: a lasso in progress eats Escape.
export interface EditWork {
  image: string;
  mask: HTMLCanvasElement;    // natural size, transparent = preserve, white = repaint
}

let work: EditWork | null = null;
let escapeHook: (() => boolean) | null = null;

export function getWork(image: string, w: number, h: number): HTMLCanvasElement {
  if (work && work.image === image && work.mask.width === w && work.mask.height === h) return work.mask;
  const mask = document.createElement("canvas");
  mask.width = w; mask.height = h;
  work = { image, mask };
  return mask;
}
export function dropWork(): void { work = null; }
export function setEscapeHook(fn: (() => boolean) | null): void { escapeHook = fn; }
/** True when Edit mode consumed the Escape (a lasso was cancelled). */
export function editEscape(): boolean { return escapeHook ? escapeHook() : false; }
