import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// React source lives in `frontend/src`; the generated artifact is emitted to
// `frontend/dist` (isolated, gitignored). The existing Python server's `/ui`
// mount is then pointed at `frontend/dist` for production serving (see
// docs/FRONTEND_MIGRATION.md). The build NEVER writes into tracked legacy
// `web/ui` source, so no tracked file is overwritten and no manual git restore
// is required.
export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ["echarts"],
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.MARKETHUB_API || "http://localhost:7070",
        changeOrigin: true,
      },
    },
  },
});
