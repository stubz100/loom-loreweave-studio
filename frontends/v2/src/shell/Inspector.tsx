// The right Inspector: tabs, resizable, collapsible (kb-loom-ui.md §3.5, migration step 4).
// Info = the selected tile and its actions; Post = its postprocess stack as a tree; Version =
// the active version (what the selection is when no tile is); Readiness = the advisory meter
// with its details inline; Muse lands with P4.
import { useApp, type InspectorTab } from "../store";
import { InfoTab, RefInfo } from "../inspect/InfoTab";
import { PostTab } from "../inspect/PostTab";
import { ReadinessTab } from "../inspect/ReadinessTab";
import { VersionTab } from "../inspect/VersionTab";
import { Resizer } from "./Resizer";

const TABS: { id: InspectorTab; label: string; later?: string }[] = [
  { id: "info", label: "Info" },
  { id: "post", label: "Post" },
  { id: "readiness", label: "Readiness" },
  { id: "version", label: "Version" },
  { id: "muse", label: "Muse", later: "P4" },
];

export function Inspector() {
  const tab = useApp((s) => s.inspectorTab);
  const width = useApp((s) => s.inspectorWidth);
  const toggleInspector = useApp((s) => s.toggleInspector);
  const setInspectorWidth = useApp((s) => s.setInspectorWidth);
  const selection = useApp((s) => s.selection);
  const project = useApp((s) => s.project);
  const job = useApp((s) => (selection?.jobId ? s.jobs[selection.jobId] : undefined));
  const current = TABS.find((t) => t.id === tab)!;
  const workspace = useApp((s) => s.workspace);
  const image = selection && job ? selection.output ?? job.result?.output_name ?? null : null;
  const postable = !!image && job?.status === "done" && !/\.(mp4|webm|mov)$/i.test(image);

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
        {workspace !== "assets" && !current.later && (
          <p className="muted">{workspace === "world" ? "The World editors are on the canvas; this inspector reads the Assets workspace." : "This workspace arrives later; the card on the canvas says what the inspector will hold."}</p>
        )}
        {workspace === "assets" && current.later && (
          <>
            <div className="section-title">{current.label}</div>
            <p className="muted">
              {tab === "muse" && "The chat dock that sees the current selection, and the agent plans awaiting approval."}
            </p>
            <p className="faint">Arrives with {current.later}.</p>
          </>
        )}
        {workspace === "assets" && tab === "info" && (!selection ? <VersionTab /> : job ? <InfoTab selection={selection} job={job} /> : selection.refId ? <RefInfo refId={selection.refId} /> : <p className="muted">That job is no longer in the queue.</p>)}
        {workspace === "assets" && tab === "post" && (
          !project ? <p className="muted">Open a project first.</p>
          : !selection ? <><div className="section-title">Post</div><p className="muted">Select a finished image on the canvas. Its stack of passes shows here as a tree, with the add form under it.</p></>
          : !postable ? <p className="muted">{selection.refId ? "A curated copy has no generation behind it; postprocess its source tile instead." : "Postprocess works on a finished image, not a video or a running job."}</p>
          : <PostTab image={image!} />
        )}
        {workspace === "assets" && tab === "version" && <VersionTab />}
        {workspace === "assets" && tab === "readiness" && <ReadinessTab />}
      </div>
    </aside>
  );
}
