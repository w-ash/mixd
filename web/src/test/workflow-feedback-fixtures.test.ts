/**
 * Sanity guard for the workflow feedback E2E fixtures
 * (`web/e2e/fixtures/workflow-feedback.ts`).
 *
 * `tsconfig` only includes `src/`, so the E2E fixtures get no compile-time
 * coverage on their own. Importing them here pulls them into the type program
 * — a generated-model change that breaks them becomes a `tsc` error — and
 * checks the two hand-rolled pieces the spec depends on: the SSE wire format
 * and the deferred-route control.
 */
import { describe, expect, it } from "vitest";

import {
  activeRun,
  Deferred,
  RUN_ID,
  runToCompletion,
  sseBody,
  workflowDetail,
  workflowSummary,
} from "../../e2e/fixtures/workflow-feedback";

describe("workflow-feedback fixtures", () => {
  it("builds a detail payload whose task_count matches its definition", () => {
    const detail = workflowDetail({ task_count: 4 });
    expect(detail.definition.tasks).toHaveLength(4);
    expect(detail.successful_run_count).toBe(0);
  });

  it("summary and detail agree on the run-count field", () => {
    expect(
      workflowSummary({ successful_run_count: 7 }).successful_run_count,
    ).toBe(7);
    expect(
      workflowDetail({ successful_run_count: 7 }).successful_run_count,
    ).toBe(7);
  });

  it("serialises SSE events in the format the parser expects", () => {
    const body = sseBody([{ event: "complete", data: { ok: true } }]);
    // id / event / data, then the blank line that terminates the frame.
    expect(body).toBe('id: 1\nevent: complete\ndata: {"ok":true}\n\n');
  });

  it("a full run stream opens with run_accepted and ends with complete", () => {
    const events = runToCompletion(["a", "b"]);
    expect(events.at(0)?.event).toBe("run_accepted");
    expect(events.at(-1)?.event).toBe("complete");
    expect(events.filter((e) => e.event === "node_status")).toHaveLength(2);
  });

  it("a Deferred stays pending until released", async () => {
    const deferred = new Deferred();
    let settled = false;
    void deferred.promise.then(() => {
      settled = true;
    });

    await Promise.resolve();
    expect(settled).toBe(false);

    deferred.release();
    await deferred.promise;
    expect(settled).toBe(true);
  });

  it("the active run points at the run the spec navigates to", () => {
    expect(activeRun().id).toBe(RUN_ID);
    expect(activeRun().status).toBe("running");
  });
});
