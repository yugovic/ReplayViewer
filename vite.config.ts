import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 5173 is often taken by other local Vite apps; pin a dedicated port and
    // fail loudly instead of silently falling back to a different one.
    port: 5199,
    strictPort: true,
    open: true,
  },
  preview: {
    port: 5199,
    strictPort: true,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three"],
          postprocessing: ["postprocessing"],
          vendor: ["uplot", "react", "react-dom"],
        },
      },
    },
  },
});
