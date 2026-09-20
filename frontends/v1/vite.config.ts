import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// v1 — the frozen reference frontend (kb-loom-ui.md, 2026-09-20). Tauri expects a fixed
// dev-server port (1420) so the Rust shell (frontends/shell) can load it.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  // Read the central .env / .env.local from the app-repo root (two levels up from
  // frontends/v1/), so VITE_LOOM_ORCH_URL / VITE_LOOM_ORCH_TOKEN come from the shared config.
  envDir: "../..",
  resolve: {
    // The typed API client + logger are shared by every frontend (frontends/shared).
    alias: { "@loom/shared": fileURLToPath(new URL("../shared", import.meta.url)) },
  },
  server: {
    port: 1420,
    strictPort: true,
  },
});
