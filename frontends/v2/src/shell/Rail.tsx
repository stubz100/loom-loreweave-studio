// The 48 px icon rail: opens the Panel on a tab, or collapses it when its tab is already up.
import { useApp, type PanelTab } from "../store";

const TABS: { id: PanelTab; icon: string; label: string }[] = [
  { id: "library", icon: "▤", label: "Library" },
  { id: "compose", icon: "✦", label: "Compose" },
  { id: "train", icon: "⚙", label: "Train" },
];

export function Rail() {
  const panelOpen = useApp((s) => s.panelOpen);
  const panelTab = useApp((s) => s.panelTab);
  const togglePanel = useApp((s) => s.togglePanel);
  const workspace = useApp((s) => s.workspace);
  const tabs = workspace === "assets" ? TABS : TABS.filter((t) => t.id === "library");
  return (
    <nav className="rail" aria-label="Panels">
      {tabs.map((t) => (
        <button key={t.id} className={panelOpen && panelTab === t.id ? "active" : ""}
                onClick={() => togglePanel(t.id)} title={`${t.label} (click again to hide)`} aria-label={t.label}>
          {t.icon}
        </button>
      ))}
    </nav>
  );
}
