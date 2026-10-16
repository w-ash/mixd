// Stryker plugin module that fixes per-test filtering under Vitest 5.
//
// @stryker-mutator/vitest-runner 10.0.0 names a test by joining its suite path
// with spaces ("pluralize uses the singular form") and filters each mutant run
// with `testNamePattern` built from that name. Vitest 5 matches the pattern
// against names joined with " > " ("pluralize > uses the singular form"), so
// the filter matches nothing, every mutant run executes 0 tests, and every
// mutant reports "Survived".
//
// Loading this module (from `plugins` in stryker.config.mjs) wraps the runner's
// Vitest instance so each space in the pattern also matches " > ". Remove it
// when the runner builds Vitest 5 names itself.

const runnerModule = new URL(
  "./vitest-test-runner.js",
  import.meta.resolve("@stryker-mutator/vitest-runner"),
);
const { VitestTestRunner } = await import(runnerModule.href);

const originalInit = VitestTestRunner.prototype.init;

VitestTestRunner.prototype.init = async function init() {
  await originalInit.call(this);
  const start = this.ctx.start.bind(this.ctx);
  this.ctx.start = (filters) => {
    for (const project of this.ctx.projects) {
      const pattern = project.config.testNamePattern;
      if (pattern) {
        project.config.testNamePattern = new RegExp(
          pattern.source.replaceAll(" ", "(?: | > )"),
          pattern.flags,
        );
      }
    }
    return start(filters);
  };
};

// Stryker warns about a plugin module that exports neither key.
export const strykerPlugins = [];
