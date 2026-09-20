// Train mode (Stage D): the staging form as a column with Stage pinned at the foot. Staged
// runs and trainer jobs live in the dock's Training pane (they are jobs); the LoRA preview
// form opens here when a done run's Preview is pressed there (kb-loom-ui.md §3.6).
import { useEffect, useState } from "react";

import { stageZimageLora } from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";
import { useCompose } from "./composeStore";
import { LoraPreview } from "./LoraPreview";

export function TrainComposer() {
  const project = useApp((s) => s.project);
  const offline = useApp((s) => s.offline);
  const assetId = useApp((s) => s.selectedAsset)!;
  const detail = useApp((s) => s.assetDetail);
  const jobs = useApp((s) => s.jobs);
  const staged = useApp((s) => s.staged);
  const refreshStaged = useApp((s) => s.refreshStaged);
  const openDock = useApp((s) => s.openDock);
  const previewJobId = useApp((s) => s.previewJobId);
  const notify = useApp((s) => s.notify);
  const t = useCompose((s) => s.train);
  const patch = useCompose((s) => s.patchTrain);

  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const refs = version?.ref_set.length ?? 0;
  const locked = !!version?.finalized;
  const hasParent = !!version && detail!.versions.some((v) => v.id !== version.id && v.lora);
  const stagedHere = staged.filter((s) => s.version_id === version?.id).length;
  const runs = Object.values(jobs).filter((j) => j.pipeline === "zimage_trainer" && j.profile_version_id === version?.id);
  const active = runs.filter((j) => j.status === "queued" || j.status === "running").length;

  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { if (!offline) void refreshStaged(); }, [refreshStaged, offline, assetId]);

  const blocked = !project ? "Open a project first." : offline ? "The orchestrator is offline." : locked ? "This version is finalized; unlock or duplicate it." : refs === 0 ? "Curate at least one reference first." : null;
  const num = (v: string) => (v.trim() ? Number(v) : undefined);
  const onStage = async () => {
    setBusy(true); setProblem(null);
    try {
      const r = await stageZimageLora(assetId, {
        version_id: version?.id, base_family: t.family, train_init: t.init,
        trigger_token: t.trigger.trim() || undefined,
        steps: t.steps.trim() ? Math.min(10000, Math.max(1, Number(t.steps))) : undefined,
        rank: num(t.rank), alpha: num(t.alpha), learning_rate: num(t.lr), resolution: num(t.res),
      });
      notify("ok", `Staged a ${r.kind.replace(/_/g, " ")} run with ${r.caption_count} captions. Add it to the queue from the dock.`);
      await refreshStaged();
      openDock("training");
    } catch (e) {
      setProblem(reasonOf(e));
    } finally {
      setBusy(false);
    }
  };

  if (previewJobId && jobs[previewJobId]) {
    return (
      <>
        <div className="panel-body compose"><LoraPreview jobId={previewJobId} /></div>
        <div className="panel-foot"><span className="faint">Samples land on the Train canvas.</span></div>
      </>
    );
  }

  return (
    <>
      <div className="panel-body compose">
        <p className="faint">Train {detail?.profile.name ?? ""}: stage a character LoRA from the {refs} curated reference{refs === 1 ? "" : "s"}. Staging writes captions, context and the dataset; queueing is a separate, explicit step.</p>

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

        <div className="section-title">Runs</div>
        <p className="muted">{stagedHere} staged, {active} active, {runs.length - active} finished{version?.lora ? ", adapter promoted" : ""}.</p>
        <button onClick={() => openDock("training")}>Show in the dock</button>
        <p className="faint">Readiness is in the Inspector's Readiness tab; the captions are the Captions view on the canvas.</p>
      </div>

      <div className="panel-foot">
        {problem && <span className="form-error" role="alert">{problem}</span>}
        {blocked && !problem && <span className="faint">{blocked}</span>}
        <span className="spacer" />
        <button className="primary" onClick={() => void onStage()} disabled={!!blocked || busy}>
          {busy ? "Staging…" : "Stage the run"}
        </button>
      </div>
    </>
  );
}
