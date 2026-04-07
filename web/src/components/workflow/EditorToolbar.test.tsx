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

  it("hides Run and History buttons for new workflows", () => {
    useEditorStore.setState({ workflowId: null });
    renderWithProviders(<EditorToolbar />);

    expect(screen.queryByText("Run")).not.toBeInTheDocument();
    expect(screen.queryByText("History")).not.toBeInTheDocument();
  });
});
