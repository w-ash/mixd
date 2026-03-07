import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { WorkflowDetail } from "./WorkflowDetail";

// Mock the WorkflowGraph since React Flow doesn't render in jsdom
vi.mock("@/components/shared/WorkflowGraph", () => ({
  WorkflowGraph: ({ tasks }: { tasks: unknown[] }) => (
    <div data-testid="workflow-graph">Graph with {tasks.length} tasks</div>
  ),
}));

// Mock useParams to return a workflow ID
vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useParams: () => ({ id: "1" }),
  };
});

const mockWorkflow = {
  id: 1,
  name: "Current Obsessions",
  description: "Tracks with 8+ plays in last 30 days",
  is_template: true,
  source_template: "current_obsessions",
  task_count: 3,
  node_types: [
    "source.liked_tracks",
    "filter.play_count",
    "destination.playlist",
  ],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-02-15T12:00:00Z",
  definition: {
    id: "current_obsessions",
    name: "Current Obsessions",
    description: "Tracks with 8+ plays in last 30 days",
    version: "1.0",
    tasks: [
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
        config: { name: "Current Obsessions" },
        upstream: ["filter"],
      },
    ],
  },
};

describe("WorkflowDetail", () => {
  it("renders workflow name and metadata", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () => {
        return HttpResponse.json(mockWorkflow, { status: 200 });
      }),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Current Obsessions")).toBeInTheDocument();
    });

    expect(screen.getByText("3 tasks")).toBeInTheDocument();
    expect(screen.getByText("Template")).toBeInTheDocument();
  });

  it("renders the workflow graph component", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () => {
        return HttpResponse.json(mockWorkflow, { status: 200 });
      }),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByTestId("workflow-graph")).toBeInTheDocument();
    });

    expect(screen.getByText("Graph with 3 tasks")).toBeInTheDocument();
  });

  it("renders node type badges", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () => {
        return HttpResponse.json(mockWorkflow, { status: 200 });
      }),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("source")).toBeInTheDocument();
    });

    expect(screen.getByText("filter")).toBeInTheDocument();
    expect(screen.getByText("destination")).toBeInTheDocument();
  });

  it("renders error state for nonexistent workflow", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () => {
        return HttpResponse.json(
          { error: { code: "NOT_FOUND", message: "Not found" } },
          { status: 404 },
        );
      }),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Workflow not found")).toBeInTheDocument();
    });
  });

  it("shows back link to workflows list", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () => {
        return HttpResponse.json(mockWorkflow, { status: 200 });
      }),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Workflows")).toBeInTheDocument();
    });

    const backLink = screen.getByText("Workflows").closest("a");
    expect(backLink).toHaveAttribute("href", "/workflows");
  });
});
