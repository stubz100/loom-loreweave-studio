import { useEffect, useState } from "react";
import { getHealth, type Health } from "./lib/orchestrator";

// M0 shell — the three-pane layout + Job Queue dock from kb-loom-p0.md §10,
// rendered as a static skeleton. The only live behaviour at M0 is the
// orchestrator health probe (proves the shell <-> orchestrator handshake from
// the UI side). Generate bar, grid, queue, inspector are non-functional mockups
// until M1+.

type Conn = "connecting" | "online" | "offline";

export default function App() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const h = await getHealth();
        if (!alive) return;
        setHealth(h);
        setConn("online");
      } catch {
        if (!alive) return;
        setConn("offline");
      }
    };
    probe();
    const id = setInterval(probe, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const dot = conn === "online" ? "ok" : conn === "offline" ? "err" : "warn";

  return (
    <div className="app">
      <header className="titlebar">
        <span className="title">Loreweave Studio</span>
        <span className="sep">—</span>
        <span className="project">Sandbox</span>
        <span className="spacer" />
        <span className={`status dot-${dot}`}>
          <i className="dot" /> orchestrator: {conn}
          {health ? ` · v${health.app_version} · pid ${health.pid}` : ""}
        </span>
      </header>

      <div className="panes">
        <nav className="rail">
          <div className="rail-head">NAVIGATOR</div>
          <div className="muted">(empty in P0)</div>
        </nav>

        <main className="stage">
          <div className="generate-bar">
            <label>
              pipeline
              <select disabled>
                <option>zimage</option>
              </select>
            </label>
            <label>
              mode
              <select disabled>
                <option>t2i</option>
              </select>
            </label>
            <input className="prompt" placeholder="prompt (wired in M1)" disabled />
            <label className="n">
              N<input type="number" defaultValue={3} disabled />
            </label>
            <button disabled>Generate ▶</button>
          </div>
          <div className="grid">
            <div className="cell ph" />
            <div className="cell ph" />
            <div className="cell ph" />
          </div>
          <p className="muted center">Result grid — the casting-grid embryo (M2).</p>
        </main>

        <aside className="inspector">
          <div className="rail-head">INSPECTOR</div>
          <div className="muted">Select an image to see job details + lineage (M2/M5).</div>
        </aside>
      </div>

      <footer className="dock">
        <span className={`status dot-${dot}`}>
          <i className="dot" /> JOB QUEUE ⏸ paused (0)
        </span>
        <span className="meter">VRAM 0.0/16.0G</span>
        <span className="meter">disk —/250G</span>
        <button disabled>unpause</button>
      </footer>
    </div>
  );
}
