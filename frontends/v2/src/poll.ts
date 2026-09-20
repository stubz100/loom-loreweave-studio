// The one poller (kb-loom-ui.md §2: v1 had none of these guards). One in-flight request at a
// time, a stable 2 s cadence, state transitions turned into notices — and nothing else in the
// app talks to /health or /jobs on a timer.
import { useEffect } from "react";

import { getHealth, getProject, listJobs } from "@loom/shared/api/orchestrator";
import { log } from "@loom/shared/api/log";

import { useApp } from "./store";

const PERIOD_MS = 2000;

export function usePolling(): void {
  useEffect(() => {
    let inflight = false;
    let stopped = false;
    let wasOffline: boolean | null = null;
    let lastDiskState: string | null = null;

    const tick = async () => {
      if (inflight || stopped) return;
      inflight = true;
      const s = useApp.getState();
      try {
        const h = await getHealth();
        s.setOnline(h);
        if (wasOffline !== false) {
          if (wasOffline === true) s.notify("ok", "Connected to the orchestrator again.");
          wasOffline = false;
        }
        const [p, j] = await Promise.all([getProject(), listJobs()]);
        if (stopped) return;
        s.setProject(p.open ? p : null);
        s.applyJobs(j);
        if (useApp.getState().selectedAsset) await s.refreshAsset();   // hero, anchor, refs stay current
        const ds = j.disk?.state ?? null;
        if (ds && lastDiskState && ds !== lastDiskState) {
          if (ds === "hard") s.notify("err", "Disk hard-stop: new jobs are held until space is freed.");
          else if (ds === "warn") s.notify("warn", "Disk space is getting low.");
          else s.notify("ok", "Disk space is back to normal.");
        }
        lastDiskState = ds;
      } catch (e) {
        if (stopped) return;
        const msg = String(e);
        s.setOffline(msg);
        if (wasOffline === false) s.notify("err", "Lost the orchestrator. Reconnecting every 2 s.");
        if (wasOffline === null) log.warn("orchestrator not reachable yet:", msg);
        wasOffline = true;
      } finally {
        inflight = false;
      }
    };

    void tick();
    const t = setInterval(() => void tick(), PERIOD_MS);
    return () => { stopped = true; clearInterval(t); };
  }, []);
}
