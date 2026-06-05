// Frontend logging (user request) — `.env`-configurable verbosity, mirrors the backend.
//
// Level from VITE_LOOM_LOG_LEVEL (Vite reads it from the root .env / .env.local):
//   "brief"   (default) -> info  : lifecycle (project open/create, generate, delete) + warn/error
//   "verbose"           -> debug : the above + poll/fetch detail
//   "silent" | standard names (error/warn/info/debug) also accepted.
//
// Routes to the browser console (visible in the Tauri webview devtools); a small ring
// buffer keeps the most recent lines for a future in-app log pane.

export type LogLevel = "silent" | "error" | "warn" | "info" | "debug";

const ORDER: Record<LogLevel, number> = { silent: 0, error: 1, warn: 2, info: 3, debug: 4 };

function configuredLevel(): LogLevel {
  // @ts-expect-error - import.meta.env is provided by Vite
  const raw = (import.meta.env?.VITE_LOOM_LOG_LEVEL as string | undefined)?.toLowerCase();
  if (raw === "verbose") return "debug";
  if (raw === "brief") return "info";
  if (raw && raw in ORDER) return raw as LogLevel;
  return "info";
}

export const logLevel: LogLevel = configuredLevel();
const threshold = ORDER[logLevel];

const ring: string[] = [];
function remember(level: LogLevel, args: unknown[]) {
  ring.push(`${new Date().toISOString().slice(11, 19)} ${level} ${args.map(String).join(" ")}`);
  if (ring.length > 300) ring.shift();
}

function emit(level: Exclude<LogLevel, "silent">, args: unknown[]) {
  if (ORDER[level] > threshold) return;
  remember(level, args);
  const fn = level === "error" ? console.error
    : level === "warn" ? console.warn
    : level === "debug" ? console.debug
    : console.info;
  fn("[loom]", ...args);
}

export const log = {
  error: (...a: unknown[]) => emit("error", a),
  warn: (...a: unknown[]) => emit("warn", a),
  info: (...a: unknown[]) => emit("info", a),
  debug: (...a: unknown[]) => emit("debug", a),
  level: logLevel,
  recent: () => ring.slice(),
};
