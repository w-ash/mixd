import { describe, expect, it, vi } from "vitest";

// Mock the problematic smart-edge package (CommonJS/ESM incompatibility in jsdom)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

// Mock ELK layout to avoid async layout computation in tests
vi.mock("#/lib/workflow-layout", async (importOriginal) => {
  const actual = await importOriginal<typeof import("#/lib/workflow-layout")>();
  return {
    ...actual,
    layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  };
});

import type { WorkflowTaskDefSchema } from "#/api/generated/model";
import { renderWithProviders, screen } from "#/test/test-utils";

import { WorkflowGraph } from "./WorkflowGraph";

const sampleTasks: WorkflowTaskDefSchema[] = [
  { id: "src", type: "source.liked_tracks", config: {}, upstream: [] },
  {
    id: "flt",
    type: "filter.play_count",
    config: { min_plays: 5 },
    upstream: ["src"],
  },
  {
    id: "dst",
    type: "destination.save_playlist",
    config: { name: "Test" },
    upstream: ["flt"],
  },
];

describe("WorkflowGraph", () => {
  it("renders without crashing with tasks", () => {
    const { container } = renderWithProviders(
      <div style={{ width: 800, height: 600 }}>
        <WorkflowGraph tasks={sampleTasks} />
      </div>,
    );
    // React Flow renders its container
    expect(container.querySelector(".react-flow")).toBeInTheDocument();
  });

  it("shows loading overlay while computing layout", () => {
    renderWithProviders(
      <div style={{ width: 800, height: 600 }}>
        <WorkflowGraph tasks={sampleTasks} />
      </div>,
    );
    expect(screen.getByText("Computing layout...")).toBeInTheDocument();
  });

  it("renders empty state without errors", () => {
    const { container } = renderWithProviders(
      <div style={{ width: 800, height: 600 }}>
        <WorkflowGraph tasks={[]} />
      </div>,
    );
    expect(container.querySelector(".react-flow")).toBeInTheDocument();
    // No loading overlay for empty tasks
    expect(screen.queryByText("Computing layout...")).not.toBeInTheDocument();
  });
});
