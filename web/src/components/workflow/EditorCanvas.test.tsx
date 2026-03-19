import { describe, expect, it, vi } from "vitest";

// Mock smart-edge (CommonJS/ESM incompatibility in jsdom)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

// Mock React Flow — EditorCanvas is fundamentally a ReactFlow wrapper
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  ),
  Background: () => <div data-testid="background" />,
  BackgroundVariant: { Dots: "dots" },
  Controls: () => <div data-testid="controls" />,
  MiniMap: () => <div data-testid="minimap" />,
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  useReactFlow: () => ({
    screenToFlowPosition: vi.fn().mockReturnValue({ x: 0, y: 0 }),
    fitView: vi.fn(),
  }),
}));

vi.mock("@/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue([]),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

import { useEditorStore } from "@/stores/editor-store";
import { renderWithProviders, screen } from "@/test/test-utils";

import { EditorCanvas } from "./EditorCanvas";

describe("EditorCanvas", () => {
  it("renders React Flow canvas with controls", () => {
    useEditorStore.setState({ nodes: [], edges: [] });
    renderWithProviders(<EditorCanvas />);

    expect(screen.getByTestId("react-flow")).toBeInTheDocument();
    expect(screen.getByTestId("controls")).toBeInTheDocument();
    expect(screen.getByTestId("minimap")).toBeInTheDocument();
    expect(screen.getByTestId("background")).toBeInTheDocument();
  });

  it("renders with nodes from store", () => {
    useEditorStore.setState({
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

    renderWithProviders(<EditorCanvas />);
    expect(screen.getByTestId("react-flow")).toBeInTheDocument();
  });
});
