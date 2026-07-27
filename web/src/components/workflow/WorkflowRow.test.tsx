import { describe, expect, it } from "vitest";

import type { WorkflowSummarySchema } from "#/api/generated/model";
import { Table, TableBody } from "#/components/ui/table";
import { renderWithProviders, screen } from "#/test/test-utils";

import { WorkflowRow } from "./WorkflowRow";

const wf: WorkflowSummarySchema = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Flow A",
  description: "A pipeline",
  definition_version: 1,
  task_count: 3,
  node_types: ["source.liked_tracks"],
  updated_at: "2026-05-30T00:00:00Z",
};

const NONE: ReadonlySet<string> = new Set<string>();

describe("WorkflowRow", () => {
  it("renders Edit, Duplicate, and Run actions in the card variant", () => {
    renderWithProviders(
      <WorkflowRow
        wf={wf}
        runningWorkflowIds={NONE}
        localRunWorkflowId={null}
        variant="card"
      />,
    );

    expect(screen.getByText("Flow A")).toBeInTheDocument();
    expect(screen.getByTitle("Edit workflow")).toBeInTheDocument();
    expect(screen.getByTitle("Duplicate workflow")).toBeInTheDocument();
    expect(screen.getByTitle("Run workflow")).toBeInTheDocument();
  });

  it("renders the same actions in the table variant (one shared renderer)", () => {
    renderWithProviders(
      <Table>
        <TableBody>
          <WorkflowRow
            wf={wf}
            runningWorkflowIds={NONE}
            localRunWorkflowId={null}
            variant="table"
          />
        </TableBody>
      </Table>,
    );

    expect(screen.getByText("Flow A")).toBeInTheDocument();
    expect(screen.getByTitle("Edit workflow")).toBeInTheDocument();
    expect(screen.getByTitle("Duplicate workflow")).toBeInTheDocument();
    expect(screen.getByTitle("Run workflow")).toBeInTheDocument();
  });

  it("links the Edit action to the workflow editor", () => {
    renderWithProviders(
      <WorkflowRow
        wf={wf}
        runningWorkflowIds={NONE}
        localRunWorkflowId={null}
        variant="card"
      />,
    );

    expect(screen.getByTitle("Edit workflow").closest("a")).toHaveAttribute(
      "href",
      `/workflows/${wf.id}/edit`,
    );
  });

  it("shows a failing marker only when the schedule is in a failed streak", () => {
    const { rerender } = renderWithProviders(
      <WorkflowRow
        wf={wf}
        runningWorkflowIds={NONE}
        localRunWorkflowId={null}
        variant="card"
      />,
    );
    expect(screen.queryByText("Failing")).not.toBeInTheDocument();

    rerender(
      <WorkflowRow
        wf={wf}
        runningWorkflowIds={NONE}
        localRunWorkflowId={null}
        variant="card"
        scheduleFailing
      />,
    );
    expect(screen.getByText("Failing")).toBeInTheDocument();
  });

  describe("successful run count", () => {
    it("renders the count in the table variant", () => {
      renderWithProviders(
        <Table>
          <TableBody>
            <WorkflowRow
              wf={{ ...wf, successful_run_count: 24 }}
              runningWorkflowIds={NONE}
              localRunWorkflowId={null}
              variant="table"
            />
          </TableBody>
        </Table>,
      );

      expect(screen.getByTitle("24 successful runs")).toHaveTextContent("24");
    });

    it("renders an em-dash rather than a zero for a never-succeeded workflow", () => {
      renderWithProviders(
        <Table>
          <TableBody>
            <WorkflowRow
              wf={{ ...wf, successful_run_count: 0 }}
              runningWorkflowIds={NONE}
              localRunWorkflowId={null}
              variant="table"
            />
          </TableBody>
        </Table>,
      );

      expect(screen.getByTitle("0 successful runs")).toHaveTextContent("—");
    });

    it("singularises a single run in the card variant", () => {
      renderWithProviders(
        <WorkflowRow
          wf={{ ...wf, successful_run_count: 1 }}
          runningWorkflowIds={NONE}
          localRunWorkflowId={null}
          variant="card"
        />,
      );

      expect(screen.getByText("1 run")).toBeInTheDocument();
    });
  });

  describe("running state", () => {
    const RUNNING: ReadonlySet<string> = new Set([wf.id]);

    it("shows Running for a run this tab never started", () => {
      // The scheduler/cross-tab case: no local execution state at all, but the
      // server says this workflow is in flight.
      renderWithProviders(
        <Table>
          <TableBody>
            <WorkflowRow
              wf={{ ...wf, last_run: undefined }}
              runningWorkflowIds={RUNNING}
              localRunWorkflowId={null}
              variant="table"
            />
          </TableBody>
        </Table>,
      );

      expect(screen.getByText("Running")).toBeInTheDocument();
      expect(screen.getByTitle("Workflow is running")).toBeDisabled();
    });

    it("prefers the live state over a stale completed last_run", () => {
      renderWithProviders(
        <Table>
          <TableBody>
            <WorkflowRow
              wf={{
                ...wf,
                last_run: {
                  id: "22222222-2222-2222-2222-222222222222",
                  run_number: 4,
                  status: "completed",
                },
              }}
              runningWorkflowIds={RUNNING}
              localRunWorkflowId={null}
              variant="table"
            />
          </TableBody>
        </Table>,
      );

      expect(screen.getByText("Running")).toBeInTheDocument();
      expect(screen.queryByText("Completed")).not.toBeInTheDocument();
    });

    it("leaves other rows runnable when a different workflow runs server-side", () => {
      // Only the local SSE slot blocks other rows — a server-side run elsewhere
      // does not, because uq_workflow_runs_active is per-workflow.
      renderWithProviders(
        <Table>
          <TableBody>
            <WorkflowRow
              wf={wf}
              runningWorkflowIds={new Set(["some-other-workflow"])}
              localRunWorkflowId={null}
              variant="table"
            />
          </TableBody>
        </Table>,
      );

      expect(screen.getByTitle("Run workflow")).toBeEnabled();
    });

    it("disables other rows while this tab streams a different workflow", () => {
      renderWithProviders(
        <Table>
          <TableBody>
            <WorkflowRow
              wf={wf}
              runningWorkflowIds={NONE}
              localRunWorkflowId="some-other-workflow"
              variant="table"
            />
          </TableBody>
        </Table>,
      );

      expect(screen.getByTitle("Run workflow")).toBeDisabled();
    });
  });
});
