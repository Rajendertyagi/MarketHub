import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// React source lives in `frontend/src`; the generated artifact is emitted to
// `frontend/dist` (isolated, gitignored). The existing Python server's `/ui`
// mount is then pointed at `frontend/dist` for production serving (see
// docs/FRONTEND_MIGRATION.md). The build NEVER writes into any other tracked
// source tree, so no tracked file is overwritten and no manual git restore
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
        // Vite 8 bundles with rolldown, which requires the function form of
        // `manualChunks`; the object form Rollup accepted is no longer valid.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          const path = id.split("\\").join("/");
          if (path.includes("/echarts/") || path.includes("/zrender/")) {
            return "echarts";
          }
          if (
            path.includes("/react/") ||
            path.includes("/react-dom/") ||
            path.includes("/react-router/") ||
            path.includes("/react-router-dom/")
          ) {
            return "react";
          }
          if (path.includes("/@tanstack/")) return "query";
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
