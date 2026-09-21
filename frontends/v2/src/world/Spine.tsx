// The story spine (kb-loom-ui.md §3.8): the premise, and the characters with their identity
// clause snippets. A character becomes a stub profile in Assets (R55); a linked one can be
// re-synced (the snippet pushed into the profile, manual and overwriting).
import { useEffect, useState } from "react";

import {
  createSpineStub, removeSpineCharacter, resyncSpineStub, setPremise, upsertSpineCharacter, type SpineCharacter,
} from "@loom/shared/api/orchestrator";

import { reasonOf } from "../lib/project";
import { useApp } from "../store";

export function Spine() {
  const bible = useApp((s) => s.bible);
  const refreshBible = useApp((s) => s.refreshBible);
  const offline = useApp((s) => s.offline);
  const notify = useApp((s) => s.notify);
  const [premise, setPremiseDraft] = useState("");
  const [newName, setNewName] = useState("");
  const [newSnippet, setNewSnippet] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { setPremiseDraft(bible?.spine?.premise ?? ""); }, [bible?.id, bible?.spine?.premise]);
  const chars = bible?.spine?.characters ?? [];
  const savedPremise = bible?.spine?.premise ?? "";

  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key);
    try { await fn(); await refreshBible(); if (done) notify("ok", done); }
    catch (e) { notify("err", reasonOf(e)); }
    finally { setBusy(null); }
  };

  return (
    <div className="editor">
      <div className="editor-head">
        <h2>Story spine</h2>
        <span className="faint">premise, arc, factions; then the characters that become profiles</span>
        <span className="spacer" />
        <button onClick={() => setPremiseDraft(savedPremise)} disabled={premise === savedPremise}>Revert</button>
        <button className="primary" onClick={() => void run("premise", () => setPremise(premise), "Premise saved.")} disabled={premise === savedPremise || busy !== null || offline}>Save premise</button>
      </div>
      <textarea rows={8} value={premise} onChange={(e) => setPremiseDraft(e.target.value)} placeholder="premise, arc, factions" spellCheck={false} />

      <div className="section-title">Characters</div>
      {chars.length === 0 && <p className="faint">No spine characters yet. Add one below; a stub profile can be made from it.</p>}
      {chars.map((c) => <SpineRow key={c.id} ch={c} busy={busy} offline={offline} run={run} />)}

      <div className="section-title">Add a character</div>
      <div className="jt-row">
        <input type="text" value={newName} placeholder="name" onChange={(e) => setNewName(e.target.value)} />
      </div>
      <textarea rows={2} value={newSnippet} placeholder="identity clause: the fixed prompt-template text" onChange={(e) => setNewSnippet(e.target.value)} spellCheck={false} />
      <div className="jt-row">
        <span className="spacer" />
        <button className="primary" disabled={!newName.trim() || busy !== null || offline}
                onClick={() => void run("add", async () => { await upsertSpineCharacter({ name: newName.trim(), snippet: newSnippet.trim() }); setNewName(""); setNewSnippet(""); }, "Character added to the spine.")}>Add character</button>
      </div>
    </div>
  );
}

function SpineRow({ ch, busy, offline, run }: { ch: SpineCharacter; busy: string | null; offline: boolean; run: (key: string, fn: () => Promise<unknown>, done?: string) => Promise<void> }) {
  const [name, setName] = useState(ch.name);
  const [snippet, setSnippet] = useState(ch.snippet);
  const [confirm, setConfirm] = useState(false);
  const selectAsset = useApp((s) => s.selectAsset);
  const setWorkspace = useApp((s) => s.setWorkspace);
  useEffect(() => { setName(ch.name); setSnippet(ch.snippet); }, [ch.id, ch.name, ch.snippet]);
  const dirty = name !== ch.name || snippet !== ch.snippet;
  const linked = !!ch.linked_asset_id;
  return (
    <div className="spine-row">
      <div className="jt-row">
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="name" />
        {linked && <button onClick={() => { selectAsset(ch.linked_asset_id!); setWorkspace("assets"); }} title={ch.linked_asset_id!}>Open profile</button>}
        {linked
          ? <button onClick={() => void run(`resync:${ch.id}`, () => resyncSpineStub(ch.id), "Snippet pushed into the linked profile.")} disabled={busy !== null || offline} title="push this snippet into the linked profile's active version (overwrites)">Re-sync</button>
          : <button onClick={() => void run(`stub:${ch.id}`, () => createSpineStub(ch.id), "Stub profile created in Assets.")} disabled={busy !== null || offline} title="make a stub character profile seeded with this snippet">Make profile</button>}
        <button onClick={() => void run(`save:${ch.id}`, () => upsertSpineCharacter({ character_id: ch.id, name: name.trim() || ch.name, snippet }), "Saved.")} disabled={!dirty || busy !== null || offline}>Save</button>
        <button className={confirm ? "danger" : ""} onBlur={() => setConfirm(false)} disabled={busy !== null || offline}
                onClick={() => { if (confirm) { setConfirm(false); void run(`rm:${ch.id}`, () => removeSpineCharacter(ch.id), "Removed from the spine; a linked profile stays."); } else setConfirm(true); }}>
          {confirm ? "Remove?" : "Remove"}
        </button>
      </div>
      <textarea rows={2} value={snippet} onChange={(e) => setSnippet(e.target.value)} placeholder="identity clause snippet" spellCheck={false} />
    </div>
  );
}
