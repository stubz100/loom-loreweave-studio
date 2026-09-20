// Open / create a project from anywhere (File menu, Start screen, dialogs) with one outcome
// path: the store learns the project at once, the author gets one notice, errors surface
// where the action was taken (the caller shows them) and never as a silent no-op.
import { createProject, openProject, type ProjectInfo } from "@loom/shared/api/orchestrator";

import { useApp } from "../store";

export async function openProjectAt(path: string): Promise<ProjectInfo> {
  const p = await openProject(path);
  const s = useApp.getState();
  s.setProject(p.open ? p : null);
  s.setDialog(null);
  s.notify("ok", `Opened ${p.name ?? path}.`);
  return p;
}

export async function createProjectAt(dest: string, name: string, capGb: number): Promise<ProjectInfo> {
  const p = await createProject(dest, name, capGb);
  const s = useApp.getState();
  s.setProject(p.open ? p : null);
  s.setDialog(null);
  s.notify("ok", `Created ${p.name ?? name}.`);
  return p;
}

/** The server's reason, without the HTTP framing the client puts in front of it. */
export function reasonOf(e: unknown): string {
  const msg = String(e);
  const m = /:\s*(\{.*\})$/.exec(msg);
  if (m) {
    try {
      const body = JSON.parse(m[1]) as { detail?: unknown };
      if (typeof body.detail === "string") return body.detail;
    } catch { /* fall through */ }
  }
  return msg;
}
