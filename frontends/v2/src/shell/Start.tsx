// The Start screen: what the canvas shows while no project is open — recent projects as
// cards, plus the two ways to get one. The frame around it stays put.
import { useEffect, useState } from "react";

import { listProjects, type ProjectListEntry } from "@loom/shared/api/orchestrator";

import { openProjectAt, reasonOf } from "../lib/project";
import { isTauri, pickFolder } from "../lib/tauri";
import { useApp } from "../store";

export function Start() {
  const setDialog = useApp((s) => s.setDialog);
  const notify = useApp((s) => s.notify);
  const offline = useApp((s) => s.offline);
  const [recent, setRecent] = useState<ProjectListEntry[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (offline) return;
    let alive = true;
    listProjects().then((r) => { if (alive) setRecent(r.projects); }).catch(() => { if (alive) setRecent([]); });
    return () => { alive = false; };
  }, [offline]);

  const openAt = async (path: string) => {
    setBusy(path);
    try {
      await openProjectAt(path);
    } catch (e) {
      notify("err", `Could not open ${path}: ${reasonOf(e)}`);
    } finally {
      setBusy(null);
    }
  };
  const onOpenFolder = async () => {
    if (isTauri()) {
      const p = await pickFolder("Open a project folder");
      if (p) await openAt(p);
    } else {
      setDialog("open");
    }
  };

  return (
    <div className="start">
      <h2>Open a project</h2>
      <p className="muted">Pick one you worked on recently, open another folder, or start a new one.</p>
      <div className="actions">
        <button className="primary" onClick={() => setDialog("new")} disabled={offline}>New project…</button>
        <button onClick={() => void onOpenFolder()} disabled={offline}>Open folder…</button>
      </div>
      <div className="section-title">Recent</div>
      {offline && <p className="faint">The recent list needs the orchestrator.</p>}
      {!offline && recent === null && <p className="faint">Loading…</p>}
      {recent?.length === 0 && <p className="faint">Nothing yet. Projects you open or create appear here.</p>}
      {recent && recent.length > 0 && (
        <div className="start-grid">
          {recent.map((p) => (
            <button key={p.path} className="start-card" disabled={!p.exists || busy !== null}
                    onClick={() => void openAt(p.path)}
                    title={p.exists ? `Open ${p.name ?? p.path}` : "This folder no longer exists"}>
              <span className="name">{p.name ?? "(unnamed)"}</span>
              <span className="path">{p.path}</span>
              <span className="meta">
                {p.size_cap_gb ? `${p.size_cap_gb} GB cap` : ""}
                {!p.exists ? " — folder missing" : busy === p.path ? " — opening…" : ""}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
