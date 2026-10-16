// Mutation testing for the web's pure logic. Run by /ship Step 6 on the release
// diff (`pnpm test:mutate --mutate "<changed files>"`), never in CI.
// Surviving mutants are killed with a test or noted as equivalent; there is no
// score gate, so `thresholds.break` stays null.

export default {
  testRunner: "vitest",
  // Stryker's default "@stryker-mutator/*" plugin glob searches beside its own
  // install, which pnpm's isolated layout keeps apart from the vitest runner.
  plugins: [
    import.meta.resolve("@stryker-mutator/vitest-runner"),
    "./stryker.vitest5-names.mjs",
  ],
  // Stryker rewrites tsconfig `extends`/`references` through the TypeScript JS
  // API, which TypeScript 7 (the native port) no longer ships. Our tsconfig has
  // neither, so pointing at an absent file skips a rewrite that would be a no-op.
  tsconfigFile: "tsconfig.stryker-skip.json",
  mutate: ["src/lib/**/*.ts", "src/hooks/**/*.ts", "!src/**/*.test.ts"],
  reporters: ["clear-text", "progress", "html", "json"],
  htmlReporter: { fileName: "reports/mutation/mutation.html" },
  jsonReporter: { fileName: "reports/mutation/mutation.json" },
  // The baseline is machine-local: its content is unstable across runs
  // (stryker-js#6004), so reports/ is gitignored.
  incremental: true,
  incrementalFile: "reports/stryker-incremental.json",
  thresholds: { high: 80, low: 60, break: null },
};
