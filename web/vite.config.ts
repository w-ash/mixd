import { execFileSync } from "node:child_process";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

function getBuildHash(): string {
  // Docker builds pass BUILD_HASH as an env var; local dev reads from git
  if (process.env.BUILD_HASH && process.env.BUILD_HASH !== "dev") {
    return process.env.BUILD_HASH;
  }
  try {
    return execFileSync("git", ["rev-parse", "--short", "HEAD"])
      .toString()
      .trim();
  } catch {
    return "dev";
  }
}

export default defineConfig({
  define: {
    __BUILD_HASH__: JSON.stringify(getBuildHash()),
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
