import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  generate,
  getHealth,
  listJobs,
  outputUrl,
  type Health,
  type Job,
} from "./lib/orchestrator";

// M2 shell — three-pane layout + Job Queue dock (kb-loom-p0.md §10). The stage is
// a generate bar + a simple selectable result grid (the smoke target / casting-grid
// embryo, §12). Batches are fired at the orchestrator, which serializes them through
// one GPU worker; the UI polls /jobs and streams each result into the grid.

type Conn = "connecting" | "online" | "offline";

export default function App() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [health, setHealth] = useState<Health | null>(null);

  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(3);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState({ queued: 0, running: 0, done: 0, failed: 0, canceled: 0 });
  const pollRef = useRef<number | null>(null);

  // Health probe (every 2 s).
  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const h = await getHealth();
        if (!alive) return;
        setHealth(h);
        setConn("online");
      } catch {
        if (alive) setConn("offline");
      }
    };
    probe();
    const id = window.setInterval(probe, 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  // Poll /jobs while anything in the current batch is still pending.
  const startPolling = () => {
    if (pollRef.current != null) return;
    const tick = async () => {
      try {
        const { jobs: all, counts: c } = await listJobs();
        setJobs(all);
        setCounts(c);
        const pending = Object.values(all).some(
          (j) => j.status === "queued" || j.status === "running"
        );
        if (!pending && pollRef.current != null) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        /* transient — keep polling */
      }
    };
    tick();
    pollRef.current = window.setInterval(tick, 1200);
  };
  useEffect(() => () => {
    if (pollRef.current != null) window.clearInterval(pollRef.current);
  }, []);

  // Seed the queue counts on mount so the dock reflects the runner's full state
  // (not just the current batch) even before/after a batch (review #5).
  useEffect(() => {
    listJobs()
      .then((r) => {
        setJobs(r.jobs);
        setCounts(r.counts);
      })
      .catch(() => {});
  }, []);

  const onCancel = async (id: string) => {
    try {
      await cancelJob(id);
    } catch (e) {
      setError(String(e));
    }
    startPolling(); // observe the transition to canceled
  };

  const onGenerate = async () => {
    setError(null);
    if (!prompt.trim()) {
      setError("enter a prompt");
      return;
    }
    try {
      const res = await generate({ prompt: prompt.trim(), count });
      setBatchIds(res.job_ids);
      setSelected(null);
      startPolling();
    } catch (e) {
      setError(String(e));
    }
  };

  const dot = conn === "online" ? "ok" : conn === "offline" ? "err" : "warn";
  const pending = counts.queued + counts.running;
  const selJob = selected ? jobs[selected] : null;

  return (
    <div className="app">
      <header className="titlebar">
        <span className="title">Loreweave Studio</span>
        <span className="sep">—</span>
        <span className="project">Sandbox</span>
        <span className="spacer" />
        <span className={`status dot-${dot}`}>
          <i className="dot" /> orchestrator: {conn}
          {health ? ` · v${health.app_version}` : ""}
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
            <input
              className="prompt"
              placeholder="prompt…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onGenerate();
              }}
            />
            <label className="n">
              N
              <input
                type="number"
                min={1}
                max={8}
                value={count}
                onChange={(e) => setCount(clamp(parseInt(e.target.value || "1", 10), 1, 8))}
              />
            </label>
            <button onClick={onGenerate} disabled={conn !== "online"}>
              Generate ▶
            </button>
          </div>
          {error && <div className="error">⚠ {error}</div>}

          <div className="grid">
            {batchIds.length === 0 && (
              <p className="muted center span">
                Fire a batch — results stream in here (the casting-grid embryo).
              </p>
            )}
            {batchIds.map((id) => (
              <GridCell
                key={id}
                job={jobs[id]}
                selected={selected === id}
                onClick={() => setSelected(id)}
                onCancel={() => onCancel(id)}
              />
            ))}
          </div>
        </main>

        <aside className="inspector">
          <div className="rail-head">INSPECTOR</div>
          {!selJob && <div className="muted">Select an image to see job details + lineage.</div>}
          {selJob && <Inspector job={selJob} />}
        </aside>
      </div>

      <footer className="dock">
        <span className={`status dot-${pending > 0 ? "warn" : dot}`}>
          <i className="dot" /> JOB QUEUE{" "}
          {pending > 0 ? `▶ running (${pending})` : `· idle`}
        </span>
        <span className="meter">
          done {counts.done}
          {counts.failed ? ` · failed ${counts.failed}` : ""}
        </span>
        <span className="meter">VRAM 0.0/16.0G</span>
        <span className="meter">disk —/250G</span>
      </footer>
    </div>
  );
}

function GridCell({
  job,
  selected,
  onClick,
  onCancel,
}: {
  job?: Job;
  selected: boolean;
  onClick: () => void;
  onCancel: () => void;
}) {
  const status = job?.status ?? "queued";
  const name = job?.result?.output_name;
  const prog = job?.progress ?? 0;
  const active = status === "queued" || status === "running";
  return (
    <div
      className={`cell ${selected ? "sel" : ""} st-${status}`}
      role="button"
      tabIndex={0}
      onClick={onClick}
    >
      {name && status === "done" ? (
        <img src={outputUrl(name)} alt={job?.id} />
      ) : (
        <span className="cell-status">
          {status === "queued" && "queued…"}
          {status === "running" && "generating…"}
          {status === "failed" && "✕ failed"}
          {status === "canceled" && "⊘ canceled"}
          {status === "done" && "—"}
        </span>
      )}
      {status === "running" && (
        <div className="progress">
          <div className="bar" style={{ width: `${Math.round(prog * 100)}%` }} />
        </div>
      )}
      {active && (
        <button
          className="cancel"
          title="cancel"
          onClick={(e) => {
            e.stopPropagation();
            onCancel();
          }}
        >
          ✕
        </button>
      )}
    </div>
  );
}

function Inspector({ job }: { job: Job }) {
  const r = job.result;
  return (
    <div className="insp">
      {r?.output_name && r.ok && <img className="preview" src={outputUrl(r.output_name)} alt={job.id} />}
      <dl>
        <dt>job</dt><dd>{job.id}</dd>
        <dt>status</dt><dd>{job.status}</dd>
        <dt>seed</dt><dd>{r?.seed ?? "—"}</dd>
        <dt>wall</dt><dd>{job.wall_s != null ? `${job.wall_s}s` : "—"}</dd>
        <dt>pipeline</dt><dd>{r?.duration_s != null ? `${r.duration_s}s` : "—"}</dd>
        <dt>file</dt><dd className="mono">{r?.output_name ?? "—"}</dd>
      </dl>
      {r?.error && <div className="error">⚠ {r.error}</div>}
      {(job.status === "failed" || job.status === "canceled") && job.log_tail && (
        <pre className="stderr">{job.log_tail}</pre>
      )}
    </div>
  );
}

function clamp(n: number, lo: number, hi: number): number {
  if (Number.isNaN(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}
