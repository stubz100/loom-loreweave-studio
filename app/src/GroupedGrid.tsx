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
  jobs, tilesOf, renderTile, tileImageUrl, onDeleteGroup, emptyHint,
  orphanTiles = [], orphanLabel = "", orphanSub = "",
}: {
  jobs: Job[];
  tilesOf: (job: Job) => TileRef[];
  renderTile: (t: TileRef) => React.ReactNode;
  /** Cover thumbnail for a tile, or null when it has no image yet (queued/failed). */
  tileImageUrl: (t: TileRef) => string | null;
  onDeleteGroup: (jobs: Job[], label: string) => void;
  emptyHint: string;
  /** Tiles with NO generating job — Stage C's durable curated refs (a copied version keeps
   *  its ref files but has no jobs behind them). A job-derived tree cannot hold them, so they
   *  get their own group rather than silently vanishing when the view is switched. */
  orphanTiles?: TileRef[];
  orphanLabel?: string;
  orphanSub?: string;
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

  // Orphans alone are still content — a copied version can hold curated refs and no jobs at
  // all, and hiding them behind "nothing here" would lose the whole Stage-C set.
  if (groups.length === 0 && orphanTiles.length === 0) {
    return <p className="muted center span">{emptyHint}</p>;
  }

  /** A group's cover: the first tile that actually has an image. */
  const coverOf = (g: Group): string | null => {
    const walk = (n: Node): string | null => {
      for (const t of n.tiles) {
        const u = tileImageUrl(t);
        if (u) return u;
      }
      for (const c of n.children) {
        const u = walk(c);
        if (u) return u;
      }
      return null;
    };
    for (const r of g.roots) {
      const u = walk(r);
      if (u) return u;
    }
    return null;
  };

  // A node with NO children contributes its tiles to the group's single flat grid — that is
  // what stops a 24-cell sweep rendering as 24 stacked one-tile rows (author 2026-08-08 #3).
  // Only a node that actually HAS derived children needs its own block, to carry the nesting.
  //
  // A chain reads HORIZONTALLY (author 2026-08-08 #2a: "stacking image tiles in a tree
  // structure will result in a lot of space wasted") — source → pass → pass, left to right,
  // one row per line of descent. When a node has SEVERAL children (the branching stacks now
  // allow: two strengths off one base) each branch gets its own row, so the fan-out is the
  // one thing that still costs vertical space, which is exactly when it carries meaning.
  const renderChain = (n: Node): React.ReactNode => (
    <div key={n.job.id} className="chain">
      <div className="chain-step">
        <div className="chain-cap">
          <span className="tree-pass">{n.job.pass || n.job.mode || n.job.pipeline}</span>
          <span className="muted sm"> · {n.job.pipeline}</span>
          {n.job.status !== "done" && <span className="muted sm"> · {n.job.status}</span>}
        </div>
        <div className="chain-tiles">{n.tiles.map(renderTile)}</div>
      </div>
      {n.children.length > 0 && (
        <div className="chain-kids">
          {n.children.map((c) => (
            <div key={c.job.id} className="chain-kid">
              <span className="chain-arrow" aria-hidden>→</span>
              {renderChain(c)}
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="tree-wrap">
      <div className="tree-bar">
        <button className="ghost" onClick={() => setCollapsed(new Set(groups.map((g) => g.id)))}>
          collapse all
        </button>
        <button className="ghost" onClick={() => setCollapsed(new Set())}>expand all</button>
        <span className="muted sm">{groups.length} operation{groups.length === 1 ? "" : "s"}</span>
      </div>
      <div className="tree">
        {orphanTiles.length > 0 && (() => {
          const shut = collapsed.has("__orphans__");
          const cover = orphanTiles.map(tileImageUrl).find(Boolean) ?? null;
          const meta = `${orphanSub} · ${orphanTiles.length} image${orphanTiles.length === 1 ? "" : "s"}`;
          return (
            <div className={`tree-group ${shut ? "shut" : "open"}`}>
              {shut ? (
                <button className="tree-card" onClick={() => toggle("__orphans__")}
                        title={`expand — ${meta}`}>
                  <span className="tree-card-img">
                    {cover ? <img src={cover} alt={orphanLabel} />
                           : <span className="tree-card-ph">no image</span>}
                    <span className="tree-card-count">×{orphanTiles.length}</span>
                  </span>
                  <span className="tree-card-foot">
                    <span className="tree-card-name">{orphanLabel}</span>
                    <span className="muted sm">{meta}</span>
                  </span>
                </button>
              ) : (
                <>
                  <div className="tree-head">
                    <button className="tree-toggle" onClick={() => toggle("__orphans__")}
                            title="collapse">▾ {orphanLabel}</button>
                    <span className="muted sm">{meta}</span>
                  </div>
                  <div className="tree-body">
                    <div className="tree-tiles">{orphanTiles.map(renderTile)}</div>
                  </div>
                </>
              )}
            </div>
          );
        })()}
        {groups.map((g) => {
          const shut = collapsed.has(g.id);
          const imgs = g.jobs.reduce((n, j) => n + (j.result?.output_names?.length ?? 0), 0);
          const plain = g.roots.filter((r) => r.children.length === 0);
          const chains = g.roots.filter((r) => r.children.length > 0);
          const cover = coverOf(g);
          const meta = `${g.sub}${imgs ? ` · ${imgs} image${imgs === 1 ? "" : "s"}` : ""} · ${when(g.at)}`;
          return (
            <div key={g.id} className={`tree-group ${shut ? "shut" : "open"}`}>
              {/* Collapsed: a TILE with a cover image and its facts underneath — a bare bar
                  told the author nothing about what was inside (2026-08-08 #2). */}
              {shut ? (
                <button className="tree-card" onClick={() => toggle(g.id)} title={`expand — ${meta}`}>
                  <span className="tree-card-img">
                    {cover ? <img src={cover} alt={g.label} />
                           : <span className="tree-card-ph">no image</span>}
                    {g.roots.length > 1 && <span className="tree-card-count">×{g.roots.length}</span>}
                  </span>
                  <span className="tree-card-foot">
                    <span className="tree-card-name">{g.label}</span>
                    <span className="muted sm">{meta}</span>
                  </span>
                </button>
              ) : (
                <>
                  <div className="tree-head">
                    <button className="tree-toggle" onClick={() => toggle(g.id)} title="collapse">
                      ▾ {g.label}
                    </button>
                    <span className="muted sm">{meta}</span>
                    <button className="ghost tree-del"
                            onClick={() => onDeleteGroup(g.jobs, `${g.label} (${g.sub})`)}
                            title="delete every job in this operation — including anything postprocessed from it — with all their artifacts">
                      🗑 group
                    </button>
                  </div>
                  <div className="tree-body">
                    {/* every childless root in ONE grid, so it fills the width */}
                    {plain.length > 0 && (
                      <div className="tree-tiles">
                        {plain.flatMap((r) => r.tiles).map(renderTile)}
                      </div>
                    )}
                    {chains.map((r) => renderChain(r))}
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
