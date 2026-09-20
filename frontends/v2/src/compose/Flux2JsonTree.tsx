// The flux.2-dev structured-JSON prompt tree as a proper column form (v1 had it as a foldout
// under a bar). Serialises to the compact JSON the dev model parses; empty fields drop out;
// pasted JSON is checked before it replaces the tree, never sent broken.
import { useEffect, useState } from "react";

import {
  emptyFlux2PromptTree, parseFlux2PromptTree, serializeFlux2PromptTree,
  type Flux2PromptSubject, type Flux2PromptTree,
} from "@loom/shared/api/orchestrator";

export function Flux2JsonTree({ value, onChange, angleDirectives }: {
  value: Flux2PromptTree;
  onChange: (t: Flux2PromptTree) => void;
  angleDirectives: Record<string, string>;
}) {
  const [rawOpen, setRawOpen] = useState(false);
  const [rawText, setRawText] = useState("");
  const [rawErr, setRawErr] = useState<string | null>(null);
  const set = (patch: Partial<Flux2PromptTree>) => onChange({ ...value, ...patch });
  const setSubject = (i: number, patch: Partial<Flux2PromptSubject>) =>
    set({ subjects: value.subjects.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const json = serializeFlux2PromptTree(value);

  useEffect(() => {
    if (rawOpen) { setRawText(json || "{\n  \n}"); setRawErr(null); }
  }, [json, rawOpen]);

  const applyRaw = () => {
    try { onChange(parseFlux2PromptTree(rawText)); setRawErr(null); }
    catch { setRawErr("That is not valid JSON, so the tree was left as it was."); }
  };

  return (
    <div className="jt">
      <div className="jt-head">
        <span className="section-title">JSON prompt</span>
        <span className="faint">{json ? `${json.length} chars` : "empty — the text prompt is used"}</span>
        <button onClick={() => onChange(emptyFlux2PromptTree())} title="clear every field">Clear</button>
      </div>
      <label className="jt-field">Scene
        <input type="text" value={value.scene ?? ""} placeholder="the setting as a whole" onChange={(e) => set({ scene: e.target.value })} />
      </label>
      <div className="jt-group">
        <span className="jt-label">Subjects</span>
        {value.subjects.map((s, i) => (
          <div key={i} className="jt-subject">
            <input type="text" value={s.description ?? ""} placeholder="who or what" onChange={(e) => setSubject(i, { description: e.target.value })} />
            <input type="text" value={s.pose ?? ""} placeholder="pose" onChange={(e) => setSubject(i, { pose: e.target.value })} />
            <input type="text" value={s.position ?? ""} placeholder="position in frame" onChange={(e) => setSubject(i, { position: e.target.value })} />
            {value.subjects.length > 1 && (
              <button title="remove this subject" onClick={() => set({ subjects: value.subjects.filter((_, j) => j !== i) })}>✕</button>
            )}
          </div>
        ))}
        <button onClick={() => set({ subjects: [...value.subjects, {}] })}>Add subject</button>
      </div>
      <div className="jt-group">
        <span className="jt-label">Camera</span>
        <div className="jt-row">
          <input type="text" value={value.camera.angle ?? ""} placeholder="angle / pose directive" onChange={(e) => set({ camera: { ...value.camera, angle: e.target.value } })} />
          <select value="" title="insert a coverage pose directive" aria-label="Pose preset"
                  onChange={(e) => { const d = e.target.value; if (d) set({ camera: { ...value.camera, angle: d } }); }}>
            <option value="">pose preset…</option>
            {Object.entries(angleDirectives).map(([k, d]) => <option key={k} value={d}>{k.replace(/_/g, " ")}</option>)}
          </select>
        </div>
        <div className="jt-row">
          <input type="text" value={value.camera.lens ?? ""} placeholder="lens, e.g. 85mm portrait" onChange={(e) => set({ camera: { ...value.camera, lens: e.target.value } })} />
          <input type="text" value={value.camera.depth_of_field ?? ""} placeholder="depth of field" onChange={(e) => set({ camera: { ...value.camera, depth_of_field: e.target.value } })} />
        </div>
      </div>
      <label className="jt-field">Lighting<input type="text" value={value.lighting ?? ""} onChange={(e) => set({ lighting: e.target.value })} /></label>
      <label className="jt-field">Style<input type="text" value={value.style ?? ""} onChange={(e) => set({ style: e.target.value })} /></label>
      <label className="jt-field">Mood<input type="text" value={value.mood ?? ""} onChange={(e) => set({ mood: e.target.value })} /></label>
      <div className="jt-group">
        <span className="jt-label">Colour palette</span>
        <div className="jt-chips">
          {value.color_palette.map((c, i) => (
            <span key={i} className="jt-chip">
              <input type="text" value={c} placeholder="name or #hex"
                     onChange={(e) => set({ color_palette: value.color_palette.map((x, j) => (j === i ? e.target.value : x)) })} />
              <button title="remove" onClick={() => set({ color_palette: value.color_palette.filter((_, j) => j !== i) })}>✕</button>
            </span>
          ))}
          <button onClick={() => set({ color_palette: [...value.color_palette, ""] })}>Add colour</button>
        </div>
      </div>
      <div className="jt-raw">
        <button onClick={() => setRawOpen((v) => !v)}>{rawOpen ? "Hide raw JSON" : "Show raw JSON"}</button>
        {rawOpen && (
          <>
            <textarea className="mono" value={rawText} spellCheck={false} rows={7} onChange={(e) => { setRawText(e.target.value); setRawErr(null); }} />
            <div className="jt-row">
              <button onClick={applyRaw}>Apply JSON to the tree</button>
              {rawErr && <span className="form-error">{rawErr}</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
