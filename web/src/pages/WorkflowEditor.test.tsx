import { describe, expect, it, vi } from "vitest";

// Mock problematic dependencies
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  ),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Controls: () => null,
  MiniMap: () => null,
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  useReactFlow: () => ({
    fitView: vi.fn(),
    screenToFlowPosition: vi.fn().mockReturnValue({ x: 0, y: 0 }),
  }),
  useNodesInitialized: () => false,
}));

vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue([]),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

import { renderWithProviders, screen } from "#/test/test-utils";

import WorkflowEditor from "./WorkflowEditor";

describe("WorkflowEditor", () => {
  it("renders the editor layout", () => {
    renderWithProviders(<WorkflowEditor />, {
      routerProps: { initialEntries: ["/workflows/new"] },
    });

    // Toolbar elements
    expect(screen.getByLabelText("Back")).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow name")).toBeInTheDocument();
    expect(screen.getByText("Save")).toBeInTheDocument();

    // Canvas
    expect(screen.getByTestId("react-flow")).toBeInTheDocument();
  });

  it("shows node palette with search input", () => {
    renderWithProviders(<WorkflowEditor />, {
      routerProps: { initialEntries: ["/workflows/new"] },
    });

    // Node palette has search input
    expect(screen.getByPlaceholderText("Search nodes...")).toBeInTheDocument();
  });
});
