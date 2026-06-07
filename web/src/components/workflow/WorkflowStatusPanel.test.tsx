/**
 * Verifies the state-aware status panel renders the right zone and never
 * duplicates the run-history table:
 *   - ACTIVE   — "Running now" + a link to the live run
 *   - IDLE     — cadence line, definition-drift banner only when stale, and NO
 *                last-run status/tracks/time (those live in the table)
 *   - NEVER    — an inviting prompt
 */

import { describe, expect, it } from "vitest";

import type {
  LastRunSchema,
  WorkflowRunSummarySchema,
} from "#/api/generated/model";
import { renderWithProviders, screen } from "#/test/test-utils";
import { WorkflowStatusPanel } from "./WorkflowStatusPanel";

const baseProps = {
  workflowId: "wf-1",
  tasks: [],
  nodeStatuses: new Map(),
  isExecuting: false,
  runAccepted: false,
  subProgress: null,
  runId: null,
  nextRunLabel: null,
};

const lastRun: LastRunSchema = {
  id: "run-9",
  status: "completed",
  definition_version: 3,
  completed_at: "2026-06-01T10:00:00Z",
  output_track_count: 120,
};

function activeRun(): WorkflowRunSummarySchema {
  return {
    id: "run-live",
    workflow_id: "wf-1",
    status: "running",
    operation_id: "op-live",
    started_at: "2026-06-05T10:00:00Z",
  };
}

describe("WorkflowStatusPanel", () => {
  it("shows the active zone with a live-run link when a run is in flight", () => {
    renderWithProviders(
      <WorkflowStatusPanel
        {...baseProps}
        lastRun={lastRun}
        currentDefinitionVersion={3}
        activeRun={activeRun()}
      />,
    );

    expect(screen.getByText(/Running now/i)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /View live run/i });
    expect(link).toHaveAttribute("href", "/workflows/wf-1/runs/run-live");
  });

  it("shows the never-run prompt when there are no runs", () => {
    renderWithProviders(
      <WorkflowStatusPanel
        {...baseProps}
        lastRun={null}
        currentDefinitionVersion={1}
        activeRun={null}
      />,
    );

    expect(screen.getByText(/Never run yet/i)).toBeInTheDocument();
  });

  it("idle: shows the cadence line and no definition-drift banner when current", () => {
    renderWithProviders(
      <WorkflowStatusPanel
        {...baseProps}
        lastRun={lastRun}
        currentDefinitionVersion={3}
        activeRun={null}
        nextRunLabel="Next run Jun 6, 6:30 AM"
      />,
    );

    expect(screen.getByText("Next run Jun 6, 6:30 AM")).toBeInTheDocument();
    expect(
      screen.queryByText(/Definition changed since last run/i),
    ).not.toBeInTheDocument();
  });

  it("idle: shows the definition-drift banner when the last run is stale", () => {
    renderWithProviders(
      <WorkflowStatusPanel
        {...baseProps}
        lastRun={lastRun}
        currentDefinitionVersion={5}
        activeRun={null}
      />,
    );

    expect(
      screen.getByText(/Definition changed since last run/i),
    ).toBeInTheDocument();
  });

  it("idle: does not duplicate the last run's status or track count", () => {
    renderWithProviders(
      <WorkflowStatusPanel
        {...baseProps}
        lastRun={lastRun}
        currentDefinitionVersion={3}
        activeRun={null}
        nextRunLabel="Not scheduled — run manually"
      />,
    );

    // The table below owns this; the panel must not re-show it.
    expect(screen.queryByText(/120/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Completed/i)).not.toBeInTheDocument();
  });
});
