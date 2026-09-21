// Small cache-aware pieces the composers use (M2.17 step d/e): the weight status of the picked
// variant under the model picker — every repo the variant loads (model, text encoder, vae),
// the bad ones named — and the Fetch / Repair / Open Models buttons on a refusal that names a
// repo ("not in cache", a gated repo).
import { fetchCache, repairCacheRef, type Job } from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

const STATUS: Record<string, string> = {
  ok: "cached", stale_extra: "cached", ref_drift: "cached, ref drift (repair)", partial: "partly cached", missing: "not cached",
};

/** The cache job (fetch / verify) live for a repo, if any. */
function liveCacheJob(jobs: Record<string, Job>, repoId: string): Job | undefined {
  return Object.values(jobs).find((j) => j.pipeline === "hf_cache" && (j.status === "running" || j.status === "queued")
    && String((j.params as { repo_id?: unknown } | undefined)?.repo_id ?? "").toLowerCase() === repoId.toLowerCase());
}
const meterText = (j: Job) => j.status === "queued" ? `${j.mode} queued` : `${j.mode === "fetch" ? "fetching" : j.mode} ${Math.round(Math.max(0, Math.min(1, j.progress || 0)) * 100)}%${j.note ? ` · ${j.note}` : ""}`;

/** The health of every repo behind a catalog variant, from the inventory's by-model view. */
export function VariantWeights({ pipeline, model }: { pipeline: string; model?: string }) {
  const cache = useApp((s) => s.cache);
  const jobs = useApp((s) => s.jobs);
  const openModels = useApp((s) => s.openModels);
  if (!cache || !model) return null;
  const tag = `catalog:${pipeline}/${model}`;
  const m = cache.models?.find((x) => x.tag === tag);
  if (!m) return null;
  const bad = m.repos.filter((r) => r.health === "missing" || r.health === "partial" || r.health === "ref_drift");
  const text = bad.length
    ? bad.map((r) => { const j = liveCacheJob(jobs, r.repo_id); return `${STATUS[r.health] ?? r.health}: ${r.repo_id} (${r.role}${j ? `, ${meterText(j)}` : ""})`; }).join("; ")
    : "cached";
  const gated = m.repos.some((r) => r.gated);
  return (
    <p className={`faint variant-weights${bad.length ? " warn" : ""}`}>
      Weights: {text}{gated ? ", gated" : ""}. <button className="link" onClick={openModels}>Models</button>
    </p>
  );
}

/** A refusal line; when it names a repo the cache can act on, the actions ride along. */
export function Problem({ text }: { text: string | null }) {
  const cache = useApp((s) => s.cache);
  const jobs = useApp((s) => s.jobs);
  const openModels = useApp((s) => s.openModels);
  const refreshCache = useApp((s) => s.refreshCache);
  const notify = useApp((s) => s.notify);
  if (!text) return null;
  const m = /"repo_id"\s*:\s*"([^"]+)"/.exec(text);
  const repoId = m?.[1] ?? null;
  const repo = repoId ? cache?.repos.find((r) => r.repo_id.toLowerCase() === repoId.toLowerCase()) : undefined;
  const drift = repo?.health === "ref_drift";
  const live = repoId ? liveCacheJob(jobs, repoId) : undefined;
  const run = async (fn: () => Promise<unknown>, done: string) => {
    try { await fn(); notify("ok", done); await refreshCache(); } catch (e) { notify("err", reasonOf(e)); }
  };
  return (
    <span className="form-error" role="alert">
      {repoId ? `${repoId}: ${live ? meterText(live) : repo ? (STATUS[repo.health] ?? repo.health) : "weights missing"}` : text}
      {repoId && (
        <span className="problem-acts">
          {live && <span className="meter"><span className="meter-fill" style={{ width: `${Math.round(Math.max(0, Math.min(1, live.progress || 0)) * 100)}%` }} /></span>}
          {!live && drift && <button onClick={() => void run(() => repairCacheRef(repoId), "Ref repaired; try again.")}>Repair</button>}
          {!live && !drift && <button onClick={() => void run(() => fetchCache({ repo_id: repoId }), "Fetch queued; the meter is here and in the dock.")}>Fetch</button>}
          <button onClick={openModels}>Models</button>
        </span>
      )}
    </span>
  );
}
