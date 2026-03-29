import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

function getBuildHash(): string {
  if (process.env.BUILD_HASH && process.env.BUILD_HASH !== "dev") {
    return process.env.BUILD_HASH.slice(0, 7);
  }
  try {
    return execFileSync("git", ["rev-parse", "--short", "HEAD"])
      .toString()
      .trim();
  } catch {
    return "dev";
  }
}

function getAppVersion(): string {
  try {
    const pyproject = readFileSync("../pyproject.toml", "utf-8");
    const match = pyproject.match(/^version\s*=\s*"(.+)"/m);
    return match?.[1] ?? "dev";
  } catch {
    return "dev";
  }
}

export default defineConfig({
  define: {
    __BUILD_HASH__: JSON.stringify(getBuildHash()),
    __APP_VERSION__: JSON.stringify(getAppVersion()),
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
