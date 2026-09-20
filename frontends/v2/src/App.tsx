// Loreweave Studio v2 — the frame (kb-loom-ui.md §4, migration step 1).
//
//   top bar: workspace tabs · project · status cluster        (+ banner slot underneath)
//   rail 48px | Panel (Library / Compose / Train) | Stage: header · Strip · Canvas | Inspector
//   dock: one line, expands to the Jobs pane
//
// Every zone keeps its KIND across workspaces; only the contents change. Layout is remembered
// per machine. The zones read one store fed by one poller; nothing else polls.
import { useEffect } from "react";

import { orchestratorUrl } from "@loom/shared/api/orchestrator";
import { log } from "@loom/shared/api/log";

import { usePolling } from "./poll";
import { Banners } from "./shell/Banners";
import { Dialogs } from "./shell/Dialogs";
import { Dock } from "./shell/Dock";
import { Inspector } from "./shell/Inspector";
import { Panel } from "./shell/Panel";
import { Rail } from "./shell/Rail";
import { Shortcuts } from "./shell/Shortcuts";
import { Stage } from "./shell/Stage";
import { Toasts } from "./shell/Toasts";
import { TopBar } from "./shell/TopBar";
import { useApp } from "./store";

export default function App() {
  usePolling();
  const panelOpen = useApp((s) => s.panelOpen);
  const panelWidth = useApp((s) => s.panelWidth);
  const inspectorOpen = useApp((s) => s.inspectorOpen);
  const inspectorWidth = useApp((s) => s.inspectorWidth);
  const menuOpen = useApp((s) => s.menuOpen);
  const setMenuOpen = useApp((s) => s.setMenuOpen);

  useEffect(() => { log.info("v2 frame mounted →", orchestratorUrl()); }, []);

  const vars = {
    "--panel-w": panelOpen ? `${panelWidth}px` : "0px",
    "--inspector-w": inspectorOpen ? `${inspectorWidth}px` : "0px",
  } as React.CSSProperties;

  return (
    <div className="shell" style={vars} onClick={() => { if (menuOpen) setMenuOpen(false); }}>
      <TopBar />
      <Banners />
      <div className="frame">
        <Rail />
        {panelOpen ? <Panel /> : <div />}
        <Stage />
        {inspectorOpen ? <Inspector /> : <div />}
      </div>
      <Dock />
      <Toasts />
      <Dialogs />
      <Shortcuts />
    </div>
  );
}
