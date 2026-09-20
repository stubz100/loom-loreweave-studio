// Transient notices, bottom-right, above the dock. Errors stay until dismissed; the rest fade.
import { useEffect } from "react";

import { useApp } from "../store";

const AUTO_MS = 6000;

export function Toasts() {
  const notices = useApp((s) => s.notices);
  const dismiss = useApp((s) => s.dismiss);

  useEffect(() => {
    const timers = notices
      .filter((n) => n.kind !== "err")
      .map((n) => setTimeout(() => dismiss(n.id), Math.max(0, AUTO_MS - (Date.now() - n.at))));
    return () => timers.forEach(clearTimeout);
  }, [notices, dismiss]);

  if (!notices.length) return null;
  return (
    <div className="toasts" aria-live="polite">
      {notices.slice(-5).map((n) => (
        <div key={n.id} className={`toast ${n.kind}`} role={n.kind === "err" ? "alert" : "status"}>
          <span className="text">{n.text}</span>
          <button onClick={() => dismiss(n.id)} aria-label="Dismiss">✕</button>
        </div>
      ))}
    </div>
  );
}
