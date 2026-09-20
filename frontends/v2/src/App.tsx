// Loreweave Studio v2 — the new frontend, built from scratch on the same orchestrator API
// (kb-loom-ui.md, 2026-09-20). This is the M2.14 seed: it proves the wiring (shared API client,
// the shell's token handshake, its own dev port) and nothing else. The frame — top bar with
// workspace tabs, icon rail + Panel, Strip, Canvas, tabbed Inspector, growable Dock — is
// migration step 1 of the plan (§6) and lands here next.

import { useEffect, useState } from "react";

import { orchestratorUrl, getHealth, type Health } from "@loom/shared/api/orchestrator";
import { log } from "@loom/shared/api/log";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const h = await getHealth();
        if (alive) { setHealth(h); setError(null); }
      } catch (e) {
        if (alive) { setHealth(null); setError(String(e)); }
      }
    };
    void probe();
    const t = setInterval(() => void probe(), 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => { log.info("v2 seed mounted →", orchestratorUrl()); }, []);

  return (
    <div className="seed">
      <h1>Loreweave Studio <span className="tag">v2</span></h1>
      <p className="muted">
        The new frontend, built from scratch on the same API as v1. The frame from
        <code> .docs/kb-loom-ui.md </code>§4 lands here as migration step 1.
      </p>
      <dl>
        <dt>orchestrator</dt>
        <dd>{orchestratorUrl()}</dd>
        <dt>status</dt>
        <dd>
          {health
            ? <span className="ok">● online · v{health.app_version} · schema {health.schema_version} · up {Math.round(health.uptime_s)} s</span>
            : <span className="err">● offline{error ? ` — ${error}` : ""}</span>}
        </dd>
      </dl>
    </div>
  );
}
