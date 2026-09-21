// The world prose (kb-loom-ui.md §3.8): a long-form markdown summary, authoring context that
// is never injected into a prompt. The Panel shows its headings as an outline.
import { useEffect, useState } from "react";

import { setWorld } from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

export function WorldText() {
  const bible = useApp((s) => s.bible);
  const refreshBible = useApp((s) => s.refreshBible);
  const offline = useApp((s) => s.offline);
  const notify = useApp((s) => s.notify);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setDraft(bible?.world ?? ""); }, [bible?.id, bible?.world]);
  const saved = bible?.world ?? "";
  const dirty = draft !== saved;
  const save = async () => {
    setBusy(true);
    try { await setWorld(draft); await refreshBible(); notify("ok", "World saved."); }
    catch (e) { notify("err", reasonOf(e)); } finally { setBusy(false); }
  };
  return (
    <div className="editor">
      <div className="editor-head">
        <h2>World</h2>
        <span className="faint">long-form summary in markdown; context for you and the Muse, never a prompt</span>
        <span className="spacer" />
        <button onClick={() => setDraft(saved)} disabled={!dirty}>Revert</button>
        <button className="primary" onClick={() => void save()} disabled={!dirty || busy || offline}>Save</button>
      </div>
      <textarea className="prose" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="# The world…" spellCheck={false} />
    </div>
  );
}
