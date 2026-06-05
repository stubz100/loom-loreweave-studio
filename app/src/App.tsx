import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createProject,
  estimateFootprint,
  generate,
  getDisk,
  getHealth,
  getProject,
  listJobs,
  openProject,
  outputUrl,
  unpauseQueue,
  type DiskStatus,
  type Health,
  type Job,
  type ProjectInfo,
} from "./lib/orchestrator";

// M2 shell — three-pane layout + Job Queue dock (kb-loom-p0.md §10). The stage is
// a generate bar + a simple selectable result grid (the smoke target / casting-grid
// embryo, §12). Batches are fired at the orchestrator, which serializes them through
// one GPU worker; the UI polls /jobs and streams each result into the grid.

type Conn = "connecting" | "online" | "offline";

export default function App() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [health, setHealth] = useState<Health | null>(null);
  const [project, setProject] = useState<ProjectInfo | null>(null);

  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(3);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState({ queued: 0, running: 0, done: 0, failed: 0, canceled: 0 });
  const [paused, setPaused] = useState(false);
  const [vramBudget, setVramBudget] = useState(16);
  const [disk, setDisk] = useState<DiskStatus | null>(null);
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
        try {
          const [p, d] = await Promise.all([getProject(), getDisk()]);
          if (alive) {
            setProject(p);
            setDisk(d);
          }
        } catch {
          /* project/disk fetch transient */
        }
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
        const r = await listJobs();
        setJobs(r.jobs);
        setCounts(r.counts);
        setPaused(r.paused);
        setVramBudget(r.vram_budget_gb);
        if (r.disk) setDisk(r.disk);
        // After a relaunch the grid is empty but the queue may hold persisted pending
        // jobs — seed the grid from them so they're reviewable + cancelable before
        // unpause (R88 "Review/Unpause"; review #3).
        setBatchIds((prev) => {
          if (prev.length > 0) return prev;
          return Object.values(r.jobs)
            .filter((j) => j.status === "queued" || j.status === "running")
            .sort((a, b) => a.created_at.localeCompare(b.created_at))
            .map((j) => j.id);
        });
        const pending = Object.values(r.jobs).some(
          (j) => j.status === "queued" || j.status === "running"
        );
        // Keep polling while work is in flight OR the queue is paused with pending
        // work (so the dock + unpause stay live after a resume-paused load, R88).
        if (!pending && !r.paused && pollRef.current != null) {
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
    // Seed + (if there's pending/paused work after a resume-paused load) keep polling.
    startPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onUnpause = async () => {
    try {
      await unpauseQueue();
      setPaused(false);
    } catch (e) {
      setError(String(e));
    }
    startPolling();
  };

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
    if (!project?.open) {
      setError("no project open — create or open one first");
      return;
    }
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

  // Minimal `loom init` flow (prompt-based; a native folder picker + format/cap wizard
  // is a later UI pass). Shows the footprint estimate so the size cap is an informed
  // choice (R164), then creates + opens the project (which resume-pauses its queue).
  const onNewProject = async () => {
    setError(null);
    const dest = window.prompt("New project folder (must be empty):");
    if (!dest) return;
    const name = window.prompt("Project name:", dest.split(/[\\/]/).pop() || "story");
    if (!name) return;
    try {
      const est = await estimateFootprint(1800, 1280, 720, 24);
      const cap = window.prompt(
        `Size cap (GB). A 30-min 720p master is ~${est.projected_master_gb} GB; ` +
          `suggested ${est.suggested_cap_gb} GB (min 50).`,
        String(est.suggested_cap_gb),
      );
      if (cap === null) return;
      const p = await createProject(dest, name, Math.max(50, parseInt(cap, 10) || 250));
      setProject(p);
      setBatchIds([]);
      setSelected(null);
      startPolling();
    } catch (e) {
      setError(String(e));
    }
  };

  const onOpenProject = async () => {
    setError(null);
    const path = window.prompt("Open project folder (contains project.json):");
    if (!path) return;
    try {
      const p = await openProject(path);
      setProject(p);
      setBatchIds([]);
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
        <span className="project">
          {project?.open ? project.name : <span className="muted">no project</span>}
        </span>
        <button className="proj-btn" onClick={onNewProject} disabled={conn !== "online"}>
          + New
        </button>
        <button className="proj-btn" onClick={onOpenProject} disabled={conn !== "online"}>
          Open
        </button>
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
            <button
              onClick={onGenerate}
              disabled={conn !== "online" || !project?.open || disk?.blocked === true}
            >
              Generate ▶
            </button>
          </div>
          {conn === "online" && !project?.open && (
            <div className="banner">
              No project open. <button className="link" onClick={onNewProject}>Create</button> or{" "}
              <button className="link" onClick={onOpenProject}>open</button> a project to generate.
            </div>
          )}
          {disk?.blocked && (
            <div className="banner banner-err">
              ⛔ Disk hard-stop — {disk.reason}. Free space or raise the project size cap; new jobs
              are blocked (running jobs finish).
            </div>
          )}
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
        <span className={`status dot-${paused ? "warn" : pending > 0 ? "warn" : dot}`}>
          <i className="dot" /> JOB QUEUE{" "}
          {paused
            ? `⏸ paused (${counts.queued} queued)`
            : pending > 0
            ? `▶ running (${pending})`
            : `· idle`}
        </span>
        <span className="meter">
          done {counts.done}
          {counts.failed ? ` · failed ${counts.failed}` : ""}
          {counts.canceled ? ` · canceled ${counts.canceled}` : ""}
        </span>
        <span className="meter">VRAM 0.0/{vramBudget.toFixed(1)}G</span>
        <span className={`meter disk-${disk?.state ?? "ok"}`} title={disk?.reason ?? "disk OK"}>
          {disk?.project
            ? `proj ${disk.project.used_gb.toFixed(1)}/${disk.project.cap_gb}G`
            : `proj —/${project?.size_cap_gb ?? 250}G`}
          {disk?.disk ? ` · disk ${disk.disk.free_pct.toFixed(0)}% free` : ""}
          {disk?.state === "warn" ? " ⚠" : disk?.state === "hard" ? " ⛔" : ""}
        </span>
        {paused && (
          <button className="unpause" onClick={onUnpause}>
            unpause ▶
          </button>
        )}
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
