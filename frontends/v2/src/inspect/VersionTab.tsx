// Inspector · Version (kb-loom-ui.md §3.5): what the active version is — prompt template,
// trigger token, anchor, adapter, curation counts, caption state — plus its actions (new
// version, switch, finalize / unlock). v1 scattered these across the curate bar and the
// TrainPanel head. The version is the selection when no tile is.
import { useEffect, useState } from "react";

import {
  activateVersion, anchorUrl, castingUrl, clearAnchor, createVersion, finalizeVersion, getCaptions, saveProfile,
  unfinalizeVersion, type CaptionsResponse,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

export function VersionTab() {
  const assetId = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const offline = useApp((s) => s.offline);
  const refreshAsset = useApp((s) => s.refreshAsset);
  const notify = useApp((s) => s.notify);
  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;

  const [template, setTemplate] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [newName, setNewName] = useState<string | null>(null);   // null = the field is closed
  const [captions, setCaptions] = useState<CaptionsResponse | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => { setTemplate(version?.prompt_template ?? ""); }, [version?.id, version?.prompt_template]);
  useEffect(() => {
    setCaptions(null);
    if (!assetId || !version || offline || version.ref_set.length === 0) return;
    let alive = true;
    getCaptions(assetId, version.id).then((c) => { if (alive) setCaptions(c); }).catch(() => { /* advisory */ });
    return () => { alive = false; };
  }, [assetId, version?.id, version?.ref_set.length, offline]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!assetId || !detail || !version) {
    return (
      <>
        <div className="section-title">Version</div>
        <p className="muted">Select a character in the Library. Its active version shows here: prompt template, anchor, adapter and curation state.</p>
      </>
    );
  }

  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key); setProblem(null);
    try {
      await fn();
      await refreshAsset();
      if (done) notify("ok", done);
    } catch (e) { setProblem(reasonOf(e)); } finally { setBusy(null); }
  };
  const hero = version.casting.find((c) => c.starred) ?? null;
  const dirty = template !== (version.prompt_template ?? "");
  const anchorVerified = !!version.anchor?.verified_at;

  return (
    <>
      <div className="section-title">{detail.profile.name}</div>
      <div className="jt-row">
        <select value={version.id} onChange={(e) => void run("switch", () => activateVersion(assetId, e.target.value))} disabled={busy !== null || offline} aria-label="Active version">
          {detail.versions.map((v) => <option key={v.id} value={v.id}>{v.name}{v.lora ? " ✨" : ""}{v.finalized ? " 🔒" : ""}</option>)}
        </select>
        <button onClick={() => setNewName(newName === null ? "" : null)} disabled={busy !== null || offline} title="copy the active version into a fresh, unlocked one">New</button>
      </div>
      {newName !== null && (
        <div className="jt-row">
          <input type="text" autoFocus placeholder="name the new version" value={newName} onChange={(e) => setNewName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") void run("new", () => createVersion(assetId, newName.trim() || undefined, version.id), "New version created and made active.").then(() => setNewName(null)); if (e.key === "Escape") setNewName(null); }} />
          <button className="primary" disabled={busy !== null} onClick={() => void run("new", () => createVersion(assetId, newName.trim() || undefined, version.id), "New version created and made active.").then(() => setNewName(null))}>Create</button>
        </div>
      )}
      <dl className="facts">
        <dt>id</dt><dd className="mono">{version.id}</dd>
        <dt>state</dt><dd>{version.finalized ? "finalized (locked)" : "open"} <button onClick={() => void run("lock", () => (version.finalized ? unfinalizeVersion(assetId, version.id) : finalizeVersion(assetId, version.id)), version.finalized ? "Version unlocked." : "Version finalized.")} disabled={busy !== null || offline}>{version.finalized ? "Unlock" : "Finalize"}</button></dd>
        <dt>trigger</dt><dd className="mono">{version.trigger_token ?? "derived at staging"}</dd>
        <dt>casting</dt><dd>{version.casting.length} candidate{version.casting.length === 1 ? "" : "s"}{hero ? ", hero starred" : ", no hero"}</dd>
        <dt>refs</dt><dd>{version.ref_set.length} curated{version.rejected?.length ? `, ${version.rejected.length} rejected` : ""}</dd>
        <dt>captions</dt><dd>{captions ? `${captions.count}, ${captions.edited_count} edited` : version.ref_set.length ? "…" : "none until refs are curated"}</dd>
        <dt>adapter</dt><dd>{version.lora ? `${version.lora.base_family} LoRA, promoted ${version.lora.promoted_at.slice(0, 10)}${version.lora.lora_weight_default != null ? `, weight ${version.lora.lora_weight_default}` : ""}` : "not trained"}</dd>
      </dl>

      <div className="section-title">Prompt template</div>
      <textarea rows={3} value={template} onChange={(e) => setTemplate(e.target.value)} disabled={version.finalized}
                placeholder="who this character is; the default clause for expansion and captions" />
      <div className="jt-row">
        <span className="spacer" />
        <button onClick={() => setTemplate(version.prompt_template ?? "")} disabled={!dirty}>Revert</button>
        <button className="primary" onClick={() => void run("save", () => saveProfile(assetId, template.trim(), version.id), "Prompt template saved.")} disabled={!dirty || busy !== null || offline || version.finalized}>Save</button>
      </div>

      <div className="section-title">Hero and anchor</div>
      <div className="hero-strip">
        {hero ? <img src={castingUrl(assetId, hero.file, version.id)} alt="hero" title="the hero ★" /> : <div className="hero-empty">no hero</div>}
        {version.anchor ? <img src={anchorUrl(assetId, version.id)} alt="anchor" title="face anchor" /> : <div className="hero-empty">no anchor</div>}
        <div className="hero-text">
          <div>{hero ? "Hero ★ set in Cast" : "Star a cast candidate in Info"}</div>
          <div className="faint">{version.anchor ? (anchorVerified ? "Anchor verified by an identity run" : "Anchor set, not verified yet") : "Set an anchor from a face image in Info"}</div>
          {version.anchor && (
            <button onClick={() => { if (!confirmClear) { setConfirmClear(true); return; } setConfirmClear(false); void run("anchor", () => clearAnchor(assetId, version.id), "Anchor cleared."); }}
                    onBlur={() => setConfirmClear(false)} disabled={busy !== null || offline}>{confirmClear ? "Clear the anchor?" : "Clear anchor"}</button>
          )}
        </div>
      </div>
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </>
  );
}
