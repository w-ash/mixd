import { describe, expect, it, vi } from "vitest";

// Mock smart-edge package (CommonJS/ESM incompatibility in jsdom)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

// Mock React Flow hooks
vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({
    fitView: vi.fn(),
  }),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// Mock layout to avoid async ELK
vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue([]),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  getNodeCategoryName: vi.fn().mockReturnValue("source"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

// Mock the execution hook so we can assert Run wiring without a live run.
const { mockExecute } = vi.hoisted(() => ({ mockExecute: vi.fn() }));
vi.mock("#/hooks/useWorkflowExecution", () => ({
  useWorkflowExecution: () => ({
    isExecuting: false,
    execute: mockExecute,
    operationId: null,
    runId: null,
    nodeStatuses: new Map(),
    runAccepted: false,
    subProgress: null,
    error: null,
  }),
}));

import { fireEvent } from "@testing-library/react";
import { useEditorStore } from "#/stores/editor-store";
import { renderWithProviders, screen } from "#/test/test-utils";

import { EditorToolbar } from "./EditorToolbar";

describe("EditorToolbar", () => {
  it("renders core toolbar buttons", () => {
    renderWithProviders(<EditorToolbar />);

    expect(screen.getByLabelText("Back")).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow name")).toBeInTheDocument();
    expect(screen.getByText("Save")).toBeInTheDocument();
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.getByLabelText("Undo")).toBeInTheDocument();
    expect(screen.getByLabelText("Redo")).toBeInTheDocument();
    expect(screen.getByLabelText("Auto layout")).toBeInTheDocument();
    expect(screen.getByLabelText("Zoom to fit")).toBeInTheDocument();
    expect(screen.getByLabelText("Delete selected")).toBeInTheDocument();
  });

  it("shows workflow name input with default value", () => {
    renderWithProviders(<EditorToolbar />);
    const input = screen.getByLabelText("Workflow name") as HTMLInputElement;
    expect(input.value).toBe("Untitled Workflow");
  });

  it("disables undo/redo when history is empty", () => {
    renderWithProviders(<EditorToolbar />);
    expect(screen.getByLabelText("Undo")).toBeDisabled();
    expect(screen.getByLabelText("Redo")).toBeDisabled();
  });

  it("shows Run and History buttons for saved workflows", () => {
    // Set a workflow ID to simulate saved state
    useEditorStore.setState({
      workflowId: "019d0000-0000-7000-8000-000000000042",
    });
    renderWithProviders(<EditorToolbar />);

    expect(screen.getByText("Run")).toBeInTheDocument();
    expect(screen.getByText("History")).toBeInTheDocument();

    // Clean up
    useEditorStore.setState({ workflowId: null });
  });

  it("Run button triggers workflow execution (no dead window event)", () => {
    mockExecute.mockClear();
    useEditorStore.setState({
      workflowId: "019d0000-0000-7000-8000-000000000042",
    });
    renderWithProviders(<EditorToolbar />);

    fireEvent.click(screen.getByText("Run"));
    expect(mockExecute).toHaveBeenCalledTimes(1);

    useEditorStore.setState({ workflowId: null });
  });

  it("disables Run while the canvas has unsaved edits (isDirty)", () => {
    // Run executes the SAVED definition; with pending edits it must not
    // silently run the stale saved version behind the user's back.
    mockExecute.mockClear();
    useEditorStore.setState({
      workflowId: "019d0000-0000-7000-8000-000000000042",
      isDirty: true,
    });
    renderWithProviders(<EditorToolbar />);

    expect(screen.getByText("Run").closest("button")).toBeDisabled();
    fireEvent.click(screen.getByText("Run"));
    expect(mockExecute).not.toHaveBeenCalled();

    useEditorStore.setState({ workflowId: null, isDirty: false });
  });

  it("hides Run and History buttons for new workflows", () => {
    useEditorStore.setState({ workflowId: null });
    renderWithProviders(<EditorToolbar />);

    expect(screen.queryByText("Run")).not.toBeInTheDocument();
    expect(screen.queryByText("History")).not.toBeInTheDocument();
  });
});
