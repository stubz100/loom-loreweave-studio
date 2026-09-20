// The centre: stage header, the one contextual Strip, and the Canvas. The canvas already
// draws a real read-only grid of the selected scope's finished images (the review view of
// migration step 5 arrives on top of it: zoom is live, selection is live, actions are not).
import { outputUrl, type Job } from "@loom/shared/api/orchestrator";

import { STAGE_LETTER, useApp, type Stage as StageId, type View } from "../store";
import { Start } from "./Start";

const VERBS: { id: StageId; label: string }[] = [
  { id: "cast", label: "Cast" }, { id: "expand", label: "Expand" }, { id: "curate", label: "Curate" }, { id: "train", label: "Train" },
];
const VIEWS: { id: View; label: string }[] = [
  { id: "flat", label: "Flat" }, { id: "grouped", label: "Grouped" }, { id: "captions", label: "Captions" }, { id: "loupe", label: "Loupe" },
];

export function Stage() {
  const workspace = useApp((s) => s.workspace);
  return (
    <main className="stage">
      {workspace === "assets" ? <AssetsStage /> : <WorldStage />}
    </main>
  );
}

function WorldStage() {
  return (
    <div className="canvas">
      <div className="empty">
        <h2>World</h2>
        <p>The style editor, the world text, the story spine and the pose sets open here, listed from the panel on the left.</p>
        <p className="faint">Arrives with migration step 3 of the UI plan.</p>
      </div>
    </div>
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
  const selectedAsset = useApp((s) => s.selectedAsset);
  const selection = useApp((s) => s.selection);
  const jobs = useApp((s) => s.jobs);
  const select = useApp((s) => s.select);
  const asset = useApp((s) => s.assetDetail);

  const versionId = asset?.profile.active_version ?? null;
  const activeVersion = asset?.versions.find((v) => v.id === versionId) ?? null;
  const letter = STAGE_LETTER[stage];
  const gridLetter = letter === "C" ? "B" : letter;   // Curate reviews the expansion set
  const scoped: Job[] = Object.values(jobs)
    .filter((j) => !j.deleted && j.status === "done")
    .filter((j) => (selectedAsset && versionId ? j.requester_id === versionId && (j.stage ?? "A") === gridLetter : !selectedAsset && (j.requester_id ?? "sandbox") === "sandbox"))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const tiles = scoped.flatMap((j) => {
    const names = j.result?.output_names ?? (j.result?.output_name ? [j.result.output_name] : []);
    return names.filter((n) => !/\.(mp4|webm|mov)$/i.test(n)).map((n) => ({ job: j, output: n }));
  });

  return (
    <>
      <div className="stage-header">
        {asset ? (
          <>
            <span className="name">{asset.profile.name}</span>
            {activeVersion && <span className="pill">{activeVersion.name}{activeVersion.lora ? " ✨" : ""}{activeVersion.finalized ? " 🔒" : ""}</span>}
            <span className="faint">{asset.versions.length} version(s)</span>
          </>
        ) : (
          <span className="name">{project ? "Sandbox" : ""}</span>
        )}
      </div>
      <div className="strip">
        <div className="verbs" role="tablist" aria-label="Stage">
          {VERBS.map((v) => (
            <button key={v.id} role="tab" aria-selected={stage === v.id} className={`verb${stage === v.id ? " active" : ""}`}
                    onClick={() => setStage(v.id)} disabled={!selectedAsset} title={selectedAsset ? v.label : "Select a character to work through its stages"}>
              {v.label}
            </button>
          ))}
        </div>
        <div className="views" role="tablist" aria-label="View">
          {VIEWS.map((v) => (
            <button key={v.id} role="tab" aria-selected={view === v.id} className={`verb${view === v.id ? " active" : ""}`}
                    onClick={() => setView(v.id)}
                    disabled={(v.id === "captions" && stage !== "train") || (v.id === "loupe" && !selection)}
                    title={v.id === "captions" ? "Captions view shows in Train" : v.id === "loupe" ? "Loupe needs a selected tile" : v.label}>
              {v.label}
            </button>
          ))}
        </div>
        <span className="muted">{tiles.length ? `${tiles.length} image${tiles.length === 1 ? "" : "s"}` : ""}</span>
        <input className="zoom" type="range" min={120} max={360} step={10} value={zoom}
               onChange={(e) => setZoom(Number(e.target.value))} title="thumbnail size" aria-label="Thumbnail size" />
      </div>
      <div className="canvas" style={{ ["--tile" as string]: `${zoom}px` }}>
        {!project ? (
          <Start />
        ) : tiles.length === 0 ? (
          <div className="empty">
            <h2>{selectedAsset ? `Nothing in ${VERBS.find((v) => v.id === stage)?.label} yet` : "The Sandbox is empty"}</h2>
            <p>{selectedAsset
              ? "Images made for this stage appear here as tiles. Generating them moves into the Compose panel with migration step 3."
              : "Unscoped generations land here. Compose arrives with migration step 3; until then v1 still generates."}</p>
          </div>
        ) : (
          <div className="grid">
            {tiles.map(({ job, output }) => {
              const isSel = selection?.jobId === job.id && selection?.output === output;
              return (
                <button key={`${job.id}:${output}`} className={`tile${isSel ? " selected" : ""}`}
                        onClick={() => select(isSel ? null : { jobId: job.id, output })}
                        title={output}>
                  <img src={outputUrl(output)} alt="" loading="lazy" />
                  <span className="cap">{job.pass ?? job.pipeline}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
