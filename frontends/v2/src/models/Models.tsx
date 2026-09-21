// The Models page (kb-loom-cache.md §4.6, M2.17 step d): the model cache seen from loom. The
// location row (path · free of total · where it comes from · Change… · Move…), the roster with
// a health verdict, who uses it, size and the actions, the repos other tools left here, a prune
// that lists before it removes, and the previous location after a move. Every destructive
// action is a second click; long ones run as jobs the dock shows.
import { useEffect, useState } from "react";

import {
  deleteCacheRepo, deleteCacheRevision, deletePreviousCache, fetchCache, moveCache, pinCache, pruneCache, repairCacheRef,
  setCacheLocation, verifyCache, type CacheRepo, type PrunePlan,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

const HEALTH: Record<string, { label: string; cls: string }> = {
  ok: { label: "ok", cls: "ok" }, ref_drift: { label: "ref drift", cls: "warn" }, partial: { label: "partial", cls: "warn" },
  missing: { label: "not cached", cls: "err" }, empty: { label: "empty", cls: "faint" }, unused: { label: "not used by loom", cls: "faint" },
  stale_extra: { label: "ok, extras", cls: "ok" },
};

export function Models() {
  const cache = useApp((s) => s.cache);
  const refreshCache = useApp((s) => s.refreshCache);
  const closeModels = useApp((s) => s.closeModels);
  const offline = useApp((s) => s.offline);
  const project = useApp((s) => s.project);
  const notify = useApp((s) => s.notify);
  const openDock = useApp((s) => s.openDock);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [path, setPath] = useState<string | null>(null);      // the Change… field (null = closed)
  const [moveTo, setMoveTo] = useState<string | null>(null);  // the Move… field
  const [plan, setPlan] = useState<PrunePlan | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  useEffect(() => { if (!offline) void refreshCache(); }, [offline, refreshCache]);

  const act = async (key: string, fn: () => Promise<unknown>, done?: string | ((r: unknown) => string)) => {
    setBusy(key); setConfirm(null);
    try {
      const r = await fn();
      if (done) notify("ok", typeof done === "function" ? done(r) : done);
      await refreshCache();
    } catch (e) { notify("err", reasonOf(e)); }
    finally { setBusy(null); }
  };
  const twice = (key: string, label: string, armed: string, fn: () => Promise<unknown>, done?: string | ((r: unknown) => string), disabled = false, title?: string) => (
    <button className={confirm === key ? "danger" : ""} disabled={disabled || busy !== null || offline} title={title}
            onBlur={() => { if (confirm === key) setConfirm(null); }}
            onClick={() => { if (confirm === key) void act(key, fn, done); else setConfirm(key); }}>
      {confirm === key ? armed : label}
    </button>
  );
  const jobNote = (what: string) => (r: unknown) => {
    const j = r as { job_id?: string | null; note?: string };
    return j.job_id ? `${what} queued as ${j.job_id}; the dock shows its progress.` : (j.note ?? `${what}: nothing to do.`);
  };

  if (!cache) {
    return <div className="canvas"><div className="empty"><h2>Models</h2><p>{offline ? "The orchestrator is offline." : "Reading the cache…"}</p><button onClick={closeModels}>Back</button></div></div>;
  }
  const loc = cache.location;
  const needed = cache.repos.filter((r) => r.needed);
  const others = cache.repos.filter((r) => !r.needed);
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const repoRow = (r: CacheRepo) => {
    const h = HEALTH[r.health] ?? { label: r.health, cls: "" };
    const isOpen = open.has(r.repo_id);
    return (
      <div key={r.repo_id} className={`repo-row s-${r.health}`}>
        <div className="repo-head">
          <button className="repo-toggle" onClick={() => toggle(r.repo_id)} aria-expanded={isOpen}>{isOpen ? "▾" : "▸"} <span className="mono">{r.repo_id}</span></button>
          <span className={`pill ${h.cls}`}>{h.label}</span>
          <span className="muted">{r.size_gb} GB</span>
          <span className="spacer" />
          <span className="repo-acts">
            {r.health === "ref_drift" && <button className="primary" onClick={() => void act(`repair:${r.repo_id}`, () => repairCacheRef(r.repo_id), "Ref repaired.")} disabled={busy !== null || offline}>Repair</button>}
            {r.needed && (r.health === "missing" || r.health === "partial") && (
              <button className="primary" onClick={() => void act(`fetch:${r.repo_id}`, () => fetchCache({ repo_id: r.repo_id }), jobNote("Fetch"))} disabled={busy !== null || offline || !project}
                      title={project ? (r.gated ? "gated: needs the license accepted and HF_TOKEN" : "fetch the missing files as a job") : "open a project first (jobs need one)"}>Fetch</button>
            )}
            {r.revisions.length > 0 && <button onClick={() => void act(`verify:${r.repo_id}`, () => verifyCache(r.repo_id), jobNote("Verify"))} disabled={busy !== null || offline || !project} title="hash every cached file against the name the hub gave it (a job)">Verify</button>}
            {twice(`del:${r.repo_id}`, "Delete", r.needed ? "Delete? loom needs it" : "Delete?", () => deleteCacheRepo(r.repo_id), (x) => `Deleted, ${(x as { freed_gb: number }).freed_gb} GB freed.`, false,
                   r.needed ? "loom needs this repo; the next generation will ask for it again" : "not used by loom")}
          </span>
        </div>
        <div className="repo-detail faint">{r.detail}{r.used_by.length ? ` · used by ${r.used_by.join(", ")}` : ""}</div>
        {isOpen && (
          <div className="rev-list">
            {r.revisions.length === 0 && <div className="faint">No revisions cached.</div>}
            {r.revisions.map((v) => (
              <div key={v.commit} className="rev-row">
                <span className="mono">{v.commit.slice(0, 10)}</span>
                <span className="muted">{v.ref ? "main" : "unreferenced"}</span>
                <span className="muted">{v.files} files, {v.size_gb} GB{r.needed ? `, ${v.needed_present} of ${v.needed_total} needed` : ""}</span>
                <span className={`pill ${v.complete_for_loom ? "ok" : "warn"}`}>{v.complete_for_loom ? "complete" : "incomplete"}</span>
                <span className="spacer" />
                <button onClick={() => void act(`pin:${r.repo_id}`, () => pinCache(r.repo_id, v.commit), `Pinned ${v.commit.slice(0, 7)}.`)} disabled={busy !== null || offline} title="loom reads this revision regardless of the ref">Pin</button>
                {twice(`delrev:${r.repo_id}:${v.commit}`, "Delete", "Delete revision?", () => deleteCacheRevision(r.repo_id, v.commit), (x) => `Revision deleted, ${(x as { freed_gb: number }).freed_gb} GB freed.`)}
              </div>
            ))}
            {r.revisions.length > 0 && <button onClick={() => void act(`unpin:${r.repo_id}`, () => pinCache(r.repo_id, null), "Pin cleared.")} disabled={busy !== null || offline}>Clear pin</button>}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="canvas models">
      <div className="editor-head">
        <h2>Models</h2>
        <span className="faint">{cache.repos.length} repos, {cache.size_gb} GB in the cache</span>
        <span className="spacer" />
        <button onClick={() => void refreshCache()} disabled={offline}>Refresh</button>
        <button onClick={closeModels}>Back (Esc)</button>
      </div>

      <div className="section-title">Location</div>
      <div className="loc-row">
        <span className="mono">{loc.path}</span>
        <span className="muted">{loc.free_gb != null ? `${loc.free_gb} of ${loc.total_gb} GB free` : ""}</span>
        <span className="pill">{loc.source === "env" ? "set by the environment" : loc.source === "dotenv" ? "set by .env" : loc.source === "setting" ? "loom setting" : loc.source === "hf_home" ? "inherited HF_HOME" : "default"}</span>
        <span className="spacer" />
        <button onClick={() => { setPath(path === null ? loc.path : null); setMoveTo(null); }} disabled={!loc.managed || offline} title={loc.managed ? "point loom at another folder (the files stay where they are)" : "remove LOOM_MODELS_DIR from .env to manage the location here"}>Change…</button>
        <button onClick={() => { setMoveTo(moveTo === null ? "" : null); setPath(null); }} disabled={!loc.managed || offline || !project} title={loc.managed ? (project ? "copy the cache to another folder as a job, then switch" : "open a project first (jobs need one)") : "remove LOOM_MODELS_DIR from .env to move from here"}>Move…</button>
      </div>
      {!loc.managed && <p className="faint">The location comes from {loc.source === "env" ? "the LOOM_MODELS_DIR environment variable" : "LOOM_MODELS_DIR in .env or .env.local"}. Remove it there to manage the location from loom.</p>}
      {path !== null && (
        <div className="jt-row">
          <input type="text" value={path} onChange={(e) => setPath(e.target.value)} placeholder="absolute folder; a hub/ subfolder is created" />
          <button className="primary" disabled={busy !== null || !path.trim()} onClick={() => void act("loc", () => setCacheLocation(path.trim()), "Location set; the next scan and the next job use it.").then(() => setPath(null))}>Use this folder</button>
        </div>
      )}
      {moveTo !== null && (
        <div className="jt-row">
          <input type="text" value={moveTo} onChange={(e) => setMoveTo(e.target.value)} placeholder="destination folder with room for the whole cache" />
          <button className="primary" disabled={busy !== null || !moveTo.trim()} onClick={() => void act("move", () => moveCache(moveTo.trim()), (r) => `Move queued as ${(r as { job_id: string }).job_id}; the location switches when it completes.`).then(() => { setMoveTo(null); openDock("active"); })}>Start the move</button>
        </div>
      )}
      {loc.previous && (
        <div className="jt-row">
          <span className="muted">Previous location {loc.previous.path}{loc.previous.exists ? `, ${loc.previous.size_gb} GB still there` : ", already gone"}.</span>
          <span className="spacer" />
          {twice("prev", "Delete the previous tree", "Delete it?", () => deletePreviousCache(), (x) => `Previous cache deleted, ${(x as { freed_gb: number }).freed_gb} GB freed.`)}
        </div>
      )}

      <div className="section-title">Models loom uses<span className="faint"> {needed.length}</span></div>
      {needed.map(repoRow)}

      <div className="section-title">Other repos in this cache<span className="faint"> {others.length}</span></div>
      <p className="faint">Not in loom's roster; they may belong to another tool sharing this cache. Nothing here is touched unless you delete it.</p>
      {others.map(repoRow)}

      <div className="section-title">Prune</div>
      <p className="faint">Removes unreferenced revisions of loom's repos that are not complete, empty repo folders and orphaned blobs. Never a complete revision, never another tool's repo. List first.</p>
      <div className="jt-row">
        <button onClick={() => void (async () => { setBusy("plan"); try { setPlan(await pruneCache(true)); } catch (e) { notify("err", reasonOf(e)); } finally { setBusy(null); } })()} disabled={busy !== null || offline}>List what a prune would remove</button>
        {plan && <span className="muted">{plan.total_gb} GB: {plan.revisions.length} revision(s), {plan.empty_repos.length} empty repo(s), {plan.orphan_blobs.length} orphan blob(s)</span>}
        <span className="spacer" />
        {plan && plan.total_gb > 0 && twice("prune", "Prune", `Remove ${plan.total_gb} GB?`, () => pruneCache(false), (x) => `Pruned, ${(x as { freed_gb: number }).freed_gb} GB freed.`)}
      </div>
      {plan && (
        <ul className="prune-list">
          {plan.revisions.map((r) => <li key={r.commit}><span className="mono">{r.repo_id}</span> revision {r.commit.slice(0, 10)}, {r.files} files, {r.size_gb} GB</li>)}
          {plan.empty_repos.map((r) => <li key={r.repo_id}><span className="mono">{r.repo_id}</span> empty folder</li>)}
          {plan.orphan_blobs.map((b) => <li key={b.blob}><span className="mono">{b.repo_id}</span> orphan blob {b.blob.slice(0, 10)}, {b.size_gb} GB</li>)}
          {plan.total_gb === 0 && <li className="faint">Nothing to prune.</li>}
        </ul>
      )}
    </div>
  );
}
