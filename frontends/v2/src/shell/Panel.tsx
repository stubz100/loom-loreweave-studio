// The left Panel: Library (read-only for now), Compose (Cast / Expand, step 3) and Train (the
// staging form, step 3b; captions, readiness and job rows follow with step 6).
import { useEffect, useState } from "react";

import { addStyle, listAssets, recipePresets, styleSampleUrl, type AssetSummary } from "@loom/shared/api/orchestrator";

import { Composer } from "../compose/Composer";
import { useCompose } from "../compose/composeStore";
import { nice } from "../lib/coverage";
import { reasonOf } from "../lib/project";
import { TrainComposer } from "../compose/Train";
import { useApp, type PanelTab, type WorldTab } from "../store";
import { LATER } from "./Later";
import { Resizer } from "./Resizer";

const TABS: { id: PanelTab; label: string }[] = [
  { id: "library", label: "Library" }, { id: "compose", label: "Compose" }, { id: "train", label: "Train" },
];

export function Panel() {
  const tab = useApp((s) => s.panelTab);
  const width = useApp((s) => s.panelWidth);
  const togglePanel = useApp((s) => s.togglePanel);
  const setPanelWidth = useApp((s) => s.setPanelWidth);
  const workspace = useApp((s) => s.workspace);
  const tabs = workspace === "assets" ? TABS : TABS.filter((t) => t.id === "library");
  return (
    <aside className="panel" aria-label="Panel">
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.id} className={`tab${tab === t.id ? " active" : ""}`} onClick={() => togglePanel(t.id)}>{t.label}</button>
        ))}
      </div>
      {tab === "library" && <Library />}
      {tab === "compose" && <Composer />}
      {tab === "train" && <TrainTab />}
      <Resizer edge="right" size={width} onResize={setPanelWidth} />
    </aside>
  );
}

function TrainTab() {
  const selectedAsset = useApp((s) => s.selectedAsset);
  if (selectedAsset) return <TrainComposer />;
  return (
    <>
      <div className="panel-body">
        <div className="section-title">Train</div>
        <p className="muted">Select a character in the Library to stage a LoRA from its curated references.</p>
      </div>
      <div className="panel-foot">
        <button className="primary" disabled>Stage the run</button>
      </div>
    </>
  );
}

function Library() {
  const project = useApp((s) => s.project);
  const workspace = useApp((s) => s.workspace);
  const selectedAsset = useApp((s) => s.selectedAsset);
  const selectAsset = useApp((s) => s.selectAsset);
  const notify = useApp((s) => s.notify);
  const [assets, setAssets] = useState<AssetSummary[] | null>(null);
  const projectId = project?.id ?? null;

  useEffect(() => {
    if (!projectId) { setAssets(null); return; }
    let alive = true;
    listAssets().then((r) => { if (alive) setAssets(r.assets); })
      .catch((e) => { if (alive) { setAssets([]); notify("err", `Could not list assets: ${String(e)}`); } });
    return () => { alive = false; };
  }, [projectId, notify]);

  if (workspace === "world") return <WorldList />;
  if (workspace !== "assets") {
    const w = LATER[workspace];
    return (
      <div className="panel-body">
        <div className="section-title">{w.title}</div>
        <p className="muted">{w.panel}</p>
        <p className="faint">Arrives with {w.phase}.</p>
      </div>
    );
  }
  if (!project) {
    return (
      <div className="panel-body">
        <div className="section-title">Library</div>
        <p className="muted">Open a project (File) to see its characters, props and scenes.</p>
      </div>
    );
  }
  const classes = [["characters", "Characters"], ["props", "Props"], ["scenes", "Scenes"]] as const;
  return (
    <div className="panel-body">
      <button className={`asset-row${selectedAsset === null ? " active" : ""}`} onClick={() => selectAsset(null)}>
        Sandbox <span className="count">unscoped</span>
      </button>
      {assets === null && <div className="faint">Loading…</div>}
      {assets && classes.map(([cls, label]) => {
        const rows = assets.filter((a) => a.asset_class === cls);
        return (
          <div key={cls}>
            <div className="section-title">{label}<span className="faint"> {rows.length}</span></div>
            {rows.length === 0 && <div className="faint">None yet.</div>}
            {rows.map((a) => (
              <button key={a.id} className={`asset-row${selectedAsset === a.id ? " active" : ""}`}
                      onClick={() => selectAsset(a.id)} title={`${a.version_count} version(s)`}>
                {a.name}<span className="count">{a.version_count} v</span>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}

const WORLD_TABS: { id: WorldTab; label: string }[] = [
  { id: "styles", label: "Styles" }, { id: "world", label: "World" }, { id: "spine", label: "Spine" }, { id: "poses", label: "Poses" },
];

/** The World workspace's navigator: styles with thumbnails, the prose outline, the spine
 *  characters, the pose sets. The editor for the chosen row opens on the canvas. */
function WorldList() {
  const project = useApp((s) => s.project);
  const tab = useApp((s) => s.worldTab);
  const setWorldTab = useApp((s) => s.setWorldTab);
  const styleSel = useApp((s) => s.styleSel);
  const setStyleSel = useApp((s) => s.setStyleSel);
  const bible = useApp((s) => s.bible);
  const poseSet = useApp((s) => s.poseSet);
  const setPoseSet = useApp((s) => s.setPoseSet);
  const offline = useApp((s) => s.offline);
  const notify = useApp((s) => s.notify);
  const styles = useCompose((s) => s.styles);
  const loadStyles = useCompose((s) => s.loadStyles);
  const [newStyle, setNewStyle] = useState<string | null>(null);
  const projectId = project?.id ?? null;
  useEffect(() => { if (projectId) void loadStyles(); }, [projectId, loadStyles]);

  if (!project) {
    return <div className="panel-body"><div className="section-title">World</div><p className="muted">Open a project (File) to author its styles, world, spine and poses.</p></div>;
  }
  const onAddStyle = async () => {
    const name = (newStyle ?? "").trim();
    if (!name) return;
    try {
      const r = await addStyle(name);
      await loadStyles();
      setStyleSel(r.styles[0]?.id ?? null);     // the newest lands first
      setNewStyle(null);
      notify("ok", `Style ${name} added.`);
    } catch (e) { notify("err", reasonOf(e)); }
  };
  const headings = (bible?.world ?? "").split("\n").filter((l) => /^#{1,3}\s/.test(l)).map((l) => l.replace(/^#+\s*/, ""));
  const chars = bible?.spine?.characters ?? [];
  return (
    <div className="panel-body">
      <div className="tabs world-tabs" role="tablist">
        {WORLD_TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? " active" : ""}`} onClick={() => setWorldTab(t.id)}>{t.label}</button>)}
      </div>
      {tab === "styles" && (
        <>
          {(styles?.styles ?? []).map((st) => {
            const active = styles?.active_style_id === st.id;
            const sel = (styleSel ?? styles?.active_style_id) === st.id;
            return (
              <button key={st.id} className={`asset-row style-row-item${sel ? " active" : ""}`} onClick={() => setStyleSel(st.id)} title={st.fragment || "no style prompt yet"}>
                {st.sample?.file ? <img src={styleSampleUrl(st.id, st.sample.set_at ?? undefined)} alt="" /> : <span className="style-ph">◐</span>}
                <span className="grow">{st.name}</span>
                {active && <span className="count">default</span>}
              </button>
            );
          })}
          {newStyle === null ? (
            <button className="asset-row" onClick={() => setNewStyle("")} disabled={offline}>+ Style</button>
          ) : (
            <div className="jt-row">
              <input type="text" autoFocus value={newStyle} placeholder="name" onChange={(e) => setNewStyle(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") void onAddStyle(); if (e.key === "Escape") setNewStyle(null); }} />
              <button className="primary" onClick={() => void onAddStyle()} disabled={!newStyle.trim()}>Add</button>
            </div>
          )}
        </>
      )}
      {tab === "world" && (
        <>
          <div className="section-title">Outline</div>
          {headings.length === 0 && <div className="faint">Headings of the world text show here.</div>}
          {headings.map((h, i) => <div key={i} className="outline-row">{h}</div>)}
        </>
      )}
      {tab === "spine" && (
        <>
          <div className="section-title">Characters<span className="faint"> {chars.length}</span></div>
          {chars.length === 0 && <div className="faint">None yet.</div>}
          {chars.map((c) => (
            <div key={c.id} className="asset-row" title={c.snippet}>
              <span className={`dot ${c.linked_asset_id ? "ok" : ""}`} />{c.name}<span className="count">{c.linked_asset_id ? "profile" : "spine only"}</span>
            </div>
          ))}
        </>
      )}
      {tab === "poses" && (
        <>
          <div className="section-title">Pose sets</div>
          {recipePresets.map((p) => (
            <button key={p} className={`asset-row${poseSet === p ? " active" : ""}`} onClick={() => setPoseSet(p)}>{nice(p)}</button>
          ))}
        </>
      )}
    </div>
  );
}
