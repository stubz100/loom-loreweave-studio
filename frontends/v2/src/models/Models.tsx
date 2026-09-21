// The Models page (kb-loom-cache.md §4.6, M2.17 steps d + e): the model cache seen from loom.
// The location row (path · free of total · where it comes from · Change… · Move…), the Hugging
// Face token (masked, its source, Set / Change / Clear / Check), the roster BY MODEL (every model
// loom offers with the repos it loads — model, text encoder, vae, ControlNet, LoRA — their
// health and where in loom the model is used), the repos in the cache (loom's with a readable
// "used by", then the ones other tools left here), a prune that lists before it removes, and
// the previous location after a move. Every destructive action is a second click; long ones
// run as jobs the dock shows.
import { useEffect, useState } from "react";

import {
  cancelJob, checkCacheToken, deleteCacheRepo, deleteCacheRevision, deletePreviousCache, fetchCache, moveCache, pinCache, pruneCache,
  repairCacheRef, setCacheLocation, setCacheToken, verifyCache, type CacheModel, type CacheRepo, type Job, type PrunePlan,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

const HEALTH: Record<string, { label: string; cls: string }> = {
  ok: { label: "ok", cls: "ok" }, ref_drift: { label: "ref drift", cls: "warn" }, partial: { label: "partial", cls: "warn" },
  missing: { label: "not cached", cls: "err" }, empty: { label: "empty", cls: "faint" }, unused: { label: "not used by loom", cls: "faint" },
  stale_extra: { label: "ok, extras", cls: "ok" },
};
const KIND_TITLES: Record<CacheModel["kind"], string> = {
  model: "Catalog models", preset: "Casting presets", tool: "Tools", train: "Training", manifest: "Launch gate", other: "Other",
};
const TOKEN_SOURCE: Record<string, string> = {
  env: "set by the environment", dotenv: "set by .env.local", setting: "loom setting",
};

export function Models() {
  const cache = useApp((s) => s.cache);
  const refreshCache = useApp((s) => s.refreshCache);
  const closeModels = useApp((s) => s.closeModels);
  const offline = useApp((s) => s.offline);
  const project = useApp((s) => s.project);
  const notify = useApp((s) => s.notify);
  const openDock = useApp((s) => s.openDock);
  const jobs = useApp((s) => s.jobs);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [path, setPath] = useState<string | null>(null);      // the Change… field (null = closed)
  const [moveTo, setMoveTo] = useState<string | null>(null);  // the Move… field
  const [tokenDraft, setTokenDraft] = useState<string | null>(null);   // the Set / Change… field
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
    const j = r as { job_id?: string | null; note?: string; snapshot?: boolean };
    return j.job_id ? `${what} queued as ${j.job_id}${j.snapshot ? " (the whole repo)" : ""}; the meter is on the row and in the dock.` : (j.note ?? `${what}: nothing to do.`);
  };
  /** The cache job (fetch / verify) live for a repo, if any: its meter replaces the row's actions. */
  const liveJob = (repoId: string): Job | undefined =>
    Object.values(jobs).find((j) => j.pipeline === "hf_cache" && (j.status === "running" || j.status === "queued")
      && String((j.params as { repo_id?: unknown } | undefined)?.repo_id ?? "").toLowerCase() === repoId.toLowerCase());
  const meter = (j: Job) => {
    const p = Math.max(0, Math.min(1, j.progress || 0));
    const verb = j.mode === "fetch" ? "fetching" : j.mode === "verify" ? "verifying" : j.mode;
    return (
      <span className="meter-wrap" title={j.note ?? ""}>
        <span className="meter"><span className="meter-fill" style={{ width: `${Math.round(p * 100)}%` }} /></span>
        <span className="muted">{j.status === "queued" ? `${verb}: queued` : `${verb} ${Math.round(p * 100)}%${j.note ? ` · ${j.note}` : ""}`}</span>
        {twice(`cancel:${j.id}`, "Cancel", "Cancel?", () => cancelJob(j.id), j.mode === "fetch" ? "Canceled; a later fetch resumes where this one stopped." : "Canceled.")}
      </span>
    );
  };
  const checkTok = async (key: string, candidate?: string) => {
    setBusy(key);
    try {
      const r = await checkCacheToken(candidate ?? null);
      notify(r.ok ? "ok" : "err", r.ok ? `The token belongs to ${r.user ?? "an account"}${r.orgs?.length ? ` (${r.orgs.join(", ")})` : ""}.` : `Token check failed: ${r.error ?? "unknown"}`);
    } catch (e) { notify("err", reasonOf(e)); }
    finally { setBusy(null); }
  };

  if (!cache) {
    return <div className="canvas"><div className="empty"><h2>Models</h2><p>{offline ? "The orchestrator is offline." : "Reading the cache…"}</p><button onClick={closeModels}>Back</button></div></div>;
  }
  const loc = cache.location;
  const tok = cache.token;
  const needed = cache.repos.filter((r) => r.needed);
  const others = cache.repos.filter((r) => !r.needed);
  const byRepo = new Map(cache.repos.map((r) => [r.repo_id, r]));
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const fetchButton = (repoId: string, gated: boolean) => (
    <button className="primary" onClick={() => void act(`fetch:${repoId}`, () => fetchCache({ repo_id: repoId }), jobNote("Fetch"))} disabled={busy !== null || offline || !project}
            title={project ? (gated ? (tok.set ? "gated: fetch with the token (the license must be accepted on huggingface.co)" : "gated: needs the license accepted and a token, see above") : "fetch what is missing as a job") : "open a project first (jobs need one)"}>Fetch</button>
  );
  const repairButton = (repoId: string) => (
    <button className="primary" onClick={() => void act(`repair:${repoId}`, () => repairCacheRef(repoId), "Ref repaired.")} disabled={busy !== null || offline}>Repair</button>
  );

  const modelRow = (m: CacheModel) => {
    const h = HEALTH[m.health] ?? { label: m.health, cls: "" };
    return (
      <div key={m.tag} className={`model-row s-${m.health}`}>
        <div className="model-head">
          <span className="model-label">{m.label}</span>
          <span className={`pill ${h.cls}`}>{h.label}</span>
          <span className="faint">{m.where.length ? m.where.join(" · ") : "not wired in v2"}</span>
        </div>
        <div className="model-repos">
          {m.repos.map((r) => {
            const rh = HEALTH[r.health] ?? { label: r.health, cls: "" };
            const full = byRepo.get(r.repo_id);
            const live = liveJob(r.repo_id);
            return (
              <div key={`${m.tag}:${r.repo_id}:${r.role}`} className="model-repo">
                <span className="role">{r.role}</span>
                <span className="mono">{r.repo_id}</span>
                <span className={`pill ${rh.cls}`}>{rh.label}</span>
                <span className="muted">{r.size_gb ? `${r.size_gb} GB` : ""}</span>
                {!live && full && r.health !== "ok" && r.health !== "stale_extra" && <span className="faint">{full.detail}</span>}
                <span className="spacer" />
                {live ? meter(live) : (
                  <>
                    {r.health === "ref_drift" && repairButton(r.repo_id)}
                    {(r.health === "missing" || r.health === "partial") && fetchButton(r.repo_id, r.gated)}
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const repoRow = (r: CacheRepo) => {
    const h = HEALTH[r.health] ?? { label: r.health, cls: "" };
    const isOpen = open.has(r.repo_id);
    const usedBy = r.uses.map((u) => `${u.label} (${u.role})`).join(", ");
    const live = liveJob(r.repo_id);
    return (
      <div key={r.repo_id} className={`repo-row s-${r.health}`}>
        <div className="repo-head">
          <button className="repo-toggle" onClick={() => toggle(r.repo_id)} aria-expanded={isOpen}>{isOpen ? "▾" : "▸"} <span className="mono">{r.repo_id}</span></button>
          <span className={`pill ${h.cls}`}>{h.label}</span>
          <span className="muted">{r.size_gb} GB</span>
          <span className="spacer" />
          {live ? meter(live) : (
            <span className="repo-acts">
              {r.health === "ref_drift" && repairButton(r.repo_id)}
              {r.needed && (r.health === "missing" || r.health === "partial") && fetchButton(r.repo_id, r.gated)}
              {r.revisions.length > 0 && <button onClick={() => void act(`verify:${r.repo_id}`, () => verifyCache(r.repo_id), jobNote("Verify"))} disabled={busy !== null || offline || !project} title="hash every cached file against the name the hub gave it (a job)">Verify</button>}
              {twice(`del:${r.repo_id}`, "Delete", r.needed ? "Delete? loom needs it" : "Delete?", () => deleteCacheRepo(r.repo_id), (x) => `Deleted, ${(x as { freed_gb: number }).freed_gb} GB freed.`, false,
                     r.needed ? "loom needs this repo; the next generation will ask for it again" : "not used by loom")}
            </span>
          )}
        </div>
        <div className="repo-detail faint">{r.detail}{usedBy ? ` · used by ${usedBy}` : ""}</div>
        {isOpen && (
          <div className="rev-list">
            {r.revisions.length === 0 && <div className="faint">No revisions cached.</div>}
            {r.revisions.map((v) => (
              <div key={v.commit} className="rev-row">
                <span className="mono">{v.commit.slice(0, 10)}</span>
                <span className="muted">{v.ref ? "main" : "unreferenced"}</span>
                <span className="muted">{v.files} files, {v.size_gb} GB{r.needed ? `, ${v.needed_present} of ${v.needed_total} needed` : ""}{r.whole && !v.weights ? ", no weight files" : ""}</span>
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

  const modelSections: JSX.Element[] = [];
  let lastKind: CacheModel["kind"] | null = null;
  for (const m of cache.models ?? []) {
    if (m.kind !== lastKind) {
      modelSections.push(<div key={`kind:${m.kind}`} className="model-group">{KIND_TITLES[m.kind] ?? m.kind}</div>);
      lastKind = m.kind;
    }
    modelSections.push(modelRow(m));
  }

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

      <div className="section-title">Hugging Face token</div>
      <div className="loc-row">
        <span>{tok.set ? <>set, <span className="mono">{tok.masked}</span></> : "not set"}</span>
        <span className="pill">{tok.source ? TOKEN_SOURCE[tok.source] : "none"}</span>
        <span className="faint">needed for gated repos only (FLUX.2 klein, SD3.5 large); the fetch worker and the loaders receive it</span>
        <span className="spacer" />
        <button onClick={() => { setTokenDraft(tokenDraft === null ? "" : null); }} disabled={!tok.managed || offline}
                title={tok.managed ? "store a token from huggingface.co/settings/tokens (read access is enough)" : "remove HF_TOKEN from .env.local (or the environment) to manage the token here"}>{tok.set ? "Change…" : "Set…"}</button>
        {tok.set && tok.managed && twice("token-clear", "Clear", "Clear the token?", () => setCacheToken(null), "Token cleared.")}
        <button onClick={() => void checkTok("token-check")} disabled={!tok.set || busy !== null || offline} title="ask huggingface.co who this token belongs to">Check</button>
      </div>
      {!tok.managed && <p className="faint">The token comes from {tok.source === "env" ? "the HF_TOKEN environment variable" : "HF_TOKEN in .env.local"}. Remove it there to manage the token from loom.</p>}
      {tokenDraft !== null && (
        <div className="jt-row">
          <input type="password" value={tokenDraft} onChange={(e) => setTokenDraft(e.target.value)} placeholder="hf_… (read access is enough)" autoComplete="off" spellCheck={false} />
          <button disabled={busy !== null || !tokenDraft.trim() || offline} onClick={() => void checkTok("token-try", tokenDraft.trim())} title="ask huggingface.co who this token belongs to before saving it">Check first</button>
          <button className="primary" disabled={busy !== null || !tokenDraft.trim()} onClick={() => void act("token", () => setCacheToken(tokenDraft.trim()), "Token saved; the next fetch uses it.").then(() => setTokenDraft(null))}>Save token</button>
        </div>
      )}

      <div className="section-title">Models loom uses<span className="faint"> {cache.models?.length ?? 0}</span></div>
      <p className="faint">Every model loom offers, the repos it loads and where it is used. A model is only as cached as the worst of its repos.</p>
      {modelSections}

      <div className="section-title">Repos in the cache<span className="faint"> {needed.length} loom's</span></div>
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
