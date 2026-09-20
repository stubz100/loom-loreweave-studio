// The left Panel: Library (read-only for now), Compose (Cast / Expand, step 3) and Train (the
// staging form, step 3b; captions, readiness and job rows follow with step 6).
import { useEffect, useState } from "react";

import { listAssets, type AssetSummary } from "@loom/shared/api/orchestrator";

import { Composer } from "../compose/Composer";
import { TrainComposer } from "../compose/Train";
import { useApp, type PanelTab } from "../store";
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

  if (workspace !== "assets") {
    return (
      <div className="panel-body">
        <div className="section-title">World</div>
        <p className="muted">Visual styles, the world text, the story spine and the pose sets list here; the editors open on the canvas. Arrives with migration step 3.</p>
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
