// Edit mode (kb-loom-ui.md §3.7, migration step 7): the selected image fills the canvas, a
// mask is painted ON it — brush, eraser, lasso, invert, feather, from the matte — and Save
// writes it as a PNG into out/ (white = repaint, black = preserve) for the Inpaint pass in
// the Post tab. The first canvas-layer component; L3's compositor reuses the idea.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { outputUrl, saveMask } from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { dropWork, getWork, setEscapeHook } from "./editState";
import { setCanvas } from "./registry";

type Tool = "brush" | "eraser" | "lasso";

export function Edit({ image }: { image: string }) {
  const jobs = useApp((s) => s.jobs);
  const offline = useApp((s) => s.offline);
  const notify = useApp((s) => s.notify);
  const addMask = useApp((s) => s.addMask);
  const closeEdit = useApp((s) => s.closeEdit);
  const toggleInspector = useApp((s) => s.toggleInspector);

  const boxRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const maskRef = useRef<HTMLCanvasElement | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const [scale, setScale] = useState(1);
  const [tool, setTool] = useState<Tool>("brush");
  const [size, setSize] = useState(48);
  const [feather, setFeather] = useState(0);
  const [lasso, setLasso] = useState<{ x: number; y: number }[]>([]);
  const [saving, setSaving] = useState(false);
  const [painted, setPainted] = useState(false);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const cursor = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => { setCanvas(null); }, []);

  // A finished matte of this image (BiRefNet: the *_bgmask output, white = background).
  const matteName = (() => {
    const base = image.split("/").pop() ?? image;
    for (const j of Object.values(jobs)) {
      if (j.pipeline !== "birefnet" || j.status !== "done") continue;
      const input = String(j.params.input_image ?? "").replace(/\\/g, "/");
      if (!input.endsWith("/" + base)) continue;
      const names = j.result?.output_names ?? [];
      const m = names.find((n) => j.result?.output_meta?.[n]?.role === "bgmask");
      if (m) return m;
    }
    return null;
  })();

  const redraw = useCallback(() => {
    const ov = overlayRef.current; const mask = maskRef.current;
    if (!ov || !mask) return;
    const ctx = ov.getContext("2d")!;
    ctx.clearRect(0, 0, ov.width, ov.height);
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(mask, 0, 0);
    ctx.globalCompositeOperation = "source-in";
    ctx.fillStyle = "rgba(224, 169, 76, 0.55)";
    ctx.fillRect(0, 0, ov.width, ov.height);
    ctx.globalCompositeOperation = "source-over";
    if (lasso.length) {
      ctx.strokeStyle = "#e0a94c"; ctx.lineWidth = 2 / scale; ctx.setLineDash([6 / scale, 4 / scale]);
      ctx.beginPath(); lasso.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.stroke(); ctx.setLineDash([]);
    }
    if (cursor.current && tool !== "lasso") {
      ctx.strokeStyle = tool === "eraser" ? "#d9534f" : "#ffffff"; ctx.lineWidth = 1.5 / scale;
      ctx.beginPath(); ctx.arc(cursor.current.x, cursor.current.y, size / 2, 0, Math.PI * 2); ctx.stroke();
    }
  }, [lasso, scale, size, tool]);
  useEffect(() => { redraw(); }, [redraw, dims]);

  const onLoad = () => {
    const im = imgRef.current!;
    const w = im.naturalWidth, h = im.naturalHeight;
    maskRef.current = getWork(image, w, h);
    setDims({ w, h });
    setPainted(hasStrokes(maskRef.current));
  };
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el || !dims) return;
    const measure = () => setScale(Math.min((el.clientWidth - 4) / dims.w, (el.clientHeight - 4) / dims.h, 1));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [dims]);

  const toCanvas = (e: React.PointerEvent) => {
    const r = overlayRef.current!.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width * (dims?.w ?? 1), y: (e.clientY - r.top) / r.height * (dims?.h ?? 1) };
  };
  const stroke = (from: { x: number; y: number } | null, to: { x: number; y: number }) => {
    const ctx = maskRef.current!.getContext("2d")!;
    ctx.globalCompositeOperation = tool === "eraser" ? "destination-out" : "source-over";
    ctx.strokeStyle = "#fff"; ctx.fillStyle = "#fff"; ctx.lineWidth = size; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath();
    if (from) { ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke(); }
    else { ctx.arc(to.x, to.y, size / 2, 0, Math.PI * 2); ctx.fill(); }
    ctx.globalCompositeOperation = "source-over";
    setPainted(true);
  };
  const onDown = (e: React.PointerEvent) => {
    if (!dims) return;
    const p = toCanvas(e);
    if (tool === "lasso") { setLasso((pts) => [...pts, p]); return; }
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drawing.current = true; last.current = p; stroke(null, p); redraw();
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dims) return;
    const p = toCanvas(e);
    cursor.current = p;
    if (drawing.current && tool !== "lasso") { stroke(last.current, p); last.current = p; }
    redraw();
  };
  const onUp = () => { drawing.current = false; last.current = null; };
  const closeLasso = () => {
    if (lasso.length < 3) { setLasso([]); return; }
    const ctx = maskRef.current!.getContext("2d")!;
    ctx.globalCompositeOperation = "source-over"; ctx.fillStyle = "#fff";
    ctx.beginPath(); lasso.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.closePath(); ctx.fill();
    setLasso([]); setPainted(true);
  };
  useEffect(() => {
    setEscapeHook(lasso.length ? () => { setLasso([]); return true; } : null);
    return () => setEscapeHook(null);
  }, [lasso.length]);

  const invert = () => {
    const mask = maskRef.current!;
    const tmp = document.createElement("canvas"); tmp.width = mask.width; tmp.height = mask.height;
    const t = tmp.getContext("2d")!;
    t.fillStyle = "#fff"; t.fillRect(0, 0, tmp.width, tmp.height);
    t.globalCompositeOperation = "destination-out"; t.drawImage(mask, 0, 0);
    const m = mask.getContext("2d")!;
    m.globalCompositeOperation = "source-over"; m.clearRect(0, 0, mask.width, mask.height); m.drawImage(tmp, 0, 0);
    setPainted(true); redraw();
  };
  const clear = () => { const mask = maskRef.current!; mask.getContext("2d")!.clearRect(0, 0, mask.width, mask.height); setPainted(false); redraw(); };
  const fromMatte = () => {
    if (!matteName || !maskRef.current) return;
    const im = new Image();
    im.crossOrigin = "anonymous";
    im.onload = () => {
      try {
        const mask = maskRef.current!;
        const tmp = document.createElement("canvas"); tmp.width = mask.width; tmp.height = mask.height;
        const t = tmp.getContext("2d")!;
        t.drawImage(im, 0, 0, mask.width, mask.height);
        const d = t.getImageData(0, 0, mask.width, mask.height);
        for (let i = 0; i < d.data.length; i += 4) { d.data[i + 3] = d.data[i]; d.data[i] = 255; d.data[i + 1] = 255; d.data[i + 2] = 255; }
        mask.getContext("2d")!.putImageData(d, 0, 0);
        setPainted(true); redraw();
        notify("ok", "Matte loaded: the background is masked. Invert to mask the subject.");
      } catch (e) { notify("err", `The matte could not be read: ${reasonOf(e)}`); }
    };
    im.onerror = () => notify("err", "The matte image did not load.");
    im.src = outputUrl(matteName);
  };
  const save = async () => {
    const mask = maskRef.current!;
    const out = document.createElement("canvas"); out.width = mask.width; out.height = mask.height;
    const o = out.getContext("2d")!;
    o.fillStyle = "#000"; o.fillRect(0, 0, out.width, out.height);
    if (feather > 0) o.filter = `blur(${feather}px)`;
    o.drawImage(mask, 0, 0);
    const b64 = out.toDataURL("image/png").split(",")[1];
    setSaving(true);
    try {
      const r = await saveMask(image, b64);
      addMask(image, r.mask);
      notify("ok", `Mask saved as ${r.mask}. Add the Inpaint pass in the Post tab.`);
      dropWork();
      closeEdit();
      toggleInspector("post");
    } catch (e) { notify("err", reasonOf(e)); } finally { setSaving(false); }
  };

  return (
    <div className="edit">
      <div className="edit-rail" aria-label="Mask tools">
        <button className={tool === "brush" ? "on" : ""} onClick={() => setTool("brush")} title="paint the area to repaint (b)">Brush</button>
        <button className={tool === "eraser" ? "on" : ""} onClick={() => setTool("eraser")} title="erase from the mask">Eraser</button>
        <button className={tool === "lasso" ? "on" : ""} onClick={() => setTool("lasso")} title="click corners, double-click to close and fill">Lasso</button>
        {tool === "lasso" && lasso.length > 0 && <button onClick={closeLasso}>Close ({lasso.length})</button>}
        <label className="p-field">Size {size}<input type="range" min={4} max={240} value={size} onChange={(e) => setSize(Number(e.target.value))} /></label>
        <label className="p-field">Feather {feather}<input type="range" min={0} max={64} value={feather} onChange={(e) => setFeather(Number(e.target.value))} /></label>
        <button onClick={invert} disabled={!dims} title="swap repaint and preserve">Invert</button>
        <button onClick={clear} disabled={!painted}>Clear</button>
        <button onClick={fromMatte} disabled={!matteName} title={matteName ? "start from the BiRefNet matte of this image (background masked)" : "no matte for this image; Matte hero in Expand makes one for the hero"}>From matte</button>
        <span className="spacer" />
        <p className="faint">White = repaint, black = keep. The mask is saved beside the outputs; the Post tab's Inpaint pass reads it.</p>
        <button className="primary" onClick={() => void save()} disabled={!painted || saving || offline}>{saving ? "Saving…" : "Save mask"}</button>
        <button onClick={closeEdit}>Back (Esc)</button>
      </div>
      <div ref={boxRef} className="edit-box">
        <div className="edit-stage" style={dims ? { width: dims.w * scale, height: dims.h * scale } : undefined}>
          <img ref={imgRef} src={outputUrl(image)} alt="" crossOrigin="anonymous" onLoad={onLoad} draggable={false} />
          {dims && (
            <canvas ref={overlayRef} width={dims.w} height={dims.h} className={`edit-overlay tool-${tool}`}
                    onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={() => { cursor.current = null; onUp(); redraw(); }}
                    onDoubleClick={() => { if (tool === "lasso") closeLasso(); }} />
          )}
        </div>
      </div>
    </div>
  );
}

function hasStrokes(c: HTMLCanvasElement): boolean {
  try {
    const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    for (let i = 3; i < d.length; i += 4 * 97) if (d[i] > 0) return true;
  } catch { /* fresh canvas */ }
  return false;
}
