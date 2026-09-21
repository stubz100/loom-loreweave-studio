// Small cache-aware pieces the composers use (M2.17 step d): the weight status of the picked
// variant under the model picker, and the Fetch / Repair / Open Models buttons on a refusal
// that names a repo ("not in cache", a gated repo).
import { fetchCache, repairCacheRef } from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

const STATUS: Record<string, string> = {
  ok: "cached", stale_extra: "cached", ref_drift: "cached, ref drift (repair)", partial: "partly cached", missing: "not cached",
};

/** The health of the repo behind a catalog variant, from the inventory's `used_by` tags. */
export function VariantWeights({ pipeline, model }: { pipeline: string; model?: string }) {
  const cache = useApp((s) => s.cache);
  const openModels = useApp((s) => s.openModels);
  if (!cache || !model) return null;
  const tag = `catalog:${pipeline}/${model}`;
  const repo = cache.repos.find((r) => r.used_by.includes(tag));
  if (!repo) return null;
  const text = STATUS[repo.health] ?? repo.health;
  const bad = repo.health === "missing" || repo.health === "partial" || repo.health === "ref_drift";
  return (
    <p className={`faint variant-weights${bad ? " warn" : ""}`}>
      Weights: {text}{repo.gated ? ", gated" : ""}. <button className="link" onClick={openModels}>Models</button>
    </p>
  );
}

/** A refusal line; when it names a repo the cache can act on, the actions ride along. */
export function Problem({ text }: { text: string | null }) {
  const cache = useApp((s) => s.cache);
  const openModels = useApp((s) => s.openModels);
  const refreshCache = useApp((s) => s.refreshCache);
  const notify = useApp((s) => s.notify);
  if (!text) return null;
  const m = /"repo_id"\s*:\s*"([^"]+)"/.exec(text);
  const repoId = m?.[1] ?? null;
  const repo = repoId ? cache?.repos.find((r) => r.repo_id.toLowerCase() === repoId.toLowerCase()) : undefined;
  const drift = repo?.health === "ref_drift";
  const run = async (fn: () => Promise<unknown>, done: string) => {
    try { await fn(); notify("ok", done); await refreshCache(); } catch (e) { notify("err", reasonOf(e)); }
  };
  return (
    <span className="form-error" role="alert">
      {repoId ? `${repoId}: ${repo ? (STATUS[repo.health] ?? repo.health) : "weights missing"}` : text}
      {repoId && (
        <span className="problem-acts">
          {drift && <button onClick={() => void run(() => repairCacheRef(repoId), "Ref repaired; try again.")}>Repair</button>}
          {!drift && <button onClick={() => void run(() => fetchCache({ repo_id: repoId }), "Fetch queued; the dock shows its progress.")}>Fetch</button>}
          <button onClick={openModels}>Models</button>
        </span>
      )}
    </span>
  );
}
