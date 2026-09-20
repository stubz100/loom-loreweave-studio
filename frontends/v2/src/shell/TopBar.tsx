// Top bar: workspace tabs (L1–L5, later layers present but disabled), the project, and the
// status cluster — orchestrator, queue, disk. Global things only (kb-loom-ui.md §3.1).
import { useEffect, useState } from "react";

import { closeProject, forgetProject, listProjects, openProject, type ProjectListEntry } from "@loom/shared/api/orchestrator";

import { useApp, type Workspace } from "../store";

const WORKSPACES: { id: Workspace; label: string; phase?: string }[] = [
  { id: "world", label: "World" },
  { id: "assets", label: "Assets" },
  { id: "shots", label: "Shots", phase: "P3" },
  { id: "flow", label: "Flow", phase: "P4" },
  { id: "episode", label: "Episode", phase: "P5" },
];

export function TopBar() {
  const workspace = useApp((s) => s.workspace);
  const setWorkspace = useApp((s) => s.setWorkspace);
  const project = useApp((s) => s.project);
  const health = useApp((s) => s.health);
  const offline = useApp((s) => s.offline);
  const counts = useApp((s) => s.counts);
  const paused = useApp((s) => s.paused);
  const disk = useApp((s) => s.disk);
  const toggleDock = useApp((s) => s.toggleDock);
  const menuOpen = useApp((s) => s.menuOpen);
  const setMenuOpen = useApp((s) => s.setMenuOpen);

  const running = counts?.running ?? 0;
  const queued = counts?.queued ?? 0;
  const queueText = paused ? `paused, ${queued} queued` : running ? `${running} running, ${queued} queued` : queued ? `${queued} queued` : "idle";
  const diskText = disk?.disk ? `${Math.round(disk.disk.free_pct)}% free` : "";
  const diskClass = disk?.state === "hard" ? "err" : disk?.state === "warn" ? "warn" : "ok";

  return (
    <header className="topbar">
      <span className="brand">Loreweave</span>
      <button onClick={() => setMenuOpen(!menuOpen)} aria-haspopup="menu" aria-expanded={menuOpen}>File</button>
      {menuOpen && <FileMenu onClose={() => setMenuOpen(false)} />}
      <nav className="ws-tabs" aria-label="Workspaces">
        {WORKSPACES.map((w) => (
          <button key={w.id} className={`ws-tab${workspace === w.id ? " active" : ""}`}
                  disabled={!!w.phase} onClick={() => setWorkspace(w.id)}
                  title={w.phase ? `${w.label} arrives with ${w.phase}` : w.label}>
            {w.label}
          </button>
        ))}
      </nav>
      <span className="spacer" />
      <span className="project-name">{project ? project.name : <span className="faint">No project open</span>}</span>
      <span className="spacer" />
      <div className="status">
        <span title={offline ? "The orchestrator is not answering" : `orchestrator v${health?.app_version}`}>
          <span className={`dot ${offline ? "err" : "ok"}`} />{offline ? "offline" : "online"}
        </span>
        <button onClick={toggleDock} title="show the job queue">
          <span className={`dot ${running ? "amber" : paused ? "warn" : ""}`} />{queueText}
        </button>
        {diskText && <span title={disk?.reason ?? "disk"}><span className={`dot ${diskClass}`} />{diskText}</span>}
      </div>
    </header>
  );
}

function FileMenu({ onClose }: { onClose: () => void }) {
  const project = useApp((s) => s.project);
  const notify = useApp((s) => s.notify);
  const [recent, setRecent] = useState<ProjectListEntry[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    listProjects().then((r) => { if (alive) setRecent(r.projects); }).catch(() => { if (alive) setRecent([]); });
    return () => { alive = false; };
  }, []);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onClose();
    } catch (e) {
      notify("err", `${label} failed: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  // Interim (migration step 2 brings the native folder dialog): a typed path, like v1.
  const onOpenFolder = () => {
    const path = window.prompt("Project folder to open (full path):");
    if (path?.trim()) void run("Open", () => openProject(path.trim()));
  };

  return (
    <div className="menu" role="menu" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <button className="menu-item" disabled title="Arrives with migration step 2 (native folder dialog + the new-project form)">New project…</button>
      <button className="menu-item" onClick={onOpenFolder} disabled={busy}>Open folder…</button>
      <button className="menu-item" onClick={() => void run("Close", closeProject)} disabled={!project || busy}>Close project</button>
      <div className="menu-sep" />
      <div className="section-title">Recent</div>
      {recent === null && <div className="faint">Loading…</div>}
      {recent?.length === 0 && <div className="faint">No recent projects.</div>}
      {recent?.map((p) => (
        <div className="recent" key={p.path}>
          <button className="open-btn" disabled={busy || !p.exists || p.active}
                  onClick={() => void run("Open", () => openProject(p.path))}
                  title={p.exists ? (p.active ? "This project is open" : "Open this project") : "This folder no longer exists"}>
            {p.name ?? "(unnamed)"}{p.active ? " — open" : ""}{!p.exists ? " — missing" : ""}
            <span className="path">{p.path}</span>
          </button>
          <button onClick={() => void run("Forget", async () => { await forgetProject(p.path); setRecent((r) => (r ?? []).filter((x) => x.path !== p.path)); })}
                  title="remove from the recent list (the folder stays)" disabled={busy}>✕</button>
        </div>
      ))}
    </div>
  );
}
