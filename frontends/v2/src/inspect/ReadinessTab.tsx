// Inspector · Readiness (kb-loom-ui.md §3.5, migration step 6): the four advisory tiers over
// the curated set as rows with their details INLINE — missing cells as chips, duplicate groups
// and outliers as thumbnails — instead of v1's tooltips. It recommends, it never gates (R14).
import { useCallback, useEffect, useState } from "react";

import {
  getReadiness, persistReadiness, queueReadinessEmbed, refUrl, type Readiness,
} from "@loom/shared/api/orchestrator";

import { nice } from "../lib/coverage";
import { reasonOf } from "../lib/project";
import { useApp } from "../store";

const STATUS: Record<string, string> = { ok: "ok", warn: "warn", info: "info", not_run: "not run" };

export function ReadinessTab() {
  const assetId = useApp((s) => s.selectedAsset);
  const detail = useApp((s) => s.assetDetail);
  const offline = useApp((s) => s.offline);
  const jobs = useApp((s) => s.jobs);
  const notify = useApp((s) => s.notify);
  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const versionId = version?.id ?? null;
  const [ready, setReady] = useState<Readiness | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [scanJob, setScanJob] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!assetId || !versionId) return;
    try { setReady(await getReadiness(assetId, versionId)); setProblem(null); }
    catch (e) { setProblem(reasonOf(e)); }
  }, [assetId, versionId]);
  useEffect(() => { setReady(null); setScanJob(null); if (!offline && version && version.ref_set.length > 0) void load(); }, [load, offline, version?.ref_set.length]);   // eslint-disable-line react-hooks/exhaustive-deps

  // The on-model scan closes through the one poller: when its job is done, harvest + persist.
  useEffect(() => {
    if (!scanJob || !assetId) return;
    const j = jobs[scanJob];
    if (!j || j.status === "queued" || j.status === "running") return;
    setScanJob(null);
    if (j.status === "done") {
      persistReadiness(assetId, versionId ?? undefined, scanJob).then((r) => { setReady(r); notify("ok", "On-model scan persisted."); }).catch((e) => setProblem(reasonOf(e)));
    } else {
      setProblem(`The on-model scan ${j.status}${j.result?.error ? `: ${j.result.error}` : "."}`);
    }
  }, [jobs, scanJob, assetId, versionId, notify]);

  if (!assetId || !version) {
    return <><div className="section-title">Readiness</div><p className="muted">Select a character. Its curated set is measured here before training.</p></>;
  }
  if (version.ref_set.length === 0) {
    return <><div className="section-title">Readiness</div><p className="muted">No curated refs yet. Keep cells in Curate first; the meter reads the ref set.</p></>;
  }
  const fileOf = (refId: string) => version.ref_set.find((r) => r.id === refId)?.file;
  const thumbs = (ids: string[]) => (
    <div className="ready-thumbs">
      {ids.map((id) => { const f = fileOf(id); return f ? <img key={id} src={refUrl(assetId, f, version.id)} alt="" title={f} /> : <span key={id} className="pill">{id}</span>; })}
    </div>
  );
  const onScan = async () => {
    setBusy(true); setProblem(null);
    try { const r = await queueReadinessEmbed(assetId, version.id); setScanJob(r.job_id); notify("ok", `On-model scan queued over ${r.ref_count} refs${r.anchor ? " against the anchor" : " against the set centroid"}.`); }
    catch (e) { setProblem(reasonOf(e)); } finally { setBusy(false); }
  };

  return (
    <>
      <div className="section-title">Readiness</div>
      {!ready && !problem && <p className="faint">Measuring…</p>}
      {ready && (() => {
        const cov = ready.coverage; const dup = ready.dupes; const cap = ready.captions; const om = ready.on_model; const adv = ready.advisory;
        const missing = Object.entries(cov.axes).filter(([, v]) => v.missing.length);
        return (
          <>
            <p className={`ready-verdict ${adv.recommended ? "ok" : "warn"}`}>
              {adv.recommended ? "Looks good to train." : `Not yet recommended: ${adv.reasons.join("; ") || adv.status}.`} <span className="faint">Advisory only; Train stays enabled.</span>
            </p>
            {(adv.notes ?? []).map((n) => <p key={n} className="faint">{n}</p>)}

            <div className={`ready-row s-${cov.status}`}>
              <div className="ready-head"><b>Coverage</b> <span className="pill">{STATUS[cov.status] ?? cov.status}</span> <span className="muted">{Math.round(cov.score * 100)}%, {cov.ref_count} refs over {cov.distinct_cells} cells</span></div>
              {missing.length > 0 ? missing.map(([axis, v]) => (
                <div key={axis} className="chips"><span className="faint">{nice(axis)} missing</span>{v.missing.map((m) => <span key={m} className="pill">{nice(m)}</span>)}</div>
              )) : <p className="faint">Every axis value is covered.</p>}
            </div>

            <div className={`ready-row s-${dup.status}`}>
              <div className="ready-head"><b>Duplicates</b> <span className="pill">{STATUS[dup.status] ?? dup.status}</span> <span className="muted">{dup.extras} extra in {dup.duplicate_groups.length} group{dup.duplicate_groups.length === 1 ? "" : "s"}{dup.cells_compared != null ? `, ${dup.cells_compared} of ${dup.cells_total} cells had two or more refs` : ""}</span></div>
              <p className="faint">Compared within a coverage cell only: refs asked for different poses are supposed to differ.</p>
              {dup.duplicate_groups.map((g, i) => <div key={i} className="ready-group">{thumbs(g)}</div>)}
            </div>

            <div className={`ready-row s-${cap.status}`}>
              <div className="ready-head"><b>Captions</b> <span className="pill">{STATUS[cap.status] ?? cap.status}</span> <span className="muted">{cap.count}{cap.edited ? `, ${cap.edited} edited` : ""}{cap.missing_trigger.length ? `, ${cap.missing_trigger.length} missing the trigger` : ""}</span></div>
              {cap.missing_trigger.length > 0 && <><p className="faint">These captions do not contain the trigger token; the adapter may not bind to it. Edit them in the Captions view.</p>{thumbs(cap.missing_trigger)}</>}
            </div>

            <div className={`ready-row s-${om.status}`}>
              <div className="ready-head"><b>On-model</b> <span className="pill">{STATUS[om.status] ?? om.status}</span>
                <span className="muted">{om.status === "not_run" ? "not scanned" : `${om.mode}, mean cos ${om.mean_cos ?? "?"}, ${om.scored ?? 0} scored${om.faces != null ? `, ${om.faces} faces` : ""}`}</span>
              </div>
              {om.anchor_status === "no_face" && <p className="faint">The anchor is set but no face was found in it, so refs were scored against the set centroid. Harmless: the anchor's job is generation support.</p>}
              {om.status !== "not_run" && <p className="faint">Outliers are judged against what a ref's own coverage cell should score; shot size, angle and expression each shift a face embedding.</p>}
              {(om.outliers?.length ?? 0) > 0 && <><p className="faint">{om.outliers!.length} outlier{om.outliers!.length === 1 ? "" : "s"} to review:</p>{thumbs(om.outliers!)}</>}
              <div className="jt-row">
                <button onClick={() => void onScan()} disabled={busy || !!scanJob || offline || version.finalized} title="a CPU identity job that embeds every ref; no image is made">{scanJob ? "Scanning…" : "Scan on-model"}</button>
                <button onClick={() => void load()} disabled={offline}>Refresh</button>
                {ready.persisted_at && <span className="faint">persisted {ready.persisted_at.slice(0, 16).replace("T", " ")}</span>}
              </div>
            </div>
          </>
        );
      })()}
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </>
  );
}
