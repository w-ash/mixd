import { describe, expect, it, vi } from "vitest";

// Mock smart-edge (CommonJS/ESM incompatibility in jsdom)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

// Mock React Flow — EditorCanvas is fundamentally a ReactFlow wrapper. The
// stand-in renders each node through the `nodeTypes` map it receives, as
// React Flow does, so the canvas's node components are exercised.
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    nodes,
    nodeTypes,
    children,
  }: {
    nodes: { id: string; type: string; data: unknown }[];
    nodeTypes: Record<string, React.ComponentType<{ data: unknown }>>;
    children?: React.ReactNode;
  }) => (
    <div data-testid="react-flow">
      {nodes.map((node) => {
        const NodeComponent = nodeTypes[node.type];
        return <NodeComponent key={node.id} data={node.data} />;
      })}
      {children}
    </div>
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

// The editor store imports the ELK layout module; layout is not under test
// here, so stub it to keep the async ELK worker out of jsdom.
vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue([]),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

import { useEditorStore } from "#/stores/editor-store";
import { renderWithProviders, screen } from "#/test/test-utils";

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

  it("renders each store node as its category card with its task id", () => {
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
        {
          id: "flt_1",
          type: "filter",
          position: { x: 200, y: 0 },
          data: {
            taskId: "flt_1",
            nodeType: "filter.play_count",
            config: { min_plays: 5 },
          },
        },
      ],
      edges: [],
    });

    renderWithProviders(<EditorCanvas />);

    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("src_1")).toBeInTheDocument();
    expect(screen.getByText("Filter")).toBeInTheDocument();
    expect(screen.getByText("flt_1")).toBeInTheDocument();
  });
});
