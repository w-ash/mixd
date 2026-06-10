import { describe, expect, it } from "vitest";

import type { WorkflowRunSummarySchema } from "#/api/generated/model";
import { renderWithProviders, screen } from "#/test/test-utils";

import { RunHistoryTable } from "./RunHistoryTable";

function makeRun(
  over: Partial<WorkflowRunSummarySchema> = {},
): WorkflowRunSummarySchema {
  return {
    id: "11111111-2222-3333-4444-555555555555",
    workflow_id: "wf-1",
    run_number: 5,
    status: "completed",
    duration_ms: 1200,
    output_track_count: 20,
    started_at: "2026-06-07T10:00:00Z",
    created_at: "2026-06-07T10:00:00Z",
    ...over,
  };
}

describe("RunHistoryTable", () => {
  it("shows the per-workflow run number, never the UUID", () => {
    renderWithProviders(
      <RunHistoryTable runs={[makeRun()]} workflowId="wf-1" />,
    );

    // The friendly number is shown (in both card and table variants)...
    expect(screen.getAllByText("#5").length).toBeGreaterThan(0);
    // ...and the UUID never appears as visible text.
    expect(
      screen.queryByText(/11111111-2222-3333-4444-555555555555/),
    ).not.toBeInTheDocument();
  });

  it("links to the run by its UUID (stable address), not its number", () => {
    renderWithProviders(
      <RunHistoryTable runs={[makeRun()]} workflowId="wf-1" />,
    );

    const links = screen.getAllByRole("link");
    expect(
      links.some((a) =>
        a
          .getAttribute("href")
          ?.endsWith("/runs/11111111-2222-3333-4444-555555555555"),
      ),
    ).toBe(true);
  });
});
