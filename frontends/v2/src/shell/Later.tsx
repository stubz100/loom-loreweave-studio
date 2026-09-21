// The workspaces that arrive with later phases (kb-loom-ui.md §3.10, migration step 8): the
// frame shows the whole tool, so each tab opens on a card that says what its navigator,
// canvas, inspector and dock will hold, and which phase brings it.
import { useApp, type Workspace } from "../store";

export const LATER: Record<Exclude<Workspace, "world" | "assets">, { title: string; phase: string; panel: string; canvas: string; inspector: string; dock: string; note: string }> = {
  shots: {
    title: "Shots", phase: "P3",
    panel: "The scene tree and the shot list.",
    canvas: "The compositor canvas: layers are the assets of this workspace, composed and generated as keyframes. The view switch also shows the take timeline as segment cards.",
    inspector: "Layers, camera, continuity from the Muse, and the Post tab.",
    dock: "The take timeline: keyframes are composed in the canvas, the cheap in-between segments run here.",
    note: "Edit mode's canvas layer is the seed of the compositor.",
  },
  flow: {
    title: "Flow", phase: "P4",
    panel: "The flow outline and the variables.",
    canvas: "The dialogue and branching graph.",
    inspector: "The node: dialogue, pins, conditions, and the Muse.",
    dock: "A play-mode reader pane.",
    note: "The triad matches the P4 knowledge base exactly.",
  },
  episode: {
    title: "Episode", phase: "P5",
    panel: "Render records and chapters.",
    canvas: "The preview of the assembled episode.",
    inspector: "The junction: transition, overlap, trim or freeze.",
    dock: "The single-lane timeline of the episode.",
    note: "Resolve's shape: preview above, timeline below.",
  },
};

export function LaterCanvas() {
  const workspace = useApp((s) => s.workspace) as keyof typeof LATER;
  const w = LATER[workspace];
  if (!w) return null;
  return (
    <div className="canvas">
      <div className="later-card">
        <h2>{w.title} <span className="pill">arrives with {w.phase}</span></h2>
        <dl className="facts">
          <dt>panel</dt><dd>{w.panel}</dd>
          <dt>canvas</dt><dd>{w.canvas}</dd>
          <dt>inspector</dt><dd>{w.inspector}</dd>
          <dt>dock</dt><dd>{w.dock}</dd>
        </dl>
        <p className="faint">{w.note}</p>
      </div>
    </div>
  );
}
