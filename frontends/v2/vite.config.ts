import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// v2 — the new frontend (kb-loom-ui.md). Its own fixed dev port (1421) so it can run beside
// v1 (1420) against the same orchestrator; the shell wraps it via tauri.v2.conf.json.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  envDir: "../..",   // the central .env / .env.local at the app-repo root
  resolve: {
    alias: { "@loom/shared": fileURLToPath(new URL("../shared", import.meta.url)) },
  },
  server: {
    port: 1421,
    strictPort: true,
  },
});
