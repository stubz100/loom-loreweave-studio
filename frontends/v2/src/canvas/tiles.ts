// What the canvas draws (kb-loom-ui.md §3.4): the selected scope's jobs flattened into tiles —
// one per output for a multi pool or a batch, interim tiles for a running cast, a placeholder
// for the in-flight remainder — plus, in Curate, the version's durable refs that no job on
// the grid produced. Mirrors v1's `cells` / `stageCells` derivation so the two views agree.
import { outputUrl, refUrl, type AssetDetail, type CoverageCell, type Job, type RefItem } from "@loom/shared/api/orchestrator";

import { STAGE_LETTER, type Filters, type Selection, type Stage } from "../store";

export interface Tile {
  key: string;
  job?: Job;
  output?: string;
  interim?: boolean;      // a running cast's already-landed candidate
  ref?: RefItem;          // a durable curated ref with no job behind it
}

export const isVideo = (name?: string | null) => !!name && /\.(mp4|webm|mov)$/i.test(name);

export function tileKey(s: Selection): string {
  if (s.refId) return `ref:${s.refId}`;
  return s.output ? `${s.jobId}:${s.output}` : s.jobId ?? "";
}
export function selectionOf(t: Tile): Selection {
  if (t.ref) return { refId: t.ref.id, output: t.ref.file };
  return { jobId: t.job?.id, output: t.output };
}

/** The image a tile shows, or null while it has none (queued, failed, a video, a tombstone). */
export function imageUrl(t: Tile, assetId: string | null, versionId: string | null): string | null {
  if (t.ref) return assetId ? refUrl(assetId, t.ref.file, versionId ?? undefined) : null;
  const name = t.output ?? t.job?.result?.output_name;
  if (!name || isVideo(name)) return null;
  if (t.job?.status === "done" || t.interim) return outputUrl(name);
  return null;
}

export function coverageOf(t: Tile): CoverageCell | undefined {
  return t.ref?.coverage_cell
    ?? (t.output ? t.job?.result?.output_meta?.[t.output]?.coverage_cell : undefined)
    ?? t.job?.coverage_cell ?? undefined;
}

/** A job that makes an image (Stage D also hosts the trainer and the readiness scan). */
const makesAnImage = (j: Job) => j.pipeline !== "zimage_trainer" && j.mode !== "score";

/** The jobs in scope: a character's active version at the stage's letter (Curate reviews the
 *  expansion set, B), or the Sandbox = everything the project itself requested. */
export function scopedJobs(jobs: Record<string, Job>, projectId: string | null, versionId: string | null, stage: Stage): Job[] {
  const letter = STAGE_LETTER[stage];
  const gridLetter = letter === "C" ? "B" : letter;
  return Object.values(jobs)
    .filter((j) => (versionId
      ? j.requester_id === versionId && (j.stage ?? "A") === gridLetter && (gridLetter !== "D" || makesAnImage(j))
      : !!projectId && j.requester_id === projectId))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** One job → its tiles (the multi pool expands to N; a running cast streams its interim
 *  candidates plus one placeholder; a tombstone draws nothing). */
export function tilesOfJob(job: Job): Tile[] {
  if (job.deleted) return [];
  const names = job.result?.output_names;
  if (names && names.length > 1) return names.map((o) => ({ key: `${job.id}:${o}`, job, output: o }));
  const partial = job.status === "running" ? job.partial_outputs ?? [] : [];
  if (partial.length > 0) {
    return [...partial.map((o) => ({ key: `${job.id}:${o}`, job, output: o, interim: true })), { key: job.id, job }];
  }
  const output = job.result?.output_name;
  return [{ key: output ? `${job.id}:${output}` : job.id, job, output }];
}

export interface CanvasModel {
  tiles: Tile[];               // what the view draws, filtered
  all: number;                 // before the Curate filters
  kept: Map<string, string>;   // source output → ref id
  rejected: Set<string>;
  starred: Set<string>;
  locked: boolean;
}

export function deriveCanvas(
  jobs: Record<string, Job>, projectId: string | null, detail: AssetDetail | null, stage: Stage, filters: Filters,
): CanvasModel {
  const version = detail?.versions.find((v) => v.id === detail.profile.active_version) ?? null;
  const scoped = scopedJobs(jobs, projectId, version?.id ?? null, stage);
  const cells = scoped.flatMap(tilesOfJob);
  const kept = new Map((version?.ref_set ?? []).filter((r) => r.source_output).map((r) => [r.source_output!, r.id] as const));
  const rejected = new Set(version?.rejected ?? []);
  const starred = new Set((version?.casting ?? []).filter((c) => c.starred).map((c) => c.source_output ?? ""));
  if (stage !== "curate" || !version) {
    return { tiles: cells, all: cells.length, kept, rejected, starred, locked: !!version?.finalized };
  }
  const onGrid = new Set(cells.map((c) => c.output).filter(Boolean));
  const durable: Tile[] = version.ref_set
    .filter((r) => !r.source_output || !onGrid.has(r.source_output))
    .map((r) => ({ key: `ref:${r.id}`, ref: r }));
  const both = [...durable, ...cells];
  const tiles = both.filter((t) => {
    if (!filters.showRejected && t.output && rejected.has(t.output)) return false;
    const cov = coverageOf(t);
    if (filters.shot && cov?.shot_size !== filters.shot) return false;
    if (filters.angle && cov?.angle !== filters.angle) return false;
    if (filters.expression && cov?.expression !== filters.expression) return false;
    return true;
  });
  return { tiles, all: both.length, kept, rejected, starred, locked: !!version.finalized };
}
