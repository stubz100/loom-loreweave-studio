// Sticky states under the top bar — reachable from EVERY workspace (v1 showed these only
// inside the Assets column, so L1 never saw a paused queue or a disk stop).
import { unpauseQueue } from "@loom/shared/api/orchestrator";

import { useApp } from "../store";

export function Banners() {
  const offline = useApp((s) => s.offline);
  const lastError = useApp((s) => s.lastError);
  const paused = useApp((s) => s.paused);
  const pauseReason = useApp((s) => s.pauseReason);
  const counts = useApp((s) => s.counts);
  const disk = useApp((s) => s.disk);
  const notify = useApp((s) => s.notify);

  const items: { key: string; kind?: "err"; text: string; action?: { label: string; run: () => void } }[] = [];
  if (offline) {
    items.push({
      key: "offline", kind: "err",
      text: `The orchestrator is not answering${lastError ? ` (${lastError})` : ""}. Start it with loom-dev.ps1, or check that its CORS list includes this page's origin.`,
    });
  }
  if (!offline && paused && (counts?.queued ?? 0) > 0) {
    items.push({
      key: "paused",
      text: pauseReason === "resume"
        ? `The queue was paused when the project reopened with ${counts?.queued} pending job(s), so nothing runs until you say so.`
        : `The queue is paused with ${counts?.queued} job(s) waiting.`,
      action: { label: "Resume", run: () => { unpauseQueue().catch((e) => notify("err", `Resume failed: ${String(e)}`)); } },
    });
  }
  if (!offline && disk?.state === "hard") {
    items.push({ key: "disk", kind: "err", text: `Disk hard-stop: ${disk.reason ?? "no space left for new jobs"}. Free space or raise the project cap.` });
  }
  if (!items.length) return null;
  return (
    <div className="banners">
      {items.map((b) => (
        <div key={b.key} className={`banner${b.kind ? ` ${b.kind}` : ""}`} role="status">
          {b.text}
          {b.action && <button onClick={b.action.run}>{b.action.label}</button>}
        </div>
      ))}
    </div>
  );
}
