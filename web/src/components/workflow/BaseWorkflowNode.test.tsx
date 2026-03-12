import { render, screen } from "@testing-library/react";
import { Activity } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

// Mock React Flow handles (require ReactFlowProvider in real usage)
vi.mock("@xyflow/react", () => ({
  Handle: ({ type }: { type: string }) => (
    <div data-testid={`handle-${type}`} />
  ),
  Position: { Left: "left", Right: "right" },
}));

import { BaseWorkflowNode, type WorkflowNodeData } from "./BaseWorkflowNode";

const baseData: WorkflowNodeData = {
  taskId: "src_1",
  nodeType: "source.liked_tracks",
  config: { connector: "spotify" },
};

const defaultProps = {
  data: baseData,
  Icon: Activity,
  accentColor: "oklch(0.7 0.12 250)",
  label: "Liked Tracks",
};

describe("BaseWorkflowNode", () => {
  it("renders label and task ID", () => {
    render(<BaseWorkflowNode {...defaultProps} />);
    expect(screen.getByText("Liked Tracks")).toBeInTheDocument();
    expect(screen.getByText("src_1")).toBeInTheDocument();
  });

  it("renders config entries", () => {
    render(<BaseWorkflowNode {...defaultProps} />);
    expect(screen.getByText("connector:")).toBeInTheDocument();
    expect(screen.getByText("spotify")).toBeInTheDocument();
  });

  it("limits config entries to 3", () => {
    const data: WorkflowNodeData = {
      ...baseData,
      config: { a: "1", b: "2", c: "3", d: "4" },
    };
    render(<BaseWorkflowNode {...defaultProps} data={data} />);
    // Only first 3 shown
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.queryByText("4")).not.toBeInTheDocument();
  });

  it("uses custom config labels when provided", () => {
    render(
      <BaseWorkflowNode
        {...defaultProps}
        configLabels={{ connector: "Service" }}
      />,
    );
    expect(screen.getByText("Service:")).toBeInTheDocument();
  });

  it("shows track counts for completed status", () => {
    const data: WorkflowNodeData = {
      ...baseData,
      executionStatus: "completed",
      inputTrackCount: 50,
      outputTrackCount: 20,
    };
    render(<BaseWorkflowNode {...defaultProps} data={data} />);
    expect(screen.getByText(/50/)).toBeInTheDocument();
    expect(screen.getByText(/20 tracks/)).toBeInTheDocument();
  });

  it("shows error message for failed status", () => {
    const data: WorkflowNodeData = {
      ...baseData,
      executionStatus: "failed",
      errorMessage: "Connection timeout",
    };
    render(<BaseWorkflowNode {...defaultProps} data={data} />);
    expect(screen.getByText("Connection timeout")).toBeInTheDocument();
  });

  it("renders both handles by default", () => {
    render(<BaseWorkflowNode {...defaultProps} />);
    expect(screen.getByTestId("handle-target")).toBeInTheDocument();
    expect(screen.getByTestId("handle-source")).toBeInTheDocument();
  });

  it("hides target handle for source nodes in edit mode", () => {
    const data: WorkflowNodeData = {
      ...baseData,
      mode: "edit",
    };
    render(<BaseWorkflowNode {...defaultProps} data={data} />);
    expect(screen.queryByTestId("handle-target")).not.toBeInTheDocument();
    expect(screen.getByTestId("handle-source")).toBeInTheDocument();
  });

  it("hides source handle for destination nodes in edit mode", () => {
    const data: WorkflowNodeData = {
      ...baseData,
      nodeType: "destination.save_playlist",
      mode: "edit",
    };
    render(<BaseWorkflowNode {...defaultProps} data={data} />);
    expect(screen.getByTestId("handle-target")).toBeInTheDocument();
    expect(screen.queryByTestId("handle-source")).not.toBeInTheDocument();
  });
});
