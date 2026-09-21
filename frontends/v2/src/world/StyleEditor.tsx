// The style editor (kb-loom-ui.md §3.8): the style the Panel points at — name, the style
// prompt, the global negative, Save / Revert, Set as default, Delete on a second click — and
// its sample: a preview prompt, a model, Generate (a project-scoped t2i with this style
// applied, watched through the one poller) or Cancel, the thumbnail with Delete. The
// "apply by default" gate lives here with the rest of style authoring.
import { useEffect, useState } from "react";

import {
  cancelJob, clearStyleSample, deleteStyle, generate, setActiveStyle, setStyle, setStyleSample, styleSampleUrl,
  updateStyle,
} from "@loom/shared/api/orchestrator";

import { useCompose } from "../compose/composeStore";
import { reasonOf } from "../lib/project";
import { useApp } from "../store";

export function StyleEditor() {
  const styles = useCompose((s) => s.styles);
  const loadStyles = useCompose((s) => s.loadStyles);
  const styleSel = useApp((s) => s.styleSel);
  const setStyleSel = useApp((s) => s.setStyleSel);
  const jobs = useApp((s) => s.jobs);
  const offline = useApp((s) => s.offline);
  const notify = useApp((s) => s.notify);
  const project = useApp((s) => s.project);

  const st = styles?.styles.find((s) => s.id === (styleSel ?? styles.active_style_id)) ?? null;
  const [name, setName] = useState("");
  const [fragment, setFragment] = useState("");
  const [neg, setNeg] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sd3.5-medium");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [sampleJob, setSampleJob] = useState<{ styleId: string; jobId: string } | null>(null);

  useEffect(() => {
    if (!st) return;
    setName(st.name); setFragment(st.fragment); setNeg(st.global_negative ?? "");
    setPrompt(st.sample?.prompt ?? ""); setModel(st.sample?.model ?? "sd3.5-medium");
  }, [st?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (project?.id && !styles) void loadStyles(); }, [project?.id, styles, loadStyles]);

  // The sample job closes through the poller: done → pin its output as the style's sample.
  useEffect(() => {
    if (!sampleJob) return;
    const j = jobs[sampleJob.jobId];
    if (!j) { setSampleJob(null); return; }
    if (j.status === "queued" || j.status === "running") return;
    setSampleJob(null);
    if (j.status === "done") {
      const out = j.result?.output_name ?? j.result?.output_names?.[0];
      if (!out) return;
      setStyleSample(sampleJob.styleId, { job_id: j.id, output: out, prompt: (j.params?.prompt as string) ?? null, model: (j.params?.model_name as string) ?? null })
        .then(() => loadStyles()).then(() => notify("ok", "Sample pinned as the style's thumbnail."))
        .catch((e) => notify("err", reasonOf(e)));
    } else if (j.status === "failed") {
      notify("err", `The sample failed: ${j.result?.error ?? "see the dock"}`);
    }
  }, [jobs, sampleJob, loadStyles, notify]);

  if (!styles) return <p className="faint">Loading styles…</p>;
  if (!st) return <div className="empty"><h2>Visual styles</h2><p>Add a style in the Panel, or pick one to edit it here.</p></div>;

  const dirty = name !== st.name || fragment !== st.fragment || neg !== (st.global_negative ?? "");
  const isActive = styles.active_style_id === st.id;
  const thumb = st.sample?.file ? styleSampleUrl(st.id, st.sample.set_at ?? undefined) : null;
  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key); setConfirm(null);
    try { await fn(); await loadStyles(); if (done) notify("ok", done); }
    catch (e) { notify("err", reasonOf(e)); }
    finally { setBusy(null); }
  };
  const twice = (key: string, label: string, armed: string, fn: () => Promise<unknown>, done?: string, disabled = false) => (
    <button className={confirm === key ? "danger" : ""} disabled={disabled || busy !== null || offline} onBlur={() => { if (confirm === key) setConfirm(null); }}
            onClick={() => { if (confirm === key) void run(key, fn, done); else setConfirm(key); }}>{confirm === key ? armed : label}</button>
  );
  const onGenerate = async () => {
    if (!prompt.trim()) { notify("warn", "Write a preview prompt for the sample."); return; }
    setBusy("sample");
    try {
      const res = await generate({ pipeline: model === "zimage-turbo" ? "zimage" : "sd35", mode: "t2i", model_name: model, prompt: prompt.trim(), apply_style: true, style_id: st.id, count: 1 });
      const jid = res.job_ids?.[0];
      if (jid) { setSampleJob({ styleId: st.id, jobId: jid }); notify("ok", "Sample queued; it becomes the thumbnail when done."); }
    } catch (e) { notify("err", reasonOf(e)); } finally { setBusy(null); }
  };
  const onCancel = async () => {
    if (!sampleJob) return;
    const jid = sampleJob.jobId; setSampleJob(null);
    try { await cancelJob(jid); } catch { /* already terminal */ }
  };

  return (
    <div className="editor">
      <div className="editor-head">
        <input className="editor-title" value={name} onChange={(e) => setName(e.target.value)} placeholder="style name" aria-label="Style name" />
        {isActive ? <span className="pill amber">project default</span>
                  : <button onClick={() => void run("default", () => setActiveStyle(st.id), `${st.name} is the project default.`)} disabled={busy !== null || offline} title="used when a generation does not pick a style">Set as default</button>}
        <span className="spacer" />
        <button onClick={() => { setName(st.name); setFragment(st.fragment); setNeg(st.global_negative ?? ""); }} disabled={!dirty}>Revert</button>
        <button className="primary" onClick={() => void run("save", () => updateStyle(st.id, { name: name.trim() || st.name, fragment, global_negative: neg }), "Style saved.")} disabled={!dirty || busy !== null || offline}>Save</button>
        {twice("delete", "Delete", "Delete this style?", async () => { await deleteStyle(st.id); setStyleSel(null); }, "Style deleted.", styles.styles.length <= 1)}
      </div>
      <label className="p-flag" title="apply the project default style to new generations unless a generation says otherwise (R104)">
        <input type="checkbox" checked={styles.enabled_default} onChange={(e) => void run("gate", () => setStyle(undefined, e.target.checked), e.target.checked ? "New generations apply the default style." : "New generations start without a style.")} disabled={busy !== null || offline} />
        Apply the default style to new generations
      </label>
      <div className="section-title">Style prompt</div>
      <textarea rows={6} value={fragment} onChange={(e) => setFragment(e.target.value)} placeholder="appended after the character prompt: the look, the medium, the palette" spellCheck={false} />
      <div className="section-title">Negative prompt</div>
      <textarea rows={2} value={neg} onChange={(e) => setNeg(e.target.value)} placeholder="appended to every negative prompt" spellCheck={false} />

      <div className="section-title">Sample</div>
      <div className="sample">
        <div className="sample-img">
          {sampleJob ? (
            <div className="hero-empty" style={{ width: 200, height: 200 }}>{jobs[sampleJob.jobId]?.status === "running" ? `generating ${Math.round((jobs[sampleJob.jobId]?.progress ?? 0) * 100)}%` : "queued"}</div>
          ) : thumb ? <img src={thumb} alt="" /> : <div className="hero-empty" style={{ width: 200, height: 200 }}>no sample yet</div>}
        </div>
        <div className="sample-form">
          <p className="faint">A project-scoped generation with this style applied. The result becomes the thumbnail the Panel and the composer show.</p>
          <label className="p-field">Preview prompt<input type="text" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="a knight portrait, three-quarter view" /></label>
          <label className="p-field">Model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="sd3.5-medium">sd3.5-medium</option>
              <option value="zimage-turbo">zimage-turbo</option>
            </select>
          </label>
          <div className="jt-row">
            {thumb && !sampleJob && twice("sample-del", "Delete sample", "Delete the sample?", () => clearStyleSample(st.id), "Sample deleted.")}
            <span className="spacer" />
            {sampleJob ? <button onClick={() => void onCancel()}>Cancel</button>
                       : <button className="primary" onClick={() => void onGenerate()} disabled={busy !== null || offline || !prompt.trim()}>Generate sample</button>}
          </div>
        </div>
      </div>
    </div>
  );
}
