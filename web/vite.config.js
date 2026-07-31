import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/api": "http://localhost:8790",
      "/runs": "http://localhost:8790",
    },
  },
});
