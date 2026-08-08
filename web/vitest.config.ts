import { defineConfig, mergeConfig } from "vitest/config";

// Explicit extension required by Vite 8.2's `configLoader: 'native'`, which becomes
// the default in a future major — without it the native loader cannot resolve this
// import and vitest warns on every run.
import viteConfig from "./vite.config.ts";

// Node 25+ ships built-in WebStorage globals that collide with jsdom's localStorage
// in test workers; opt out where the flag exists. Node 24 LTS has no WebStorage and
// rejects the flag with "bad option", so omit it there.
const nodeMajor = Number(process.versions.node.split(".")[0]);
const execArgv = nodeMajor >= 25 ? ["--no-webstorage"] : [];

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      exclude: ["src/api/generated/**"],
      execArgv,
    },
  }),
);
