// One keyboard dispatcher for the frame (kb-loom-ui.md §3.4): Tab hides/shows both side
// panels, 1–4 pick the stage, ? shows this list, Escape closes what is open.
import { useEffect } from "react";

import { useApp, type Stage } from "../store";

const STAGE_KEYS: Record<string, Stage> = { "1": "cast", "2": "expand", "3": "curate", "4": "train" };

function inField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

export function Shortcuts() {
  const helpOpen = useApp((s) => s.helpOpen);
  const setHelpOpen = useApp((s) => s.setHelpOpen);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useApp.getState();
      if (e.key === "Escape") {
        if (s.dialog) { s.setDialog(null); e.preventDefault(); return; }
        if (s.helpOpen) { s.setHelpOpen(false); e.preventDefault(); return; }
        if (s.menuOpen) { s.setMenuOpen(false); e.preventDefault(); return; }
        if (s.selection) { s.select(null); e.preventDefault(); return; }
        return;
      }
      if (inField(e.target)) return;
      if (e.key === "Tab" && !e.ctrlKey && !e.altKey && !e.metaKey) { s.hideAllPanels(); e.preventDefault(); return; }
      if (e.key === "?" ) { s.setHelpOpen(!s.helpOpen); e.preventDefault(); return; }
      if (STAGE_KEYS[e.key] && s.workspace === "assets" && s.selectedAsset) { s.setStage(STAGE_KEYS[e.key]); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!helpOpen) return null;
  return (
    <div className="help" onClick={() => setHelpOpen(false)} role="dialog" aria-label="Keyboard shortcuts">
      <div className="card" onClick={(e) => e.stopPropagation()}>
        <h2>Keyboard</h2>
        <dl>
          <dt>Tab</dt><dd>hide or show both side panels</dd>
          <dt>1 2 3 4</dt><dd>Cast, Expand, Curate, Train</dd>
          <dt>Esc</dt><dd>close a dialog, this list or the File menu, or clear the selection</dd>
          <dt>?</dt><dd>this list</dd>
        </dl>
        <p className="faint">Grid keys (arrows, keep, reject) arrive with migration step 5.</p>
      </div>
    </div>
  );
}
