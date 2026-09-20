// Catalog-driven tunables (GET /models): one control per ParamSpec, filtered by mode and model,
// grouped as v1 grouped them; an unset control means "the model's default".
import type { ParamSpec } from "@loom/shared/api/orchestrator";

export function ParamControls({ specs, mode, values, onChange, exclude = [], sizeDefaults, modelName }: {
  specs: ParamSpec[];
  mode: string;
  values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
  exclude?: string[];
  sizeDefaults?: { width?: number; height?: number };
  modelName?: string;
}) {
  const modelOk = (s: ParamSpec) => !s.models || (modelName != null && s.models.includes(modelName));
  const usable = (s: ParamSpec) => s.type !== "image" && !exclude.includes(s.name) && (!s.modes || s.modes.includes(mode)) && modelOk(s);
  const visible = specs.filter((s) => !s.advanced && usable(s));
  const advanced = specs.filter((s) => s.advanced && usable(s));
  if (!visible.length && !advanced.length) return <p className="faint">No tunables for this pipeline and mode.</p>;
  const isFamily = (s: ParamSpec, fam: string) => s.name === fam || s.name.startsWith(`${fam}_`);
  const groups: [string, ParamSpec[]][] = [
    ["Model and generation", visible.filter((s) => !s.post)],
    ["Clean pass", visible.filter((s) => !!s.post && isFamily(s, "clean"))],
    ["Polish pass", visible.filter((s) => !!s.post && isFamily(s, "polish"))],
    ["Other postprocess", visible.filter((s) => !!s.post && !isFamily(s, "clean") && !isFamily(s, "polish"))],
  ];
  return (
    <>
      {groups.filter(([, g]) => g.length).map(([label, g]) => (
        <div key={label} className="p-group">
          <span className="jt-label">{label}</span>
          {g.map((s) => <Control key={s.name} spec={s} value={values[s.name]} onChange={onChange} sizeDefaults={sizeDefaults} />)}
        </div>
      ))}
      {advanced.length > 0 && (
        <details className="p-group">
          <summary className="jt-label">Advanced ({advanced.length})</summary>
          {advanced.map((s) => <Control key={s.name} spec={s} value={values[s.name]} onChange={onChange} sizeDefaults={sizeDefaults} />)}
        </details>
      )}
    </>
  );
}

function Control({ spec: s, value: v, onChange, sizeDefaults }: {
  spec: ParamSpec; value: unknown; onChange: (name: string, value: unknown) => void;
  sizeDefaults?: { width?: number; height?: number };
}) {
  const title = s.note ?? "";
  const sizeOverride = s.name === "width" || s.name === "height" ? sizeDefaults?.[s.name] : undefined;
  const def = sizeOverride ?? s.default;
  const label = s.name.replace(/_/g, " ");
  if (s.type === "flag") {
    return (
      <label className="p-flag" title={title}>
        <input type="checkbox" checked={v === true} onChange={(e) => onChange(s.name, e.target.checked ? true : undefined)} />
        {label}
      </label>
    );
  }
  if (s.type === "enum") {
    return (
      <label className="p-field" title={title}>{label}
        <select value={typeof v === "string" ? v : ""} onChange={(e) => onChange(s.name, e.target.value || undefined)}>
          <option value="">default{def != null ? ` (${String(def)})` : ""}</option>
          {(s.choices ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </label>
    );
  }
  if (s.type === "int" || s.type === "float") {
    return (
      <label className="p-field" title={title}>{label}
        <input type="number" value={typeof v === "number" ? v : ""} placeholder={def != null ? String(def) : ""}
               min={s.min} max={s.max} step={s.step ?? (s.type === "int" ? 1 : 0.05)}
               onChange={(e) => { const t = e.target.value; onChange(s.name, t === "" ? undefined : (s.type === "int" ? parseInt(t, 10) : parseFloat(t))); }} />
      </label>
    );
  }
  return (
    <label className="p-field" title={title}>{label}
      <input type="text" value={typeof v === "string" ? v : ""} placeholder={def != null ? String(def) : ""}
             onChange={(e) => onChange(s.name, e.target.value || undefined)} />
    </label>
  );
}
