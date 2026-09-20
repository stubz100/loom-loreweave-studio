// The grouped view (author 2026-08-08): operation groups at the top level, derivation nested
// inside — a chain reads left to right (source → pass → pass), a fan-out gets a row per
// branch, a folded group or lineage is a card with a cover. Ported from v1's GroupedGrid with
// the structure kept and the tiles drawn by the one Tile component.
import { useEffect, useMemo, useState } from "react";

import type { Job } from "@loom/shared/api/orchestrator";

import { useApp } from "../store";
import { tileActions } from "./actions";
import { useTileFlags } from "./Grid";
import { setCanvas } from "./registry";
import { Tile } from "./Tile";
import { imageUrl, tilesOfJob, type CanvasModel, type Tile as TileModel } from "./tiles";

type Node = { job: Job; tiles: TileModel[]; children: Node[] };
type Group = { id: string; label: string; sub: string; at: string; roots: Node[]; jobs: Job[] };

function groupLabel(batchId: string, jobs: Job[]): { label: string; sub: string } {
  const j = jobs[0];
  const pipe = Array.from(new Set(jobs.map((x) => x.pipeline).filter(Boolean))).join("/") || "unknown";
  const n = jobs.length;
  const sub = `${pipe}, ${n} job${n === 1 ? "" : "s"}`;
  if (batchId.startsWith("prv_")) return { label: "LoRA preview", sub };
  if (batchId.startsWith("trn_")) return { label: "Training run", sub };
  if (batchId.startsWith("rdn_")) return { label: "Readiness scan", sub };
  if (batchId.startsWith("poses_")) return { label: "Pose icons", sub };
  if (!batchId) return { label: "Postprocess", sub };
  if (j?.stage === "A") return { label: "Cast", sub };
  if (j?.stage === "B") return { label: "Expansion sweep", sub };
  return { label: "Batch", sub };
}

function describeChain(n: Node): string {
  const name = (j: Job) => j.pass || j.mode || j.pipeline;
  const parts = [name(n.job)];
  let cur = n;
  while (cur.children.length === 1) { cur = cur.children[0]; parts.push(name(cur.job)); }
  if (cur.children.length > 1) parts.push(`+${cur.children.length} branches`);
  return parts.join(" → ");
}
const countChain = (n: Node): number => 1 + n.children.reduce((t, c) => t + countChain(c), 0);
const when = (iso?: string) => { try { return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""; } catch { return ""; } };

/** Groups with derivation nested: a job is a child when its parent is in view, else a root. */
export function buildGroups(jobs: Job[]): Group[] {
  const nodes = new Map<string, Node>(jobs.map((j) => [j.id, { job: j, tiles: tilesOfJob(j), children: [] }]));
  const roots: Node[] = [];
  for (const node of nodes.values()) {
    const parent = node.job.chained_from ? nodes.get(node.job.chained_from) : undefined;
    if (parent && parent !== node) parent.children.push(node); else roots.push(node);
  }
  const groups = new Map<string, Group>();
  for (const root of roots) {
    const gid = root.job.batch_id || `solo:${root.job.id}`;
    let g = groups.get(gid);
    if (!g) { g = { id: gid, label: "", sub: "", at: root.job.created_at, roots: [], jobs: [] }; groups.set(gid, g); }
    g.roots.push(root);
    if ((root.job.created_at || "") < (g.at || "")) g.at = root.job.created_at;
  }
  const collect = (n: Node, into: Job[]) => { into.push(n.job); n.children.forEach((c) => collect(c, into)); };
  for (const g of groups.values()) {
    g.roots.forEach((r) => collect(r, g.jobs));
    const meta = groupLabel(g.id.startsWith("solo:") ? "" : g.id, g.jobs);
    g.label = meta.label; g.sub = meta.sub;
    g.roots.sort((a, b) => (a.job.created_at || "").localeCompare(b.job.created_at || ""));
  }
  return Array.from(groups.values()).sort((a, b) => (b.at || "").localeCompare(a.at || ""));
}

export function Grouped({ model, jobs, assetId, versionId }: { model: CanvasModel; jobs: Job[]; assetId: string | null; versionId: string | null }) {
  const zoom = useApp((s) => s.zoom);
  const fit = useApp((s) => s.fit);
  const offline = useApp((s) => s.offline);
  const flagsOf = useTileFlags(model);
  const groups = useMemo(() => buildGroups(jobs), [jobs]);
  const [shut, setShut] = useState<Set<string>>(new Set());
  const [confirmGroup, setConfirmGroup] = useState<string | null>(null);
  const visible = useMemo(() => new Set(model.tiles.map((t) => t.key)), [model.tiles]);
  const orphans = useMemo(() => model.tiles.filter((t) => t.ref), [model.tiles]);
  const toggle = (id: string) => setShut((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  // The keyboard order = the render order; a folded group contributes nothing.
  const order: TileModel[] = [];
  const draw = (t: TileModel) => { if (!visible.has(t.key)) return null; order.push(t); return <Tile key={t.key} tile={t} flags={flagsOf(t)} assetId={assetId} versionId={versionId} />; };
  const cover = (ts: TileModel[]): string | null => { for (const t of ts) { const u = imageUrl(t, assetId, versionId); if (u) return u; } return null; };
  const coverOfNode = (n: Node): string | null => cover(n.tiles) ?? n.children.map(coverOfNode).find(Boolean) ?? null;

  const renderChain = (n: Node) => (
    <div key={n.job.id} className="chain">
      <div className="chain-step">
        <div className="chain-cap">{n.job.pass || n.job.mode || n.job.pipeline}<span className="faint"> {n.job.pipeline}{n.job.status !== "done" ? `, ${n.job.status}` : ""}</span></div>
        {n.job.deleted ? <div className="chain-gone" title="this image was deleted; the step is kept so what was built from it stays attached">deleted</div>
                       : <div className="chain-tiles">{n.tiles.map(draw)}</div>}
      </div>
      {n.children.length > 0 && (
        <div className="chain-kids">
          {n.children.map((c) => <div key={c.job.id} className="chain-kid"><span className="chain-arrow" aria-hidden>→</span>{renderChain(c)}</div>)}
        </div>
      )}
    </div>
  );

  const card = (id: string, label: string, meta: string, coverUrl: string | null, count: string | null) => (
    <button className="tree-card" onClick={() => toggle(id)} title={`expand: ${meta}`}>
      <span className="tree-card-img">{coverUrl ? <img src={coverUrl} alt="" /> : <span className="tree-card-ph">no image</span>}{count && <span className="tree-card-count">{count}</span>}</span>
      <span className="tree-card-foot"><span className="tree-card-name">{label}</span><span className="faint">{meta}</span></span>
    </button>
  );

  const body = (
    <div className={`tree${fit === "fill" ? " fill" : ""}`} style={{ ["--tile" as string]: `${zoom}px` }}>
      {orphans.length > 0 && (
        <div className={`tree-group${shut.has("__refs__") ? " shut" : ""}`}>
          {shut.has("__refs__") ? card("__refs__", "Curated refs", `${orphans.length} without a generation on this grid`, cover(orphans), `×${orphans.length}`) : (
            <>
              <div className="tree-head"><button className="tree-toggle" onClick={() => toggle("__refs__")}>▾ Curated refs</button><span className="faint">{orphans.length} without a generation on this grid</span></div>
              <div className="tree-body">{orphans.map(draw)}</div>
            </>
          )}
        </div>
      )}
      {groups.map((g) => {
        const imgs = g.jobs.reduce((n, j) => n + (j.result?.output_names?.length ?? 0), 0);
        const plain = g.roots.filter((r) => r.children.length === 0 && !r.job.deleted);
        const chains = g.roots.filter((r) => r.children.length > 0);
        const meta = `${g.sub}${imgs ? `, ${imgs} image${imgs === 1 ? "" : "s"}` : ""}, ${when(g.at)}`;
        if (shut.has(g.id)) return <div key={g.id} className="tree-group shut">{card(g.id, g.label, meta, g.roots.map(coverOfNode).find(Boolean) ?? null, g.roots.length > 1 ? `×${g.roots.length}` : null)}</div>;
        return (
          <div key={g.id} className="tree-group">
            <div className="tree-head">
              <button className="tree-toggle" onClick={() => toggle(g.id)}>▾ {g.label}</button>
              <span className="faint">{meta}</span>
              <span className="spacer" />
              <button className={confirmGroup === g.id ? "danger" : ""} disabled={offline} onBlur={() => setConfirmGroup(null)}
                      onClick={() => { if (confirmGroup === g.id) { setConfirmGroup(null); void tileActions.removeGroup(g.jobs); } else setConfirmGroup(g.id); }}
                      title="delete every job in this operation, including what was postprocessed from it">
                {confirmGroup === g.id ? `Delete ${g.jobs.length} job${g.jobs.length === 1 ? "" : "s"}?` : "Delete group"}
              </button>
            </div>
            <div className="tree-body">
              {plain.flatMap((r) => r.tiles).map(draw)}
              {chains.map((r) => {
                const cid = `${g.id}::${r.job.id}`;
                const passes = countChain(r);
                if (shut.has(cid)) return <div key={r.job.id} className="chain-card shut">{card(cid, describeChain(r), `${passes} pass${passes === 1 ? "" : "es"}`, coverOfNode(r), `⑂${passes}`)}</div>;
                return (
                  <div key={r.job.id} className="chain-card">
                    <div className="tree-head"><button className="tree-toggle" onClick={() => toggle(cid)}>▾ {describeChain(r)}</button><span className="faint">{passes} pass{passes === 1 ? "" : "es"}</span></div>
                    <div className="chain-card-body">{renderChain(r)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );

  const orderKey = order.map((t) => t.key).join("|");
  useEffect(() => { setCanvas({ order: [...order], cols: 1 }); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [orderKey]);
  useEffect(() => () => setCanvas(null), []);

  return (
    <>
      <div className="tree-bar">
        <button onClick={() => setShut(new Set([...groups.map((g) => g.id), "__refs__"]))}>Collapse all</button>
        <button onClick={() => setShut(new Set())}>Expand all</button>
        <span className="faint">{groups.length} operation{groups.length === 1 ? "" : "s"}</span>
      </div>
      {body}
    </>
  );
}
