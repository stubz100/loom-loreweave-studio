// The Captions view (kb-loom-ui.md §3.6, migration step 6): one row per curated ref with the
// image next to its caption — thumbnail, pose, the caption text, an origin badge, the missing-
// trigger warning, Save / Reset per row, Reset all on a second click. Per-ref editing is
// canvas work: it needs width and the image beside the text. The Curate filters apply.
import { useCallback, useEffect, useState } from "react";

import {
  clearCaptionOverride, getCaptions, refUrl, setCaptionOverride, type CaptionsResponse,
} from "@loom/shared/api/orchestrator";

import { nice } from "../lib/coverage";
import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { setCanvas } from "./registry";

export function Captions() {
  const assetId = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const offline = useApp((s) => s.offline);
  const filters = useApp((s) => s.filters);
  const notify = useApp((s) => s.notify);
  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const versionId = version?.id ?? null;
  const [caps, setCaps] = useState<CaptionsResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);

  useEffect(() => { setCanvas(null); }, []);   // the arrow keys have no tiles to walk here
  const load = useCallback(async () => {
    if (!assetId || !versionId) return;
    try { setCaps(await getCaptions(assetId, versionId)); setDrafts({}); setProblem(null); }
    catch (e) { setProblem(reasonOf(e)); }
  }, [assetId, versionId]);
  useEffect(() => { setCaps(null); if (!offline && version && version.ref_set.length > 0) void load(); }, [load, offline, version?.ref_set.length]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!assetId || !version) return <div className="empty"><h2>Captions</h2><p>Select a character.</p></div>;
  if (version.ref_set.length === 0) return <div className="empty"><h2>No captions yet</h2><p>Captions are written for the curated refs. Keep cells in Curate first.</p></div>;

  const rows = (caps?.captions ?? []).filter((c) => {
    const cell = c.coverage_cell ?? {};
    if (filters.shot && cell.shot_size !== filters.shot) return false;
    if (filters.angle && cell.angle !== filters.angle) return false;
    if (filters.expression && cell.expression !== filters.expression) return false;
    return true;
  });
  const locked = version.finalized;
  const save = async (id: string) => {
    const text = (drafts[id] ?? "").trim();
    if (!text) return;
    setBusy(id); setProblem(null);
    try { await setCaptionOverride(assetId, id, text, version.id); await load(); }
    catch (e) { setProblem(reasonOf(e)); } finally { setBusy(null); }
  };
  const reset = async (id?: string) => {
    if (!id && !confirmAll) { setConfirmAll(true); return; }
    setConfirmAll(false);
    setBusy(id ?? "all"); setProblem(null);
    try { const r = await clearCaptionOverride(assetId, id, version.id); await load(); if (!id) notify("ok", `${r.cleared} caption${r.cleared === 1 ? "" : "s"} back to the template.`); }
    catch (e) { setProblem(reasonOf(e)); } finally { setBusy(null); }
  };

  return (
    <div className="captions">
      <div className="captions-head">
        <span className="muted">Template: trigger, angle, shot, expression, background. Trigger <b className="mono">{caps?.trigger_token ?? version.trigger_token ?? "…"}</b>. An edit is a durable override on this version; the next staging bakes it in.</span>
        <span className="spacer" />
        {caps && <span className="faint">{rows.length} of {caps.count}{caps.edited_count ? `, ${caps.edited_count} edited` : ""}</span>}
        {caps && caps.edited_count > 0 && !locked && (
          <button className={confirmAll ? "danger" : ""} onBlur={() => setConfirmAll(false)} onClick={() => void reset()} disabled={busy !== null || offline}>{confirmAll ? "Reset every caption?" : "Reset all"}</button>
        )}
      </div>
      {problem && <p className="form-error" role="alert">{problem}</p>}
      {!caps && !problem && <p className="faint">Loading…</p>}
      {rows.map((c) => {
        const draft = drafts[c.id];
        const dirty = draft !== undefined && draft.trim() !== c.caption;
        const cell = c.coverage_cell ?? {};
        return (
          <div key={c.id} className={`cap-row${c.origin === "edited" ? " edited" : ""}`}>
            <img src={refUrl(assetId, c.file, version.id)} alt="" loading="lazy" />
            <div className="cap-main">
              <div className="cap-meta">
                <span>{nice(cell.shot_size)}, {nice(cell.angle)}, {nice(cell.expression)}{cell.background ? `, ${nice(cell.background)}` : ""}</span>
                <span className={`pill${c.origin === "edited" ? " amber" : ""}`}>{c.origin === "edited" ? "edited" : "template"}</span>
                {!c.has_trigger && <span className="pill warn" title="the trigger token is missing; the adapter may not bind to it">no trigger</span>}
                <span className="faint mono">{c.file}</span>
              </div>
              <textarea rows={2} value={draft ?? c.caption} disabled={locked || busy === c.id} spellCheck={false}
                        onChange={(e) => setDrafts({ ...drafts, [c.id]: e.target.value })} />
              {c.origin === "edited" && <div className="faint">Template was: {c.template_caption}</div>}
            </div>
            <div className="cap-acts">
              {dirty && !locked && <button className="primary" onClick={() => void save(c.id)} disabled={busy !== null || offline || !(draft ?? "").trim()}>Save</button>}
              {dirty && <button onClick={() => setDrafts(({ [c.id]: _, ...rest }) => rest)}>Discard</button>}
              {c.origin === "edited" && !locked && !dirty && <button onClick={() => void reset(c.id)} disabled={busy !== null || offline} title="back to the template text">Reset</button>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
