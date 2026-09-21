// The World workspace's canvas (kb-loom-ui.md §3.8, migration step 8): the Panel lists
// (styles with thumbnails, the prose outline, the spine characters, the pose sets) and the
// canvas hosts the editor the list points at. L1 finally shares the global toasts and banners.
import { useEffect } from "react";

import { useApp } from "../store";
import { Poses } from "./Poses";
import { Spine } from "./Spine";
import { StyleEditor } from "./StyleEditor";
import { WorldText } from "./WorldText";

export function World() {
  const project = useApp((s) => s.project);
  const tab = useApp((s) => s.worldTab);
  const refreshBible = useApp((s) => s.refreshBible);
  const projectId = project?.id ?? null;
  useEffect(() => { if (projectId) void refreshBible(); }, [projectId, refreshBible]);

  if (!project) {
    return (
      <div className="canvas">
        <div className="empty"><h2>World</h2><p>Open a project (File) to author its visual styles, world, story spine and pose sets.</p></div>
      </div>
    );
  }
  return (
    <div className="canvas world">
      {tab === "styles" && <StyleEditor />}
      {tab === "world" && <WorldText />}
      {tab === "spine" && <Spine />}
      {tab === "poses" && <Poses />}
    </div>
  );
}
