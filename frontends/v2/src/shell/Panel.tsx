// The left Panel: Library (real, read-only for now), Compose and Train (their forms arrive with
// migration steps 3 and 6 — what is here proves the shape: a scrolling body and a pinned foot).
import { useEffect, useState } from "react";

import { listAssets, type AssetSummary } from "@loom/shared/api/orchestrator";

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
      {tab === "compose" && <Placeholder title="Compose" step="3"
        text="The prompt, the JSON prompt tree, the model picker and the Stage-B recipe move in here as a column, with Generate pinned below." action="Generate" />}
      {tab === "train" && <Placeholder title="Train" step="6"
        text="The staging form (base, init, trigger, steps, advanced) moves in here; staged runs and training progress go to the dock." action="Stage" />}
      <Resizer edge="right" size={width} onResize={setPanelWidth} />
    </aside>
  );
}

function Placeholder({ title, step, text, action }: { title: string; step: string; text: string; action: string }) {
  return (
    <>
      <div className="panel-body">
        <div className="section-title">{title}</div>
        <p className="muted">{text}</p>
        <p className="faint">Arrives with migration step {step} of the UI plan.</p>
      </div>
      <div className="panel-foot">
        <button className="primary" disabled title={`Arrives with step ${step}`}>{action}</button>
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
