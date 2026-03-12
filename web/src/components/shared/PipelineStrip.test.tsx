import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NodeStatus } from "@/lib/sse-types";
import { PipelineStrip } from "./PipelineStrip";

// Override the global scrollIntoView no-op with a spy for assertion
const scrollIntoViewMock = vi.fn();
beforeEach(() => {
  Element.prototype.scrollIntoView = scrollIntoViewMock;
});
afterEach(() => {
  scrollIntoViewMock.mockClear();
});

const mockTasks = [
  {
    id: "source",
    type: "source.liked_tracks",
    config: { service: "spotify" },
    upstream: [],
  },
  {
    id: "filter",
    type: "filter.play_count",
    config: { min_plays: 8 },
    upstream: ["source"],
  },
  {
    id: "dest",
    type: "destination.playlist",
    config: { name: "Test" },
    upstream: ["filter"],
  },
];

describe("PipelineStrip", () => {
  it("renders correct number of node dots with labels", () => {
    render(<PipelineStrip tasks={mockTasks} />);

    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("Filter")).toBeInTheDocument();
    expect(screen.getByText("Destination")).toBeInTheDocument();
  });

  it("returns null for empty tasks", () => {
    const { container } = render(<PipelineStrip tasks={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("applies custom className", () => {
    const { container } = render(
      <PipelineStrip tasks={mockTasks} className="mt-4" />,
    );
    expect(container.firstChild).toHaveClass("mt-4");
  });

  it("shows node tooltips with type info", () => {
    render(<PipelineStrip tasks={mockTasks} />);

    expect(screen.getByTitle("Source: source")).toBeInTheDocument();
    expect(screen.getByTitle("Filter: filter")).toBeInTheDocument();
    expect(screen.getByTitle("Destination: dest")).toBeInTheDocument();
  });

  it("shows progress bar during execution", () => {
    const statuses = new Map<string, NodeStatus>([
      [
        "source",
        {
          nodeId: "source",
          nodeType: "source.liked_tracks",
          status: "completed",
          executionOrder: 1,
          totalNodes: 3,
        },
      ],
      [
        "filter",
        {
          nodeId: "filter",
          nodeType: "filter.play_count",
          status: "running",
          executionOrder: 2,
          totalNodes: 3,
        },
      ],
    ]);

    render(<PipelineStrip tasks={mockTasks} nodeStatuses={statuses} />);

    // Progress description should show current step
    expect(screen.getByText(/Step 2\/3/)).toBeInTheDocument();
    expect(screen.getByText(/Step 2\/3 — Filter/)).toBeInTheDocument();
  });

  it("does not show progress bar when not executing", () => {
    render(<PipelineStrip tasks={mockTasks} />);

    expect(screen.queryByText(/Step/)).not.toBeInTheDocument();
  });

  it("shows initializing state when executing with no statuses yet", () => {
    render(<PipelineStrip tasks={mockTasks} isExecuting />);

    // Progress section visible with initializing text
    expect(screen.getByText("Initializing…")).toBeInTheDocument();
    // No step counter since no node statuses exist
    expect(screen.queryByText(/Step/)).not.toBeInTheDocument();
  });

  it("wraps dot chain in a scrollable container", () => {
    render(<PipelineStrip tasks={mockTasks} />);

    const dot = screen.getByTitle("Source: source");
    const scrollContainer = dot.closest(".overflow-x-auto");
    expect(scrollContainer).toBeInTheDocument();
  });

  it("scrolls active node into view during execution", () => {
    const statuses = new Map<string, NodeStatus>([
      [
        "source",
        {
          nodeId: "source",
          nodeType: "source.liked_tracks",
          status: "completed",
          executionOrder: 1,
          totalNodes: 3,
        },
      ],
      [
        "filter",
        {
          nodeId: "filter",
          nodeType: "filter.play_count",
          status: "running",
          executionOrder: 2,
          totalNodes: 3,
        },
      ],
    ]);

    render(<PipelineStrip tasks={mockTasks} nodeStatuses={statuses} />);

    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "nearest",
      inline: "center",
    });
  });
});
