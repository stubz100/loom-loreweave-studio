// The right Inspector: tabs, resizable, collapsible. Info is real (the selected tile's job);
// Post, Readiness, Version and Muse land with migration steps 4, 6 and P4.
import { outputUrl } from "@loom/shared/api/orchestrator";

import { useApp, type InspectorTab } from "../store";
import { Resizer } from "./Resizer";

const TABS: { id: InspectorTab; label: string; later?: string }[] = [
  { id: "info", label: "Info" },
  { id: "post", label: "Post", later: "step 4" },
  { id: "readiness", label: "Readiness", later: "step 6" },
  { id: "version", label: "Version", later: "step 4" },
  { id: "muse", label: "Muse", later: "P4" },
];

export function Inspector() {
  const tab = useApp((s) => s.inspectorTab);
  const width = useApp((s) => s.inspectorWidth);
  const toggleInspector = useApp((s) => s.toggleInspector);
  const setInspectorWidth = useApp((s) => s.setInspectorWidth);
  const selection = useApp((s) => s.selection);
  const job = useApp((s) => (selection ? s.jobs[selection.jobId] : undefined));
  const current = TABS.find((t) => t.id === tab)!;

  return (
    <aside className="inspector" aria-label="Inspector">
      <Resizer edge="left" size={width} onResize={setInspectorWidth} />
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab${tab === t.id ? " active" : ""}`} onClick={() => toggleInspector(t.id)}
                  title={t.later ? `${t.label} arrives with ${t.later}` : t.label}>{t.label}</button>
        ))}
      </div>
      <div className="panel-body">
        {tab !== "info" && (
          <>
            <div className="section-title">{current.label}</div>
            <p className="muted">
              {tab === "post" && "The postprocess stack: the step tree, tombstones in place, and the add form with room for the JSON tree and the size row."}
              {tab === "readiness" && "The four readiness tiers with their details inline: missing cells as chips, duplicate groups as tile pairs."}
              {tab === "version" && "What the active version is: prompt template, trigger token, promoted adapter, caption state."}
              {tab === "muse" && "The chat dock that sees the current selection, and the agent plans awaiting approval."}
            </p>
            <p className="faint">Arrives with {current.later}.</p>
          </>
        )}
        {tab === "info" && !selection && (
          <>
            <div className="section-title">Nothing selected</div>
            <p className="muted">Select a tile on the canvas to see how it was made: model, seed, size, the resolved prompt and its log.</p>
          </>
        )}
        {tab === "info" && selection && job && (
          <>
            {selection.output && <img className="preview" src={outputUrl(selection.output)} alt="" />}
            <dl className="facts">
              <dt>job</dt><dd className="mono">{job.id}</dd>
              <dt>status</dt><dd>{job.status}{job.status === "running" ? ` ${Math.round(job.progress * 100)}%` : ""}</dd>
              <dt>pipeline</dt><dd>{job.pipeline} / {job.mode}{job.pass ? ` (${job.pass})` : ""}{job.stage ? `, stage ${job.stage}` : ""}</dd>
              {job.params.model_name ? <><dt>model</dt><dd>{String(job.params.model_name)}</dd></> : null}
              {job.params.width || job.params.height ? <><dt>size</dt><dd>{String(job.params.width)} × {String(job.params.height)}</dd></> : null}
              {job.result?.seed != null && <><dt>seed</dt><dd>{String(job.result.seed)}</dd></>}
              {job.wall_s != null && <><dt>wall</dt><dd>{Math.round(job.wall_s)} s</dd></>}
              {selection.output && <><dt>file</dt><dd className="mono">{selection.output}</dd></>}
              {job.chained_from && <><dt>from</dt><dd className="mono">{job.chained_from}</dd></>}
              {job.style_id && <><dt>style</dt><dd className="mono">{job.style_id}</dd></>}
            </dl>
            {typeof job.params.prompt === "string" && (
              <>
                <div className="section-title">Prompt</div>
                <p className="muted" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{job.params.prompt}</p>
              </>
            )}
            {job.status === "failed" && job.result?.error && (
              <>
                <div className="section-title">Error</div>
                <p className="mono" style={{ color: "var(--err)", overflowWrap: "anywhere" }}>{job.result.error}</p>
              </>
            )}
          </>
        )}
        {tab === "info" && selection && !job && (
          <p className="muted">That job is no longer in the queue.</p>
        )}
      </div>
    </aside>
  );
}
