// Train mode (Stage D): the staging form as a column with Stage pinned at the foot, and the
// version's staged runs with Add to queue. Captions, readiness, previews and promote arrive
// with migration step 6 (captions view on the canvas, Readiness tab, job rows in the dock).
import { useCallback, useEffect, useState } from "react";

import {
  deleteStagedTraining, getStagedTraining, queueStagedTraining, stageZimageLora, type StagedTraining,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { useCompose } from "./composeStore";

export function TrainComposer() {
  const project = useApp((s) => s.project);
  const offline = useApp((s) => s.offline);
  const assetId = useApp((s) => s.selectedAsset)!;
  const detail = useApp((s) => s.assetDetail);
  const notify = useApp((s) => s.notify);
  const t = useCompose((s) => s.train);
  const patch = useCompose((s) => s.patchTrain);

  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const refs = version?.ref_set.length ?? 0;
  const locked = !!version?.finalized;
  const hasParent = !!version && detail!.versions.some((v) => v.id !== version.id && v.lora);

  const [staged, setStaged] = useState<StagedTraining[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);   // inline two-click remove, no native dialog
  const [problem, setProblem] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await getStagedTraining();
      setStaged(r.staged.filter((s) => s.asset_id === assetId));
    } catch { setStaged([]); }
  }, [assetId]);
  useEffect(() => { if (!offline) void refresh(); }, [refresh, offline]);

  const blocked = !project ? "Open a project first." : offline ? "The orchestrator is offline." : locked ? "This version is finalized; unlock or duplicate it." : refs === 0 ? "Curate at least one reference first." : null;

  const num = (v: string) => (v.trim() ? Number(v) : undefined);
  const onStage = async () => {
    setBusy("stage"); setProblem(null);
    try {
      const r = await stageZimageLora(assetId, {
        version_id: version?.id, base_family: t.family, train_init: t.init,
        trigger_token: t.trigger.trim() || undefined,
        steps: t.steps.trim() ? Math.min(10000, Math.max(1, Number(t.steps))) : undefined,
        rank: num(t.rank), alpha: num(t.alpha), learning_rate: num(t.lr), resolution: num(t.res),
      });
      notify("ok", `Staged a ${r.kind.replace(/_/g, " ")} run with ${r.caption_count} captions. Add it to the queue when ready.`);
      await refresh();
    } catch (e) {
      setProblem(reasonOf(e));
    } finally {
      setBusy(null);
    }
  };
  const onQueue = async (id: string) => {
    setBusy(id); setProblem(null);
    try {
      const r = await queueStagedTraining(id);
      notify("ok", `Training queued as ${r.job_id}. Progress shows in the dock.`);
      await refresh();
    } catch (e) {
      setProblem(reasonOf(e));
    } finally {
      setBusy(null);
    }
  };
  const onDelete = async (id: string) => {
    if (confirmId !== id) { setConfirmId(id); return; }
    setConfirmId(null);
    setBusy(id); setProblem(null);
    try {
      await deleteStagedTraining(id);
      await refresh();
    } catch (e) {
      setProblem(reasonOf(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="panel-body compose">
        <p className="faint">Train {detail?.profile.name ?? ""}: stage a character LoRA from the {refs} curated reference{refs === 1 ? "" : "s"}, then add it to the queue.</p>

        <div className="section-title">Run</div>
        <label className="p-field">Base model
          <select value={t.family} onChange={(e) => patch({ family: e.target.value as "zimage" | "sd35" })}>
            <option value="zimage">Z-Image (768 px preset)</option>
            <option value="sd35">SD3.5 Medium (512 px preset, 1.8× faster)</option>
          </select>
        </label>
        <label className="p-field">Start from
          <select value={t.init} onChange={(e) => patch({ init: e.target.value as "from_base" | "seed_parent" })}>
            <option value="from_base">the base model</option>
            <option value="seed_parent" disabled={!hasParent}>the parent version's promoted adapter{hasParent ? "" : " (none promoted)"}</option>
          </select>
        </label>
        <label className="p-field">Trigger token<input type="text" value={t.trigger} placeholder={version?.trigger_token ?? "derived from the name"} onChange={(e) => patch({ trigger: e.target.value })} /></label>
        <label className="p-field">Steps<input type="number" min={1} max={10000} value={t.steps} placeholder="preset (500)" onChange={(e) => patch({ steps: e.target.value })} /></label>
        <details className="p-group">
          <summary className="section-title">Advanced</summary>
          <div className="jt-row">
            <label className="p-field">Rank<input type="number" min={1} max={256} value={t.rank} placeholder="16" onChange={(e) => patch({ rank: e.target.value })} /></label>
            <label className="p-field">Alpha<input type="number" min={1} max={256} value={t.alpha} placeholder="16" onChange={(e) => patch({ alpha: e.target.value })} /></label>
          </div>
          <div className="jt-row">
            <label className="p-field">Learning rate<input type="number" min={0} max={1} step={0.00001} value={t.lr} placeholder="0.0001" onChange={(e) => patch({ lr: e.target.value })} /></label>
            <label className="p-field">Resolution<input type="number" min={256} max={2048} step={16} value={t.res} placeholder={t.family === "sd35" ? "512" : "768"} onChange={(e) => patch({ res: e.target.value })} /></label>
          </div>
          <p className="faint">An adapter holds identity only at the resolution it was trained at; previews default to it.</p>
        </details>

        <div className="section-title">Staged runs</div>
        {staged === null && <p className="faint">Loading…</p>}
        {staged?.length === 0 && <p className="faint">None yet. Stage one below.</p>}
        {staged?.map((s) => (
          <div key={s.id} className="staged-row">
            <div>
              <div>{s.trigger_token} <span className="faint">{s.version_name ?? s.version_id}</span></div>
              <div className="faint">{s.kind.replace(/_/g, " ")}, {s.caption_count} captions{s.settings?.steps ? `, ${String(s.settings.steps)} steps` : ""}</div>
            </div>
            <div className="jt-row">
              <button className="primary" onClick={() => void onQueue(s.id)} disabled={busy !== null || offline}>Add to queue</button>
              <button onClick={() => void onDelete(s.id)} onBlur={() => setConfirmId(null)} disabled={busy !== null}
                      title={confirmId === s.id ? "click again: the prepared dataset is deleted, the curated refs stay" : "remove this staged run"}>
                {confirmId === s.id ? "Remove?" : "✕"}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="panel-foot">
        {problem && <span className="form-error" role="alert">{problem}</span>}
        {blocked && !problem && <span className="faint">{blocked}</span>}
        <span className="spacer" />
        <button className="primary" onClick={() => void onStage()} disabled={!!blocked || busy !== null}>
          {busy === "stage" ? "Staging…" : "Stage the run"}
        </button>
      </div>
    </>
  );
}
