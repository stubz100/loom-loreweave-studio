import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed dev-server port (1420) so the Rust shell can load it.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  // Read the central .env / .env.local from the app-repo root (one level up from
  // app/), so VITE_LOOM_ORCH_URL / VITE_LOOM_ORCH_TOKEN come from the shared config.
  envDir: "..",
  server: {
    port: 1420,
    strictPort: true,
  },
});
