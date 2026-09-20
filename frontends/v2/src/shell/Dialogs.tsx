// Project dialogs (migration step 2): New project — folder, name, size cap with the footprint
// estimate beside it — and Open folder for the browser, where no native picker exists.
// In the shell the folder field has a Browse button; outside it the path is typed.
import { useEffect, useRef, useState, type ReactNode } from "react";

import { estimateFootprint, type FootprintReport } from "@loom/shared/api/orchestrator";

import { createProjectAt, openProjectAt, reasonOf } from "../lib/project";
import { isTauri, pickFolder } from "../lib/tauri";
import { useApp } from "../store";

export function Dialogs() {
  const dialog = useApp((s) => s.dialog);
  if (dialog === "new") return <NewProjectDialog />;
  if (dialog === "open") return <OpenFolderDialog />;
  return null;
}

function Modal({ title, children }: { title: string; children: ReactNode }) {
  const setDialog = useApp((s) => s.setDialog);
  return (
    <div className="modal-backdrop" onClick={() => setDialog(null)} role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

function FolderField({ id, value, onChange, hint, autoFocus }: {
  id: string; value: string; onChange: (v: string) => void; hint: string; autoFocus?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  const browse = async () => {
    setPicking(true);
    try {
      const p = await pickFolder("Choose a folder");
      if (p) onChange(p);
    } finally {
      setPicking(false);
    }
  };
  return (
    <>
      <label htmlFor={id}>Folder</label>
      <div className="row">
        <input id={id} type="text" value={value} onChange={(e) => onChange(e.target.value)}
               placeholder={isTauri() ? "Browse, or type a full path" : "Full path, e.g. F:\\stories\\my-story"} autoFocus={autoFocus} />
        {isTauri() && <button onClick={() => void browse()} disabled={picking}>Browse…</button>}
      </div>
      <span className="hint">{hint}</span>
    </>
  );
}

const MIN_CAP_GB = 50;
const ESTIMATE = { length_s: 1800, width: 1280, height: 720, fps: 24 };   // a 30-minute 720p episode

function NewProjectDialog() {
  const setDialog = useApp((s) => s.setDialog);
  const [dest, setDest] = useState("");
  const [name, setName] = useState("");
  const nameTouched = useRef(false);
  const [cap, setCap] = useState("");
  const [est, setEst] = useState<FootprintReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    estimateFootprint(ESTIMATE.length_s, ESTIMATE.width, ESTIMATE.height, ESTIMATE.fps)
      .then((r) => { if (alive) { setEst(r); setCap((c) => c || String(r.suggested_cap_gb)); } })
      .catch(() => { if (alive) setCap((c) => c || "250"); });
    return () => { alive = false; };
  }, []);

  const onDest = (v: string) => {
    setDest(v);
    if (!nameTouched.current) setName(v.split(/[\\/]/).filter(Boolean).pop() ?? "");
  };
  const capNum = parseInt(cap, 10);
  const capOk = Number.isFinite(capNum) && capNum >= MIN_CAP_GB;
  const canCreate = dest.trim() !== "" && name.trim() !== "" && capOk && !busy;

  const create = async () => {
    setBusy(true); setError(null);
    try {
      await createProjectAt(dest.trim(), name.trim(), capNum);
    } catch (e) {
      setError(reasonOf(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="New project">
      <form className="field" onSubmit={(e) => { e.preventDefault(); if (canCreate) void create(); }}>
        <FolderField id="np-dest" value={dest} onChange={onDest} autoFocus
                     hint="An empty folder. The project's files, queue and outputs live inside it." />
        <label htmlFor="np-name">Name</label>
        <input id="np-name" type="text" value={name} onChange={(e) => { nameTouched.current = true; setName(e.target.value); }} />
        <label htmlFor="np-cap">Size cap</label>
        <div className="row">
          <input id="np-cap" type="number" min={MIN_CAP_GB} step={10} value={cap} onChange={(e) => setCap(e.target.value)} style={{ maxWidth: 120 }} />
          <span className="muted">GB</span>
        </div>
        <span className="hint">
          {est
            ? `A 30-minute 720p master is about ${est.projected_master_gb} GB; ${est.suggested_cap_gb} GB is the suggested cap (${MIN_CAP_GB} GB minimum). The disk guard warns as the project approaches it and stops new jobs at the cap.`
            : `The disk guard stops new jobs at this cap (${MIN_CAP_GB} GB minimum).`}
        </span>
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="modal-actions">
          <button type="button" onClick={() => setDialog(null)} disabled={busy}>Cancel</button>
          <button type="submit" className="primary" disabled={!canCreate}>{busy ? "Creating…" : "Create project"}</button>
        </div>
      </form>
    </Modal>
  );
}

function OpenFolderDialog() {
  const setDialog = useApp((s) => s.setDialog);
  const [dest, setDest] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = async () => {
    setBusy(true); setError(null);
    try {
      await openProjectAt(dest.trim());
    } catch (e) {
      setError(reasonOf(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Open a project folder">
      <form className="field" onSubmit={(e) => { e.preventDefault(); if (dest.trim() && !busy) void open(); }}>
        <FolderField id="op-dest" value={dest} onChange={setDest} autoFocus
                     hint="A folder that already holds a loom project (its project.json)." />
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="modal-actions">
          <button type="button" onClick={() => setDialog(null)} disabled={busy}>Cancel</button>
          <button type="submit" className="primary" disabled={!dest.trim() || busy}>{busy ? "Opening…" : "Open"}</button>
        </div>
      </form>
    </Modal>
  );
}
