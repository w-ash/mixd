import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockMatchMedia } from "#/test/test-utils";

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

describe("WorkflowEditor mobile gate", () => {
  afterEach(() => {
    mockMatchMedia(1280);
  });

  function renderEditAtMobile(path: string) {
    return renderWithProviders(
      <Routes>
        <Route path="workflows/:id/edit" element={<WorkflowEditor />} />
      </Routes>,
      { routerProps: { initialEntries: [path] } },
    );
  }

  it("renders the placeholder below lg: instead of mounting React Flow", () => {
    mockMatchMedia(390);
    renderEditAtMobile("/workflows/abc/edit");

    expect(
      screen.getByText("Workflow editing needs a larger screen"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("react-flow")).not.toBeInTheDocument();
  });

  it("placeholder's primary CTA links to the workflow's runs view", () => {
    mockMatchMedia(390);
    renderEditAtMobile("/workflows/abc/edit");

    const cta = screen.getByRole("link", { name: /View runs/ });
    expect(cta).toHaveAttribute("href", "/workflows/abc");
  });
});
