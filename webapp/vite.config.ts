import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // GitHub Pages serves this repo under /post-branches-finder/, not /.
  // The deploy workflow sets VITE_BASE_PATH; local dev/build defaults to "/".
  base: process.env.VITE_BASE_PATH || "/",
  server: {
    port: 5173,
    proxy: {
      // dev: forward /api/* to the FastAPI server
      "/api": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
