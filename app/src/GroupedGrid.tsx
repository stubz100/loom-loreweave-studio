// Grouped image browser — the collapsible operation/derivation tree (author 2026-08-08,
// observation 2: "multi-candidate generations, batch generations, post-proc and expansions
// are increasing the image libraries significantly").
//
// The flat grid stays exactly as it was, behind a toggle. This view answers two questions the
// flat one cannot: *which operation made these?* and *what was this postproc image made FROM?*
//
// Shape (the author's pick): **operation group at the top level, derivation nested inside**.
//   ▾ Expansion sweep · flux2 · 24 cells
//      ▾ cell tile
//           └ clean          ← chained_from
//              └ upscale     ← chained_from of the clean
//
// Nesting is by JOB, not by output, because a derivation is not always 1→1: the manual
// postproc surface is (one image → one pass), but an auto-chained pass is 1→N — one job over
// every output of its parent. Attaching that to a single tile would be a lie, so children hang
// off the parent JOB and read correctly in both cases.
//
// Dedicated module on purpose (M2.8 monolith policy: new feature families stay OUT of
// App.tsx). Tiles are rendered by a callback so this file owns structure + group chrome only —
// selection, curation, star/keep/cull, delete and the lightbox all keep the flat view's exact
// behaviour, and there is no circular import back into App.

import { useMemo, useState } from "react";

import { Job, RefItem } from "./lib/orchestrator";

/** One renderable tile — the same shape the flat grid's `Cell` uses, so `renderTile` is
 *  literally the same function in both views (`refItem` = a durable curated ref with no job
 *  behind it; those never appear in the tree, which is job-derived). */
export type TileRef = {
  key: string; job?: Job; output?: string; interim?: boolean; refItem?: RefItem;
};

type Node = { job: Job; tiles: TileRef[]; children: Node[] };
type Group = { id: string; label: string; sub: string; at: string; roots: Node[]; jobs: Job[] };

/** Human label for a batch id + its jobs. The prefixes are the submit-site conventions. */
function groupLabel(batchId: string, jobs: Job[]): { label: string; sub: string } {
  const j = jobs[0];
  const pipes = Array.from(new Set(jobs.map((x) => x.pipeline).filter(Boolean)));
  const pipe = pipes.join("/") || "—";
  const n = jobs.length;
  const cells = `${n} job${n === 1 ? "" : "s"}`;
  if (batchId.startsWith("prv_")) return { label: "🖼 LoRA preview", sub: `${pipe} · ${cells}` };
  if (batchId.startsWith("trn_")) return { label: "⚙ Training run", sub: `${pipe} · ${cells}` };
  if (batchId.startsWith("rdn_")) return { label: "🔬 Readiness scan", sub: `${pipe} · ${cells}` };
  if (batchId.startsWith("poses_")) return { label: "🕴 Pose icons", sub: `${pipe} · ${cells}` };
  if (!batchId) return { label: "🧩 Postprocess", sub: `${pipe} · ${cells}` };
  const stage = j?.stage;
  if (stage === "A") return { label: "🎭 Cast", sub: `${pipe} · ${cells}` };
  if (stage === "B") return { label: "▦ Expansion sweep", sub: `${pipe} · ${cells}` };
  return { label: "▣ Batch", sub: `${pipe} · ${cells}` };
}

function when(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

/**
 * Build operation groups with derivation nested inside.
 *
 * `tilesOf` maps a job to the tiles the flat grid would draw for it (a multi-candidate pool
 * expands to N), so both views always agree on what exists.
 */
export function buildGroups(jobs: Job[], tilesOf: (job: Job) => TileRef[]): Group[] {
  const nodes = new Map<string, Node>(
    jobs.map((j) => [j.id, { job: j, tiles: tilesOf(j), children: [] }]),
  );

  // A job is a CHILD when its parent is also in view; otherwise it stands as a root, so a
  // pass whose parent was deleted (or filtered out by the stage scope) is never orphaned.
  const roots: Node[] = [];
  for (const node of nodes.values()) {
    const parentId = node.job.chained_from;
    const parent = parentId ? nodes.get(parentId) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }

  const groups = new Map<string, Group>();
  for (const root of roots) {
    // Solo postproc jobs carry no batch id — key them individually so each chain is its own
    // collapsible entry rather than all of them collapsing into one "no batch" bucket.
    const gid = root.job.batch_id || `solo:${root.job.id}`;
    let g = groups.get(gid);
    if (!g) {
      g = { id: gid, label: "", sub: "", at: root.job.created_at, roots: [], jobs: [] };
      groups.set(gid, g);
    }
    g.roots.push(root);
    if ((root.job.created_at || "") < (g.at || "")) g.at = root.job.created_at;
  }

  // Every job in a group (roots + descendants) — what a group delete acts on.
  const collect = (n: Node, into: Job[]) => {
    into.push(n.job);
    n.children.forEach((c) => collect(c, into));
  };
  for (const g of groups.values()) {
    g.roots.forEach((r) => collect(r, g.jobs));
    const meta = groupLabel(g.id.startsWith("solo:") ? "" : g.id, g.jobs);
    g.label = meta.label;
    g.sub = meta.sub;
    g.roots.sort((a, b) => (a.job.created_at || "").localeCompare(b.job.created_at || ""));
  }
  return Array.from(groups.values())
    .sort((a, b) => (b.at || "").localeCompare(a.at || ""));   // newest operation first
}

export default function GroupedGrid({
  jobs, tilesOf, renderTile, onDeleteGroup, emptyHint,
}: {
  jobs: Job[];
  tilesOf: (job: Job) => TileRef[];
  renderTile: (t: TileRef) => React.ReactNode;
  onDeleteGroup: (jobs: Job[], label: string) => void;
  emptyHint: string;
}) {
  const groups = useMemo(() => buildGroups(jobs, tilesOf), [jobs, tilesOf]);
  // Collapsed by default would hide everything on open; expanded-by-default with an explicit
  // collapse set keeps the first render useful AND makes "collapse all" cheap.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setCollapsed((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  if (groups.length === 0) return <p className="muted center span">{emptyHint}</p>;

  const renderNode = (n: Node, depth: number): React.ReactNode => (
    <div key={n.job.id} className="tree-node" style={{ marginLeft: depth ? 18 : 0 }}>
      {depth > 0 && (
        <div className="tree-branch">
          <span className="tree-elbow">└</span>
          <span className="tree-pass">{n.job.pass || n.job.mode || n.job.pipeline}</span>
          <span className="muted sm"> · {n.job.pipeline} · {n.job.id.slice(0, 10)}</span>
          {n.job.status !== "done" && <span className="muted sm"> · {n.job.status}</span>}
        </div>
      )}
      <div className="tree-tiles">{n.tiles.map(renderTile)}</div>
      {n.children.map((c) => renderNode(c, depth + 1))}
    </div>
  );

  return (
    <div className="tree">
      <div className="tree-bar">
        <button className="ghost" onClick={() => setCollapsed(new Set(groups.map((g) => g.id)))}>
          collapse all
        </button>
        <button className="ghost" onClick={() => setCollapsed(new Set())}>expand all</button>
        <span className="muted sm">{groups.length} operation{groups.length === 1 ? "" : "s"}</span>
      </div>
      {groups.map((g) => {
        const shut = collapsed.has(g.id);
        const imgs = g.jobs.reduce((n, j) => n + (j.result?.output_names?.length ?? 0), 0);
        return (
          <div key={g.id} className="tree-group">
            <div className="tree-head">
              <button className="tree-toggle" onClick={() => toggle(g.id)}
                      title={shut ? "expand" : "collapse"}>
                {shut ? "▸" : "▾"} {g.label}
              </button>
              <span className="muted sm">
                {g.sub}{imgs ? ` · ${imgs} image${imgs === 1 ? "" : "s"}` : ""} · {when(g.at)}
              </span>
              <button className="ghost tree-del"
                      onClick={() => onDeleteGroup(g.jobs, `${g.label} (${g.sub})`)}
                      title="delete every job in this operation — including anything postprocessed from it — with all their artifacts">
                🗑 group
              </button>
            </div>
            {!shut && <div className="tree-body">{g.roots.map((r) => renderNode(r, 0))}</div>}
          </div>
        );
      })}
    </div>
  );
}
