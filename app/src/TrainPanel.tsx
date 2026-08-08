// Stage D · Train — the P2/M2 staged-LoRA panel (M2.9a; the M2-owed "[Train LoRA]" surface).
//
// R118 staged-job semantics, surfaced literally: [⚙ Stage] materializes captions/policy/
// context/dataset/train.yaml + a durable `jobs/staged.json` record but NEVER queues;
// [▶ Add to queue] is the explicit transition — the first moment GPU work can start.
// Promote-on-success is M6; this panel is stage → queue → watch (cancel like any job).
// Dedicated component (pre-M1 review #4: new feature families stay OUT of the App.tsx
// monolith).

import { useCallback, useEffect, useState } from "react";
import {
  CaptionsResponse,
  Job,
  LoraInfo,
  PreviewPose,
  Readiness,
  StagedTraining,
  cleanupTrainingRun,
  clearCaptionOverride,
  deleteJob,
  deleteStagedTraining,
  getCaptions,
  getJob,
  getPreviewPoses,
  getReadiness,
  getStagedTraining,
  persistReadiness,
  poseIconUrl,
  previewTrainedLora,
  promoteTrainedLora,
  queueReadinessEmbed,
  queueStagedTraining,
  setCaptionOverride,
  stageZimageLora,
} from "./lib/orchestrator";

interface TrainPanelProps {
  assetId: string;
  assetName: string;
  versionId: string;
  versionName?: string;
  versionLocked: boolean;
  refCount: number;                     // curated refs (Stage C) — the training corpus
  lora: LoraInfo | null;                // M6: the version's promoted LoRA (null = untrained)
  trainJobs: Job[];                     // zimage_trainer jobs for THIS version
  onCancelJob: (jobId: string) => void;
  onPromoted: () => void;               // M6: reload the asset detail (version.lora flipped)
  onError: (msg: string | null) => void;
}

export default function TrainPanel({
  assetId, assetName, versionId, versionName, versionLocked, refCount, lora,
  trainJobs, onCancelJob, onPromoted, onError,
}: TrainPanelProps) {
  const [staged, setStaged] = useState<StagedTraining[]>([]);
  const [trigger, setTrigger] = useState("");
  const [steps, setSteps] = useState("");   // blank → the M1-accepted default (500)
  // "advanced ⚙" (M5 pull-forward slice): the four knobs the staging endpoint already takes.
  // Blank = the M1-accepted preset value; everything else stays preset-pinned until M5
  // (quantize/low_vram/optimizer are rig-safety-sensitive on 16 GB — deliberately not here).
  const [adv, setAdv] = useState(false);
  const [rank, setRank] = useState("");     // 1–256 (preset 16)
  const [alpha, setAlpha] = useState("");   // 1–256 (preset 16)
  const [lr, setLr] = useState("");         // (0, 1] (preset 0.0001)
  const [res, setRes] = useState("");       // 256–2048 ÷16 (preset 512)
  // M5 train options: base family (sd35 spike-gated server-side) + R68 train-init.
  const [family, setFamily] = useState<"zimage" | "sd35">("zimage");
  const [trainInit, setTrainInit] = useState<"from_base" | "seed_parent">("from_base");
  // "stage" or a staged id — only the in-flight action's controls lock, not the whole panel
  const [busy, setBusy] = useState<string | null>(null);
  // M3 captions review/edit: collapsed by default; drafts hold per-row unsaved text.
  const [capsOpen, setCapsOpen] = useState(false);
  const [caps, setCaps] = useState<CaptionsResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // M4 readiness meter (advisory): live view + the on-model scan poll.
  const [readyOpen, setReadyOpen] = useState(false);
  const [ready, setReady] = useState<Readiness | null>(null);
  const [scanJob, setScanJob] = useState<string | null>(null);
  // Rig feedback 2026-07-13: cleanup succeeded server-side but LOOKED like a failure —
  // nothing visible changes. Per-row confirmation notes fix that.
  const [rowNote, setRowNote] = useState<Record<string, string>>({});
  // Rig feedback 2026-07-15: the fixed-prompt 1024² preview couldn't answer "is the
  // adapter working?" — inline options (prompt/seed/size/weight + a same-seed A/B).
  const [prevOpen, setPrevOpen] = useState<string | null>(null);
  const [prevPrompt, setPrevPrompt] = useState("");
  const [prevSeed, setPrevSeed] = useState("12345");
  const [prevSize, setPrevSize] = useState("");     // blank = the TRAINED resolution
  const [prevWeight, setPrevWeight] = useState(""); // blank = the run's default (1.0)
  // Author request 2026-08-08: the framing is a PICK, not a fixed portrait. Icons come from
  // the L1 · Poses set (M2.11) — four of the five are real coverage cells, so they usually
  // already have one; `t_pose` is out-of-vocabulary and falls back to a text chip.
  const [poses, setPoses] = useState<PreviewPose[]>([]);
  const [prevPose, setPrevPose] = useState("");

  const refresh = useCallback(async () => {
    try {
      setStaged((await getStagedTraining()).staged);
    } catch {
      /* transient — the panel re-fetches on the next action */
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh, assetId, versionId]);

  // The pose menu is version-scoped (its prompts carry this version's trigger token).
  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const r = await getPreviewPoses(assetId, versionId);
        if (!live) return;
        setPoses(r.poses);
        setPrevPose((p) => p || r.default);
      } catch {
        /* the picker just stays empty — the prompt field still works */
      }
    })();
    return () => { live = false; };
  }, [assetId, versionId]);

  const loadCaptions = useCallback(async () => {
    try {
      setCaps(await getCaptions(assetId, versionId));
      setDrafts({});
    } catch (e) { onError(String(e)); }
  }, [assetId, versionId, onError]);
  useEffect(() => {
    setCaps(null); setDrafts({});
    if (capsOpen) void loadCaptions();
  }, [capsOpen, loadCaptions]);

  const loadReadiness = useCallback(async () => {
    try {
      setReady(await getReadiness(assetId, versionId));
    } catch (e) { onError(String(e)); }
  }, [assetId, versionId, onError]);
  useEffect(() => {
    setReady(null); setScanJob(null);
    if (readyOpen) void loadReadiness();
  }, [readyOpen, loadReadiness]);
  // On-model scan poll: job done → harvest+persist (the client closes the loop), then
  // the fresh snapshot replaces the view. Failure surfaces on the error bar.
  useEffect(() => {
    if (!scanJob) return;
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const j = await getJob(scanJob);
          if (!j || j.status === "queued" || j.status === "running") return;
          window.clearInterval(t);
          setScanJob(null);
          if (j.status === "done") {
            setReady(await persistReadiness(assetId, versionId, scanJob));
          } else {
            onError(`on-model scan ${j.status}${j.result?.error ? `: ${j.result.error}` : ""}`);
          }
        } catch (e) { window.clearInterval(t); setScanJob(null); onError(String(e)); }
      })();
    }, 3000);
    return () => window.clearInterval(t);
  }, [scanJob, assetId, versionId, onError]);

  const mine = staged.filter((s) => s.version_id === versionId);
  const canStage = !versionLocked && refCount > 0 && busy !== "stage";

  const clampSteps = () => {   // backend bound is 1–10000; keep what's shown = what's sent
    if (steps.trim()) setSteps(String(Math.min(10000, Math.max(1, Number(steps)))));
  };
  // Advanced-knob clamps mirror the backend bounds (keep what's shown = what's sent).
  const clampInt = (v: string, set: (s: string) => void, lo: number, hi: number) => {
    if (v.trim()) set(String(Math.min(hi, Math.max(lo, Number(v)))));
  };
  const clampRes = () => {     // 256–2048, snapped to /16 (the catalog dimension rule)
    if (res.trim()) {
      const n = Math.min(2048, Math.max(256, Number(res)));
      setRes(String(Math.round(n / 16) * 16));
    }
  };
  const clampLr = () => {      // (0, 1] float; junk or ≤0 clears back to the preset
    if (!lr.trim()) return;
    const n = Number(lr);
    if (!Number.isFinite(n) || n <= 0) setLr("");
    else if (n > 1) setLr("1");
  };
  const onStage = async () => {
    setBusy("stage"); onError(null);
    try {
      await stageZimageLora(assetId, {
        version_id: versionId,
        trigger_token: trigger.trim() || undefined,
        steps: steps.trim() ? Math.min(10000, Math.max(1, Number(steps))) : undefined,
        rank: rank.trim() ? Number(rank) : undefined,
        alpha: alpha.trim() ? Number(alpha) : undefined,
        learning_rate: lr.trim() ? Number(lr) : undefined,
        resolution: res.trim() ? Number(res) : undefined,
        base_family: family,
        train_init: trainInit,
      });
      setTrigger(""); setSteps("");
      setRank(""); setAlpha(""); setLr(""); setRes("");
      await refresh();
      if (capsOpen) await loadCaptions();   // staging may change the trigger → templates
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onSaveCaption = async (refId: string) => {
    const text = (drafts[refId] ?? "").trim();
    if (!text) return;
    setBusy(`cap:${refId}`); onError(null);
    try {
      await setCaptionOverride(assetId, refId, text, versionId);
      await loadCaptions();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  // M6 actions on a DONE trainer run: preview (P2-11) / promote (Stage E) / cleanup (R13).
  const previewBody = () => {
    const size = prevSize.trim() ? Math.round(Math.min(2048, Math.max(256, Number(prevSize))) / 16) * 16 : undefined;
    return {
      // A typed prompt is the override; otherwise the picked pose supplies the framing.
      pose: prevPrompt.trim() ? undefined : (prevPose || undefined),
      prompt: prevPrompt.trim() || undefined,
      seed: prevSeed.trim() ? Math.max(0, Number(prevSeed)) : undefined,
      width: size, height: size,
      lora_weight: prevWeight.trim() ? Math.min(4, Math.max(0, Number(prevWeight))) : undefined,
    };
  };
  const poseOf = (id: string) => poses.find((p) => p.id === id);
  const onPreview = async (jobId: string, ab = false) => {
    setBusy(`m6:${jobId}`); onError(null);
    try {
      // the sample streams into the Stage-D grid below via App's normal poll
      const r = await previewTrainedLora(jobId, previewBody());
      const framing = prevPrompt.trim() ? "custom prompt" : (poseOf(prevPose)?.label ?? prevPose);
      let note = `🖼 preview ${r.job_id} queued → the grid below (${framing})`;
      if (ab) {
        const base = await previewTrainedLora(jobId, { ...previewBody(), with_lora: false });
        note = `⚖ A/B queued → the grid below (${framing}): ${r.job_id} (LoRA) vs ${base.job_id} (base, same seed)`;
      }
      setRowNote((n) => ({ ...n, [jobId]: note }));
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onPromote = async (jobId: string) => {
    setBusy(`m6:${jobId}`); onError(null);
    try {
      await promoteTrainedLora(jobId);
      onPromoted();          // version.lora flipped — reload the asset detail
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onCleanup = async (jobId: string) => {
    if (!window.confirm("Delete this run's _temp/ dir (dataset copy, checkpoints, config)? Promote first if you want to keep the adapter.")) return;
    setBusy(`m6:${jobId}`); onError(null);
    try {
      const r = await cleanupTrainingRun(jobId);
      setRowNote((n) => ({ ...n, [jobId]: r.cleaned ? "🧹 temp cleaned ✓" : "🧹 already clean" }));
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onRemoveRow = async (jobId: string) => {
    if (!window.confirm("Remove this trainer job from the list? (Its _temp/ dir is cleaned first; the job record, log and trainer manifest are deleted.)")) return;
    setBusy(`m6:${jobId}`); onError(null);
    try {
      try { await cleanupTrainingRun(jobId); } catch { /* temp may already be gone */ }
      await deleteJob(jobId);   // the row disappears via App's normal poll
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onScanOnModel = async () => {
    onError(null);
    try {
      const r = await queueReadinessEmbed(assetId, versionId);
      setScanJob(r.job_id);   // the poll effect takes it from here
    } catch (e) { onError(String(e)); }
  };
  const onResetCaption = async (refId?: string) => {
    if (!refId && !window.confirm("Reset ALL captions back to their templates?")) return;
    setBusy(refId ? `cap:${refId}` : "cap:all"); onError(null);
    try {
      await clearCaptionOverride(assetId, refId, versionId);
      await loadCaptions();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onQueue = async (stagedId: string) => {
    setBusy(stagedId); onError(null);
    try {
      await queueStagedTraining(stagedId);   // the job appears via App's normal poll
      await refresh();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };
  const onDelete = async (stagedId: string) => {
    if (!window.confirm("Delete this staged training run? (Its temp dataset/config stay in _temp/ until cleanup.)")) return;
    setBusy(stagedId); onError(null);
    try {
      await deleteStagedTraining(stagedId);
      await refresh();
    } catch (e) { onError(String(e)); } finally { setBusy(null); }
  };

  return (
    <div className="train-panel">
      <div className="train-head">
        <b>STAGE D · TRAIN</b>
        <span className="muted">
          {" "}— character LoRA for {assetName}{versionName ? ` · ${versionName}` : ""} (zimage;
          preset: 500 steps · rank/alpha 16/16 · 512px · bf16 · qfloat8 · AdamW 1e-4 — the
          M1-accepted default). Staging writes captions + context + dataset; queueing is explicit
          (R118) and the job is resumable (R88).
        </span>
      </div>

      {versionLocked && (
        <p className="muted">🔒 This version is finalized — unlock it (or duplicate to a new
          version) to stage training.</p>
      )}
      {!versionLocked && refCount === 0 && (
        <p className="muted">No curated refs yet — keep ✓ Stage-B candidates in Stage C first
          (the ref set is the training corpus).</p>
      )}

      <div className="train-stage-form">
        <label className="sm">
          base
          <select value={family} onChange={(e) => setFamily(e.target.value as "zimage" | "sd35")}
                  disabled={!canStage}
                  title="base model family the LoRA trains against. sd35 is behind the M5 ROCm spike front-gate — staging refuses until LOOM_TRAINER_SD35_GO stamps the rig spike.">
            <option value="zimage">zimage</option>
            <option value="sd35">sd35 ⚗</option>
          </select>
        </label>
        <label className="sm">
          init
          <select value={trainInit}
                  onChange={(e) => setTrainInit(e.target.value as "from_base" | "seed_parent")}
                  disabled={!canStage}
                  title="R68: train-from-base (default) or seed from the PARENT version's promoted LoRA (needs a derived version whose parent has a lora/ artifact — refused otherwise)">
            <option value="from_base">from base</option>
            <option value="seed_parent">seed parent</option>
          </select>
        </label>
        <label>
          trigger token
          <input
            value={trigger}
            placeholder="(auto from name)"
            onChange={(e) => setTrigger(e.target.value)}
            disabled={!canStage}
            title="unique per character/version; letters/digits/underscores, starts with a letter (blank = derived from the character name, e.g. mara_lw)"
          />
        </label>
        <label>
          steps
          <input
            value={steps}
            placeholder="500"
            inputMode="numeric"
            onChange={(e) => setSteps(e.target.value.replace(/[^0-9]/g, ""))}
            onBlur={clampSteps}
            disabled={!canStage}
            title="total training steps, 1–10000 (M1 accepted 500 for the fixed-set spike)"
          />
        </label>
        <button className="ghost" onClick={() => setAdv(!adv)}
                title="rank/alpha, learning rate, resolution — blank = the M1-accepted preset. The rig-safety knobs (quantize/low_vram/optimizer) stay preset-pinned until M5.">
          {adv ? "▾" : "▸"} advanced
        </button>
        {adv && (
          <>
            <label className="sm">
              rank
              <input value={rank} placeholder="16" inputMode="numeric"
                     onChange={(e) => setRank(e.target.value.replace(/[^0-9]/g, ""))}
                     onBlur={() => clampInt(rank, setRank, 1, 256)}
                     disabled={!canStage}
                     title="LoRA rank (network.linear), 1–256 — adapter capacity; 16 = the M1 preset. Higher learns more detail but risks overfitting a small ref set." />
            </label>
            <label className="sm">
              alpha
              <input value={alpha} placeholder="16" inputMode="numeric"
                     onChange={(e) => setAlpha(e.target.value.replace(/[^0-9]/g, ""))}
                     onBlur={() => clampInt(alpha, setAlpha, 1, 256)}
                     disabled={!canStage}
                     title="LoRA alpha (network.linear_alpha), 1–256 — effective scale = alpha/rank; the usual convention is alpha = rank (M1 preset 16/16)." />
            </label>
            <label className="sm">
              learning rate
              <input value={lr} placeholder="0.0001"
                     onChange={(e) => setLr(e.target.value.replace(/[^0-9.eE-]/g, ""))}
                     onBlur={clampLr}
                     disabled={!canStage}
                     title="optimizer learning rate, (0, 1] — 1e-4 = the M1-accepted AdamW preset. The single most impactful knob: lower = slower/safer, higher = faster/less stable." />
            </label>
            <label className="sm">
              resolution
              <input value={res} placeholder="512" inputMode="numeric"
                     onChange={(e) => setRes(e.target.value.replace(/[^0-9]/g, ""))}
                     onBlur={clampRes}
                     disabled={!canStage}
                     title="training resolution, 256–2048 (÷16) — 512 = the M1 preset validated on the 16 GB rig; higher squares the memory/time cost." />
            </label>
          </>
        )}
        <button className="ghost" onClick={() => void onStage()} disabled={!canStage}
                title="materialize captions/policy/context/dataset/train.yaml + a staged record — does NOT queue (R118)">
          ⚙ Stage · Train LoRA ({refCount} ref{refCount === 1 ? "" : "s"})
        </button>
      </div>

      {refCount > 0 && (
        <div className="train-captions">
          <button className="ghost" onClick={() => setReadyOpen(!readyOpen)}
                  title="advisory training-readiness proxies over the curated set — coverage, near-duplicates, captions, on-model (R14: recommends, never blocks)">
            {readyOpen ? "▾" : "▸"} readiness
            {ready ? (ready.advisory.recommended ? " ✅" : ` (${ready.advisory.status})`) : ""}
          </button>
          {readyOpen && ready && (() => {
            const icon = (s: string) => s === "ok" ? "✅" : s === "warn" ? "⚠️" : "ℹ️";
            const cov = ready.coverage;
            const missing = Object.entries(cov.axes)
              .filter(([, v]) => v.missing.length)
              .map(([axis, v]) => `${axis}: ${v.missing.join(", ")}`)
              .join(" · ");
            const om = ready.on_model;
            return (
              <div className="ready-tiers">
                <div className="ready-row" title={missing ? `missing — ${missing}` : "all axis values covered"}>
                  {icon(cov.status)} coverage {Math.round(cov.score * 100)}%
                  <span className="muted"> · {cov.ref_count} refs · {cov.distinct_cells} cells
                    {missing ? ` · missing ${missing}` : ""}</span>
                </div>
                <div className="ready-row"
                     title={ready.dupes.duplicate_groups.length
                       ? `duplicate groups: ${ready.dupes.duplicate_groups.map((g) => g.join(" ≈ ")).join(" | ")}`
                       : "no near-duplicates (dHash)"}>
                  {icon(ready.dupes.status)} duplicates
                  <span className="muted"> · {ready.dupes.extras} extra(s) in {ready.dupes.duplicate_groups.length} group(s)</span>
                </div>
                <div className="ready-row">
                  {icon(ready.captions.status)} captions
                  <span className="muted"> · {ready.captions.count}
                    {ready.captions.edited ? ` (${ready.captions.edited} edited)` : ""}
                    {ready.captions.missing_trigger.length
                      ? ` · ⚠ ${ready.captions.missing_trigger.length} missing trigger` : ""}</span>
                </div>
                <div className="ready-row">
                  {om.status === "not_run" ? "ℹ️" : icon(om.status)} on-model
                  <span className="muted">
                    {om.status === "not_run"
                      ? " · not scanned"
                      : ` · ${om.mode} · mean cos ${om.mean_cos ?? "?"} · ${om.scored ?? 0} scored` +
                        ((om.outliers?.length ?? 0) > 0 ? ` · ${om.outliers!.length} outlier(s)` : "")}
                  </span>
                  <button className="ghost" onClick={() => void onScanOnModel()}
                          disabled={!!scanJob || versionLocked}
                          title="queue the face-embedding scan (CPU identity job; anchor-cosine when an anchor is set, else set-centroid — R120). Advisory only.">
                    {scanJob ? "⏳ scanning…" : "🔬 scan"}
                  </button>
                  <button className="ghost" onClick={() => void loadReadiness()} title="refresh the live view">↻</button>
                </div>
                <div className="ready-row muted">
                  {ready.advisory.recommended
                    ? "✅ looks good to train (advisory — your call either way)"
                    : `advisory: ${ready.advisory.reasons.join(" · ") || ready.advisory.status}`}
                </div>
              </div>
            );
          })()}
          <button className="ghost" onClick={() => setCapsOpen(!capsOpen)}
                  title="review/edit the template captions the trainer will see — an edit is a durable override on this version (survives re-staging); reset returns a row to its template">
            {capsOpen ? "▾" : "▸"} captions
            {caps ? ` (${caps.count}${caps.edited_count ? ` · ${caps.edited_count} edited` : ""})` : ""}
          </button>
          {capsOpen && caps && (
            <>
              <div className="muted cap-hint">
                template: <code>&lt;trigger&gt;, &lt;angle&gt;, &lt;shot&gt;, &lt;expression&gt;[, &lt;bg&gt; background]</code>
                {" "}· trigger <b>{caps.trigger_token}</b> — edits are saved on the version;
                the next ⚙ Stage bakes them into the dataset (captions_hash reflects them).
                {caps.edited_count > 0 && !versionLocked && (
                  <button className="ghost" onClick={() => void onResetCaption()}
                          disabled={busy === "cap:all"}
                          title="drop every override — all rows return to their template text">
                    ↺ reset all
                  </button>
                )}
              </div>
              {caps.captions.map((c) => {
                const draft = drafts[c.id];
                const dirty = draft !== undefined && draft.trim() !== c.caption;
                return (
                  <div className="cap-row" key={c.id}>
                    <span className="cap-file muted" title={`${c.file} · ${Object.values(c.coverage_cell ?? {}).filter(Boolean).join(" · ")}`}>
                      {c.file.replace(/\.[a-z0-9]+$/i, "")}
                    </span>
                    <input
                      className="cap-input"
                      value={draft ?? c.caption}
                      onChange={(e) => setDrafts({ ...drafts, [c.id]: e.target.value })}
                      disabled={versionLocked || busy === `cap:${c.id}`}
                      title={c.origin === "edited" ? `edited — template was: ${c.template_caption}` : "template caption (edit + 💾 to override)"}
                    />
                    {c.origin === "edited" && <span title="edited (override)">✎</span>}
                    {!c.has_trigger && (
                      <span title={`⚠ the trigger token "${caps.trigger_token}" is missing from this caption — the LoRA may not bind to it (advisory)`}>⚠</span>
                    )}
                    {dirty && !versionLocked && (
                      <button className="ghost" onClick={() => void onSaveCaption(c.id)}
                              disabled={busy === `cap:${c.id}` || !(draft ?? "").trim()}
                              title="save as a durable override on this version">
                        💾
                      </button>
                    )}
                    {c.origin === "edited" && !versionLocked && (
                      <button className="ghost" onClick={() => void onResetCaption(c.id)}
                              disabled={busy === `cap:${c.id}`}
                              title="drop the override — back to the template text">
                        ↺
                      </button>
                    )}
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}

      {mine.length > 0 && (
        <div className="train-staged">
          <div className="muted">STAGED (not queued — GPU starts only on ▶):</div>
          {mine.map((s) => (
            <div className="train-row" key={s.id}>
              <span className="train-row-main">
                <b>{s.trigger_token}</b>
                <span className="muted">
                  {" "}· {s.base_family ?? "zimage"}
                  {s.train_init === "seed_parent" ? " (seeded)" : ""}
                  {" "}· {s.caption_count} caption{s.caption_count === 1 ? "" : "s"}
                  {" "}· {String((s.settings ?? {}).steps ?? "?")} steps
                  {" "}· r{String((s.settings ?? {}).rank ?? "?")}/a{String((s.settings ?? {}).alpha ?? "?")}
                  {" "}· {String((s.settings ?? {}).resolution ?? "?")}px
                  {" "}· lr {String((s.settings ?? {}).learning_rate ?? "?")}
                  {" "}· {s.id}
                  {s.context_digest ? ` · ctx ${s.context_digest.slice(0, 8).toLowerCase()}` : ""}
                </span>
              </span>
              <button className="ghost" onClick={() => void onQueue(s.id)} disabled={busy === s.id}
                      title="add to the GPU queue as a resumable trainer job (the explicit R118 transition)">
                ▶ Add to queue
              </button>
              <button className="ghost" onClick={() => void onDelete(s.id)} disabled={busy === s.id}
                      title="drop the staged record (no queued/running job is touched)">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {trainJobs.length > 0 && (
        <div className="train-jobs">
          <div className="muted">TRAINING JOBS (this version):</div>
          {trainJobs.map((j) => (
            <div key={j.id}>
            <div className="train-row">
              <span className="train-row-main">
                {j.status === "done" ? "✅" : j.status === "failed" ? "❌"
                  : j.status === "canceled" ? "🚫" : j.status === "running" ? "⏳" : "🕐"}{" "}
                <b>{j.id}</b>
                <span className="muted">
                  {" "}· {j.status}
                  {j.status === "running" ? ` · ${Math.round((j.progress || 0) * 100)}%` : ""}
                  {j.note ? ` · ${j.note}` : ""}
                  {j.result?.error && j.status === "failed" ? ` · ${j.result.error}` : ""}
                  {rowNote[j.id] ? ` · ${rowNote[j.id]}` : ""}
                </span>
              </span>
              {(j.status === "queued" || j.status === "running") && (
                <button className="ghost" onClick={() => onCancelJob(j.id)}
                        title="cancel the trainer job (resumable jobs recover their checkpoint on a re-queue)">
                  ✕ cancel
                </button>
              )}
              {j.status === "done" && (
                <>
                  <button className="ghost"
                          onClick={() => setPrevOpen(prevOpen === j.id ? null : j.id)}
                          disabled={busy === `m6:${j.id}`}
                          title="P2-11: sample generations with the FRESH un-promoted adapter (from the run dir) — eyeball the character before promoting. Opens prompt/seed/size/weight options + a same-seed A/B vs the bare base.">
                    🖼 preview {prevOpen === j.id ? "▾" : "▸"}
                  </button>
                  <button className="ghost" onClick={() => void onPromote(j.id)}
                          disabled={busy === `m6:${j.id}` || versionLocked}
                          title="Stage E: copy the adapter into versions/<v>/lora/ + write lora.manifest.json (dataset/caption/context hashes) + set version.lora — temp stays until 🧹">
                    ⬆ promote
                  </button>
                  <button className="ghost" onClick={() => void onCleanup(j.id)}
                          disabled={busy === `m6:${j.id}`}
                          title="R13 manual cleanup: delete this run's _temp/ dir (idempotent; promote first to keep the adapter)">
                    🧹
                  </button>
                </>
              )}
              {(j.status === "failed" || j.status === "canceled") && (
                <>
                  <button className="ghost" onClick={() => void onCleanup(j.id)}
                          disabled={busy === `m6:${j.id}`}
                          title="R13 manual cleanup: delete this run's _temp/ dir (the row stays)">
                    🧹
                  </button>
                  <button className="ghost" onClick={() => void onRemoveRow(j.id)}
                          disabled={busy === `m6:${j.id}`}
                          title="remove this dead run entirely: temp cleaned + the job record/log/manifest deleted — the row disappears">
                    🗑
                  </button>
                </>
              )}
            </div>
            {prevOpen === j.id && j.status === "done" && (
              <>
              {poses.length > 0 && (
                <div className="train-row prev-pose-row">
                  <span className="sm muted">pose</span>
                  {poses.map((p) => (
                    <button key={p.id}
                            className={`prev-pose ${prevPose === p.id && !prevPrompt.trim() ? "sel" : ""}`}
                            onClick={() => setPrevPose(p.id)}
                            disabled={!!prevPrompt.trim()}
                            title={`${p.prompt}${p.in_vocabulary ? "" : "\n\n⚠ T-pose is NOT in the frozen coverage vocabulary, so the training set contains no T-pose images — the base model supplies the pose and the adapter only the identity. Expect this one to read weakest."}`}>
                      {p.has_icon
                        ? <img src={poseIconUrl(p.pose_key)} alt={p.label} />
                        : <span className="prev-pose-ph">no icon</span>}
                      <span className="prev-pose-label">
                        {p.label}{p.in_vocabulary ? "" : " ⚠"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
              <div className="train-row prev-opts">
                <input className="prev-prompt" value={prevPrompt}
                       placeholder={poseOf(prevPose)?.prompt
                         ? `(pose: ${poseOf(prevPose)!.prompt} — type here to override)`
                         : "(include the trigger token!)"}
                       onChange={(e) => setPrevPrompt(e.target.value)}
                       title="free-text override — wins over the pose picker above. The character only appears when the TRIGGER TOKEN is in the prompt." />
                <label className="sm">seed
                  <input value={prevSeed} inputMode="numeric"
                         onChange={(e) => setPrevSeed(e.target.value.replace(/[^0-9]/g, ""))}
                         title="fixed seed — keep it constant to compare weights / A vs B" />
                </label>
                <label className="sm">size
                  <input value={prevSize} inputMode="numeric" placeholder="trained"
                         onChange={(e) => setPrevSize(e.target.value.replace(/[^0-9]/g, ""))}
                         title="square render size (÷16). Blank = the TRAINED resolution — most faithful to what the adapter learned (and fastest)" />
                </label>
                <label className="sm">weight
                  <input value={prevWeight} placeholder="1.0"
                         onChange={(e) => setPrevWeight(e.target.value.replace(/[^0-9.]/g, ""))}
                         title="adapter scale 0–4: raise (1.2–1.5) if the identity is weak, 0 = effectively off" />
                </label>
                <button className="ghost" onClick={() => void onPreview(j.id)}
                        disabled={busy === `m6:${j.id}`}
                        title="one sample with the adapter — streams into the version grid">
                  ▶
                </button>
                <button className="ghost" onClick={() => void onPreview(j.id, true)}
                        disabled={busy === `m6:${j.id}`}
                        title="A/B: TWO samples at the same seed — with the adapter vs the bare base model. If they look the same, the adapter isn't carrying signal (undertrained).">
                  ⚖ A/B
                </button>
              </div>
              </>
            )}
            </div>
          ))}
        </div>
      )}

      {lora && (
        <p className="muted">
          ✨ promoted LoRA: <b>{lora.file}</b> · {lora.base_family}
          {lora.trigger_token ? ` · trigger ${lora.trigger_token}` : ""}
          {" "}· {new Date(lora.promoted_at).toLocaleString()} · sha {lora.sha256.slice(0, 8).toLowerCase()}
          {" "}(full record: {lora.manifest})
        </p>
      )}
    </div>
  );
}
