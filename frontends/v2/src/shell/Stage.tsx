// The centre: stage header, the one contextual Strip, and the Canvas (kb-loom-ui.md §3.4,
// migration step 5). The strip carries the stage verbs, the view switch, the Curate filters,
// the selection bar when tiles are marked, fit/fill and the zoom slider. The canvas is the
// flat grid, the grouped tree or the loupe — the same tiles in every view.
import { useMemo } from "react";

import { tileActions } from "../canvas/actions";
import { Captions } from "../canvas/Captions";
import { Edit } from "../canvas/Edit";
import { Grid } from "../canvas/Grid";
import { Grouped } from "../canvas/Grouped";
import { Loupe } from "../canvas/Loupe";
import { deriveCanvas, scopedJobs } from "../canvas/tiles";
import { ANGLES, EXPRESSIONS, SHOTS, nice } from "../lib/coverage";
import { STAGE_LETTER, useApp, type Stage as StageId, type View } from "../store";
import { Models } from "../models/Models";
import { World } from "../world/World";
import { LaterCanvas } from "./Later";
import { Start } from "./Start";

const VERBS: { id: StageId; label: string }[] = [
  { id: "cast", label: "Cast" }, { id: "expand", label: "Expand" }, { id: "curate", label: "Curate" }, { id: "train", label: "Train" },
];
const VIEWS: { id: View; label: string }[] = [
  { id: "flat", label: "Flat" }, { id: "grouped", label: "Grouped" }, { id: "captions", label: "Captions" }, { id: "loupe", label: "Loupe" }, { id: "edit", label: "Edit" },
];

export function Stage() {
  const workspace = useApp((s) => s.workspace);
  const modelsOpen = useApp((s) => s.modelsOpen);
  return (
    <main className="stage">
      {modelsOpen ? <Models /> : workspace === "assets" ? <AssetsStage /> : workspace === "world" ? <World /> : <LaterCanvas />}
    </main>
  );
}

function AssetsStage() {
  const project = useApp((s) => s.project);
  const stage = useApp((s) => s.stage);
  const setStage = useApp((s) => s.setStage);
  const view = useApp((s) => s.view);
  const setView = useApp((s) => s.setView);
  const zoom = useApp((s) => s.zoom);
  const setZoom = useApp((s) => s.setZoom);
  const fit = useApp((s) => s.fit);
  const setFit = useApp((s) => s.setFit);
  const selectedAsset = useApp((s) => s.selectedAsset);
  const selection = useApp((s) => s.selection);
  const jobs = useApp((s) => s.jobs);
  const asset = useApp((s) => s.assetDetail);
  const filters = useApp((s) => s.filters);
  const setFilters = useApp((s) => s.setFilters);
  const bulk = useApp((s) => s.bulk);
  const setBulk = useApp((s) => s.setBulk);
  const pendingDelete = useApp((s) => s.pendingDelete);
  const setPendingDelete = useApp((s) => s.setPendingDelete);
  const offline = useApp((s) => s.offline);

  const versionId = asset?.profile.active_version ?? null;
  const activeVersion = asset?.versions.find((v) => v.id === versionId) ?? null;
  const projectId = project?.id ?? null;
  const model = useMemo(() => deriveCanvas(jobs, projectId, asset, stage, filters), [jobs, projectId, asset, stage, filters]);
  const scoped = useMemo(() => scopedJobs(jobs, projectId, versionId, stage), [jobs, projectId, versionId, stage]);
  const marked = useMemo(() => model.tiles.filter((t) => bulk.includes(t.key)), [model.tiles, bulk]);
  const curating = !!selectedAsset && stage === "curate";
  // Edit mode needs a finished still with a job behind it (a curated copy or a video cannot be masked).
  const selJob = selection?.jobId ? jobs[selection.jobId] : undefined;
  const editImage = selJob && selJob.status === "done" ? (selection?.output ?? selJob.result?.output_name ?? null) : null;
  const editable = !!editImage && !/\.(mp4|webm|mov)$/i.test(editImage);
  const letter = STAGE_LETTER[stage];

  return (
    <>
      <div className="stage-header">
        {asset ? (
          <>
            <span className="name">{asset.profile.name}</span>
            {activeVersion && <span className="pill">{activeVersion.name}{activeVersion.lora ? " ✨" : ""}{activeVersion.finalized ? " 🔒" : ""}</span>}
            <span className="faint">{asset.versions.length} version{asset.versions.length === 1 ? "" : "s"}</span>
          </>
        ) : (
          <span className="name">{project ? "Sandbox" : ""}</span>
        )}
      </div>
      <div className="strip">
        <div className="verbs" role="tablist" aria-label="Stage">
          {VERBS.map((v) => (
            <button key={v.id} role="tab" aria-selected={stage === v.id} className={`verb${stage === v.id ? " active" : ""}`}
                    onClick={() => setStage(v.id)} disabled={!selectedAsset} title={selectedAsset ? `${v.label} (stage ${STAGE_LETTER[v.id]}, key ${VERBS.indexOf(v) + 1})` : "Select a character to work through its stages"}>
              {v.label}
            </button>
          ))}
        </div>
        <div className="views" role="tablist" aria-label="View">
          {VIEWS.map((v) => (
            <button key={v.id} role="tab" aria-selected={view === v.id} className={`verb${view === v.id ? " active" : ""}`}
                    onClick={() => setView(v.id)}
                    disabled={(v.id === "captions" && !(selectedAsset && stage === "train")) || (v.id === "loupe" && !selection) || (v.id === "edit" && !editable)}
                    title={v.id === "captions" ? "the curated refs with their captions (Train)" : v.id === "loupe" ? (selection ? "Loupe (Enter)" : "Loupe needs a selected tile") : v.id === "edit" ? (editable ? "paint an inpaint mask on the selected image (e)" : "Edit needs a selected finished image") : v.label}>
              {v.label}
            </button>
          ))}
        </div>
        {(curating || (view === "captions" && stage === "train")) && bulk.length === 0 && (
          <div className="filters" aria-label="Curate filters">
            <select value={filters.shot} onChange={(e) => setFilters({ shot: e.target.value })} title="shot size"><option value="">any shot</option>{SHOTS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select>
            <select value={filters.angle} onChange={(e) => setFilters({ angle: e.target.value })} title="angle"><option value="">any angle</option>{ANGLES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select>
            <select value={filters.expression} onChange={(e) => setFilters({ expression: e.target.value })} title="expression"><option value="">any expression</option>{EXPRESSIONS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select>
            <label className="p-flag" style={{ margin: 0 }}><input type="checkbox" checked={filters.showRejected} onChange={(e) => setFilters({ showRejected: e.target.checked })} />rejected</label>
            <span className="faint">{model.kept.size + model.tiles.filter((t) => t.ref).length} kept, {model.rejected.size} rejected, {model.tiles.length} of {model.all} shown</span>
          </div>
        )}
        {bulk.length > 0 && (
          <div className="selbar" aria-label="Selection">
            <span>{bulk.length} marked</span>
            {curating && !model.locked && <button onClick={() => void tileActions.bulk("keep", marked)} disabled={offline}>Keep</button>}
            {curating && !model.locked && <button onClick={() => void tileActions.bulk("reject", marked)} disabled={offline}>Reject</button>}
            <button className={pendingDelete === "__bulk__" ? "danger" : ""} onBlur={() => { if (pendingDelete === "__bulk__") setPendingDelete(null); }}
                    onClick={() => { if (pendingDelete === "__bulk__") void tileActions.bulkDelete(marked); else setPendingDelete("__bulk__"); }} disabled={offline}>
              {pendingDelete === "__bulk__" ? `Delete ${bulk.length}?` : "Delete"}
            </button>
            <button onClick={() => setBulk([])}>Clear</button>
          </div>
        )}
        <span className="spacer" />
        <span className="muted">{model.tiles.length ? `${model.tiles.length} tile${model.tiles.length === 1 ? "" : "s"}` : ""}</span>
        <button className={`verb${fit === "fill" ? " active" : ""}`} onClick={() => setFit(fit === "fill" ? "fit" : "fill")} title="fill the tile box (crops) or fit the whole image (bars)">{fit === "fill" ? "Fill" : "Fit"}</button>
        <input className="zoom" type="range" min={120} max={360} step={10} value={zoom}
               onChange={(e) => setZoom(Number(e.target.value))} title="thumbnail size" aria-label="Thumbnail size" />
      </div>
      <div className="canvas">
        {!project ? (
          <Start />
        ) : view === "edit" && editable ? (
          <Edit image={editImage!} />
        ) : view === "captions" && stage === "train" ? (
          <Captions />
        ) : view === "loupe" ? (
          <Loupe model={model} assetId={selectedAsset} versionId={versionId} />
        ) : model.tiles.length === 0 ? (
          <div className="empty">
            <h2>{selectedAsset ? `Nothing in ${VERBS.find((v) => v.id === stage)?.label} yet` : "The Sandbox is empty"}</h2>
            <p>{model.all > 0 ? "Every tile is hidden by the filters." : selectedAsset
              ? (stage === "cast" ? "Cast candidates from the Compose panel; star the one that becomes the hero."
                : stage === "expand" || stage === "curate" ? "Expand the hero into a coverage set from the Compose panel; the cells land here, and Curate keeps the good ones."
                : "LoRA previews land here once a run is promoted.")
              : "Unscoped generations land here. Use the Compose panel."}</p>
            {letter && <p className="faint">stage {letter}</p>}
          </div>
        ) : view === "grouped" ? (
          <Grouped model={model} jobs={scoped} assetId={selectedAsset} versionId={versionId} />
        ) : (
          <Grid model={model} assetId={selectedAsset} versionId={versionId} />
        )}
      </div>
    </>
  );
}
