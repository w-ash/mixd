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

describe("WorkflowRow", () => {
  it("renders Edit, Duplicate, and Run actions in the card variant", () => {
    renderWithProviders(
      <WorkflowRow wf={wf} runningWorkflowId={null} variant="card" />,
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
          <WorkflowRow wf={wf} runningWorkflowId={null} variant="table" />
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
      <WorkflowRow wf={wf} runningWorkflowId={null} variant="card" />,
    );

    expect(screen.getByTitle("Edit workflow").closest("a")).toHaveAttribute(
      "href",
      `/workflows/${wf.id}/edit`,
    );
  });

  it("shows a failing marker only when the schedule is in a failed streak", () => {
    const { rerender } = renderWithProviders(
      <WorkflowRow wf={wf} runningWorkflowId={null} variant="card" />,
    );
    expect(screen.queryByText("Failing")).not.toBeInTheDocument();

    rerender(
      <WorkflowRow
        wf={wf}
        runningWorkflowId={null}
        variant="card"
        scheduleFailing
      />,
    );
    expect(screen.getByText("Failing")).toBeInTheDocument();
  });
});
