import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Production target is the existing Python server's `/ui` mount, which serves
// the `web/ui` directory. The build emits into `web/ui` (index.html + assets/)
// so the app is served at `/ui` with no backend runtime changes.
export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../web/ui",
    emptyOutDir: false,
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
