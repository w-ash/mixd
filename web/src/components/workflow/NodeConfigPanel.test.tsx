import { describe, expect, it, vi } from "vitest";

// Mock smart-edge (transitive dep via WorkflowGraph chain)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
  useReactFlow: () => ({ fitView: vi.fn() }),
  useNodesInitialized: () => false,
}));

vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue([]),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

import { useEditorStore } from "#/stores/editor-store";
import { renderWithProviders, screen } from "#/test/test-utils";

import { NodeConfigPanel } from "./NodeConfigPanel";

describe("NodeConfigPanel", () => {
  it("renders nothing when no node is selected", () => {
    useEditorStore.setState({ selectedNodeId: null, nodes: [] });
    const { container } = renderWithProviders(<NodeConfigPanel />);
    // Panel is hidden — no visible content
    expect(container.textContent).toBe("");
  });

  it("renders panel with node type badge when node is selected", () => {
    useEditorStore.setState({
      selectedNodeId: "src_1",
      nodes: [
        {
          id: "src_1",
          type: "source",
          position: { x: 0, y: 0 },
          data: {
            taskId: "src_1",
            nodeType: "source.liked_tracks",
            config: {},
          },
        },
      ],
      edges: [],
    });

    renderWithProviders(<NodeConfigPanel />);

    // Should show the close button and node category badge
    expect(screen.getByLabelText("Close panel")).toBeInTheDocument();
    expect(screen.getByText("source")).toBeInTheDocument();
    // Task ID field
    expect(screen.getByLabelText("Task ID")).toBeInTheDocument();
  });

  it("shows no-config message for nodes without schema", () => {
    useEditorStore.setState({
      selectedNodeId: "src_1",
      nodes: [
        {
          id: "src_1",
          type: "source",
          position: { x: 0, y: 0 },
          data: {
            taskId: "src_1",
            nodeType: "source.liked_tracks",
            config: {},
          },
        },
      ],
      edges: [],
    });

    renderWithProviders(<NodeConfigPanel />);
    expect(screen.getByText("No configuration needed")).toBeInTheDocument();
  });
});
