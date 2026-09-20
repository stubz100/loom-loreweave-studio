// Everything Tauri-only goes through this door, and every caller has a browser fallback —
// v2 must keep working from `npm run dev` in a plain browser (no shell, no native dialogs).

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Native folder picker (the dialog plugin). Resolves to the chosen path, or null when the
 *  picker was cancelled — or when there is no shell, in which case the caller shows its own
 *  typed-path form instead. */
export async function pickFolder(title: string): Promise<string | null> {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, multiple: false, title });
  return typeof picked === "string" ? picked : null;
}
