// A drag handle for a resizable zone. Pointer capture, no library; the parent owns the size.
import { useRef, useState } from "react";

interface Props {
  /** which edge of its parent the handle sits on */
  edge: "left" | "right" | "top";
  /** current size of the zone (px) */
  size: number;
  onResize: (size: number) => void;
}

export function Resizer({ edge, size, onResize }: Props) {
  const start = useRef<{ pos: number; size: number } | null>(null);
  const [active, setActive] = useState(false);
  const horizontal = edge === "top";

  return (
    <div
      className={`${horizontal ? "resizer-h" : `resizer ${edge}`}${active ? " active" : ""}`}
      role="separator"
      aria-orientation={horizontal ? "horizontal" : "vertical"}
      onPointerDown={(e) => {
        start.current = { pos: horizontal ? e.clientY : e.clientX, size };
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        setActive(true);
      }}
      onPointerMove={(e) => {
        if (!start.current) return;
        const delta = (horizontal ? e.clientY : e.clientX) - start.current.pos;
        // growing the zone means dragging away from the canvas
        const sign = edge === "right" ? 1 : -1;
        onResize(start.current.size + sign * delta);
      }}
      onPointerUp={(e) => {
        start.current = null;
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
        setActive(false);
      }}
    />
  );
}
