import { execFileSync } from "node:child_process";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const gitHash = execFileSync("git", ["rev-parse", "--short", "HEAD"])
  .toString()
  .trim();

export default defineConfig({
  define: {
    __BUILD_HASH__: JSON.stringify(gitHash),
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    tsconfigPaths: true,
  },
  server: {
    forwardConsole: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "elkjs",
              test: /elkjs/,
              priority: 20,
            },
            {
              name: "xyflow",
              test: /@xyflow/,
              priority: 15,
            },
            {
              name: "vendor",
              test: /node_modules/,
              priority: 10,
              minSize: 50_000,
            },
          ],
        },
      },
    },
  },
});
