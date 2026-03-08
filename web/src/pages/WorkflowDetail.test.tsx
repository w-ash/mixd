import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { WorkflowDetail } from "./WorkflowDetail";

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
  definition_version: 3,
  task_count: 3,
  node_types: [
    "source.liked_tracks",
    "filter.play_count",
    "destination.playlist",
  ],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-02-15T12:00:00Z",
  last_run: {
    id: 5,
    status: "completed",
    definition_version: 2,
    completed_at: "2026-02-15T11:00:00Z",
    output_track_count: 20,
  },
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

const emptyRuns = { data: [], total: 0, limit: 10, offset: 0 };

describe("WorkflowDetail", () => {
  it("renders workflow name and description", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Current Obsessions")).toBeInTheDocument();
    });

    expect(
      screen.getByText("Tracks with 8+ plays in last 30 days"),
    ).toBeInTheDocument();
  });

  it("renders pipeline strip with correct node labels", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Source")).toBeInTheDocument();
    });

    expect(screen.getByText("Filter")).toBeInTheDocument();
    expect(screen.getByText("Destination")).toBeInTheDocument();
  });

  it("shows template badge for template workflows", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Template")).toBeInTheDocument();
    });
  });

  it("renders last run card with status", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Completed")).toBeInTheDocument();
    });

    expect(screen.getByText("20 tracks")).toBeInTheDocument();
    expect(screen.getByText("Details")).toBeInTheDocument();
  });

  it("shows version mismatch warning in last run card", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(
        screen.getByText("Definition changed since last run"),
      ).toBeInTheDocument();
    });
  });

  it("renders error state for nonexistent workflow", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(
          { error: { code: "NOT_FOUND", message: "Not found" } },
          { status: 404 },
        ),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Workflow not found")).toBeInTheDocument();
    });
  });

  it("shows back link to workflows list", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Workflows")).toBeInTheDocument();
    });

    const backLink = screen.getByText("Workflows").closest("a");
    expect(backLink).toHaveAttribute("href", "/workflows");
  });

  it("shows run button", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(mockWorkflow, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("Run")).toBeInTheDocument();
    });
  });

  it("renders no runs message when no run history", async () => {
    const workflowNoRun = { ...mockWorkflow, last_run: null };

    server.use(
      http.get("*/api/v1/workflows/:id", () =>
        HttpResponse.json(workflowNoRun, { status: 200 }),
      ),
      http.get("*/api/v1/workflows/:id/runs", () =>
        HttpResponse.json(emptyRuns, { status: 200 }),
      ),
    );

    renderWithProviders(<WorkflowDetail />);

    await waitFor(() => {
      expect(screen.getByText("No runs yet")).toBeInTheDocument();
    });
  });
});
