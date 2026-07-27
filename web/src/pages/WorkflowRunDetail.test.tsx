import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { WorkflowRunDetail } from "./WorkflowRunDetail";

// Mock React Flow (jsdom can't render canvas). Node statuses are surfaced as
// test ids so the persisted-vs-live overlay is observable.
vi.mock("#/components/shared/WorkflowGraph", () => ({
  WorkflowGraph: ({
    tasks,
    nodeStatuses,
  }: {
    tasks: unknown[];
    nodeStatuses?: Map<string, { status: string }>;
  }) => (
    <div data-testid="workflow-graph">
      Graph with {tasks.length} tasks
      {[...(nodeStatuses ?? new Map())].map(([nodeId, status]) => (
        <span key={nodeId} data-testid={`node-status-${nodeId}`}>
          {status.status}
        </span>
      ))}
    </div>
  ),
}));

// Partial mock: keep the real provider (test-utils mounts it) but let a test
// drive what the execution context reports. Set `mockContextOverride` to
// simulate this tab streaming some other workflow.
const { mockContextOverride } = vi.hoisted(() => ({
  mockContextOverride: { current: null as Record<string, unknown> | null },
}));
vi.mock("#/contexts/WorkflowExecutionContext", async (importActual) => {
  const actual =
    await importActual<typeof import("#/contexts/WorkflowExecutionContext")>();
  return {
    ...actual,
    useWorkflowExecutionContext: () => {
      const real = actual.useWorkflowExecutionContext();
      return mockContextOverride.current
        ? { ...real, ...mockContextOverride.current }
        : real;
    },
  };
});

// Mock useParams
vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useParams: () => ({ id: "1", runId: "5" }),
  };
});

const mockWorkflow = {
  id: 1,
  name: "Hidden Gems",
  description: "Tracks unplayed for 6 months",
  definition_version: 3,
  task_count: 2,
  node_types: ["source", "filter"],
  definition: {
    id: "hidden_gems",
    name: "Hidden Gems",
    tasks: [],
  },
};

const mockRun = {
  id: 5,
  run_number: 5,
  workflow_id: 1,
  status: "completed",
  definition_version: 2,
  started_at: "2026-02-15T10:00:00Z",
  completed_at: "2026-02-15T10:01:30Z",
  duration_ms: 90000,
  output_track_count: 15,
  output_playlist_id: 10,
  error_message: null as string | null,
  created_at: "2026-02-15T10:00:00Z",
  definition_snapshot: {
    id: "hidden_gems",
    name: "Hidden Gems",
    description: "",
    version: "1.0",
    tasks: [
      {
        id: "source",
        type: "source.liked_tracks",
        config: {},
        upstream: [],
      },
      {
        id: "filter",
        type: "filter.play_count",
        config: { metric_name: "play_count", min_value: 3 },
        upstream: ["source"],
      },
      {
        id: "update",
        type: "destination.update_playlist",
        config: { playlist_id: "test-playlist", connector: "spotify" },
        upstream: ["filter"],
      },
    ],
  },
  nodes: [
    {
      id: 1,
      node_id: "source",
      node_type: "source.liked_tracks",
      status: "completed",
      started_at: "2026-02-15T10:00:01Z",
      completed_at: "2026-02-15T10:00:30Z",
      duration_ms: 29000,
      input_track_count: null,
      output_track_count: 100,
      error_message: null,
      execution_order: 1,
      node_details: null,
    },
    {
      id: 2,
      node_id: "filter",
      node_type: "filter.play_count",
      status: "completed",
      started_at: "2026-02-15T10:00:31Z",
      completed_at: "2026-02-15T10:01:00Z",
      duration_ms: 29000,
      input_track_count: 100,
      output_track_count: 15,
      error_message: null,
      execution_order: 2,
      node_details: null,
    },
    {
      id: 3,
      node_id: "update",
      node_type: "destination.update_playlist",
      status: "completed",
      started_at: "2026-02-15T10:01:01Z",
      completed_at: "2026-02-15T10:01:30Z",
      duration_ms: 29000,
      input_track_count: 15,
      output_track_count: 15,
      error_message: null,
      execution_order: 3,
      node_details: {
        playlist_changes: {
          tracks_added: [
            { track_id: 1, title: "Midnight City", artists: "M83" },
          ],
          tracks_removed: [
            { track_id: 2, title: "Fade to Black", artists: "Metallica" },
          ],
          tracks_moved: 0,
          playlist_id: "test-playlist",
          connector: "spotify",
        },
      },
    },
  ],
  output_tracks: [
    {
      track_id: 1,
      title: "Midnight City",
      artists: "M83",
      rank: 1,
      metric_name: "play_count",
      metric_value: 12,
    },
  ],
};

function setupHandlers(
  runOverrides: Partial<typeof mockRun> = {},
  workflowOverrides: Partial<typeof mockWorkflow> = {},
) {
  server.use(
    http.get("*/api/v1/workflows/:id/runs/:runId", () =>
      HttpResponse.json({ ...mockRun, ...runOverrides }, { status: 200 }),
    ),
    http.get("*/api/v1/workflows/:id", () =>
      HttpResponse.json(
        { ...mockWorkflow, ...workflowOverrides },
        { status: 200 },
      ),
    ),
  );
}

describe("WorkflowRunDetail", () => {
  it("shows named back link from parent workflow", async () => {
    setupHandlers();

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("Hidden Gems")).toBeInTheDocument();
    });

    const backLink = screen.getByText("Hidden Gems").closest("a");
    expect(backLink).toHaveAttribute("href", "/workflows/1");
  });

  it("renders run header with status and Run Again button", async () => {
    setupHandlers();

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("Run #5")).toBeInTheDocument();
    });

    // Multiple "Completed" badges exist (header + per-node), so check count
    const completedBadges = screen.getAllByText("Completed");
    expect(completedBadges.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Run Again")).toBeInTheDocument();
  });

  it("shows version mismatch warning when definition changed", async () => {
    setupHandlers({ definition_version: 2 }, { definition_version: 3 });

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(
        screen.getByText(/Workflow definition has changed/),
      ).toBeInTheDocument();
    });
  });

  it("does not show version mismatch when versions match", async () => {
    setupHandlers({ definition_version: 3 }, { definition_version: 3 });

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("Run #5")).toBeInTheDocument();
    });

    expect(
      screen.queryByText(/Workflow definition has changed/),
    ).not.toBeInTheDocument();
  });

  it("renders run metadata (started, duration, output)", async () => {
    setupHandlers();

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("15 tracks")).toBeInTheDocument();
    });
  });

  it("renders node execution details", async () => {
    setupHandlers();

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("source")).toBeInTheDocument();
    });

    expect(screen.getByText("filter")).toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("Filter")).toBeInTheDocument();
  });

  it("expands node details to show track decisions", async () => {
    // Use a run with no output_tracks to avoid duplicate text matches
    setupHandlers({ output_tracks: [] });
    const user = userEvent.setup();

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("update")).toBeInTheDocument();
    });

    // "Fade to Black" (removed track) should NOT be visible before expanding
    expect(screen.queryByText("Fade to Black")).not.toBeInTheDocument();

    // The destination node has playlist_changes, so it should be expandable
    const updateRow = screen.getByText("update").closest("[role='button']");
    expect(updateRow).toBeInTheDocument();

    // biome-ignore lint/style/noNonNullAssertion: guarded by assertion above
    await user.click(updateRow!);

    // Now playlist changes should be visible
    await waitFor(() => {
      expect(screen.getByText("Fade to Black")).toBeInTheDocument();
    });

    expect(screen.getByText("Added to playlist (1)")).toBeInTheDocument();
    expect(screen.getByText("Removed from playlist (1)")).toBeInTheDocument();
  });

  it("renders output tracks table", async () => {
    setupHandlers();

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("Output Tracks")).toBeInTheDocument();
    });

    expect(screen.getAllByText("Midnight City").length).toBeGreaterThan(0);
    expect(screen.getAllByText("M83").length).toBeGreaterThan(0);
  });

  it("renders error state for nonexistent run", async () => {
    server.use(
      http.get("*/api/v1/workflows/:id/runs/:runId", () =>
        HttpResponse.json(
          { error: { code: "NOT_FOUND", message: "Not found" } },
          { status: 404 },
        ),
      ),
    );

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(screen.getByText("Run not found")).toBeInTheDocument();
    });
  });

  it("shows error message for failed runs", async () => {
    setupHandlers({
      status: "failed",
      error_message: "Spotify API rate limit exceeded",
    });

    renderWithProviders(<WorkflowRunDetail />);

    await waitFor(() => {
      expect(
        screen.getByText("Spotify API rate limit exceeded"),
      ).toBeInTheDocument();
    });
  });

  describe("live run", () => {
    /**
     * active-runs claiming `runId` is in flight.
     *
     * The id MUST be a string: the page compares it against the router param,
     * so a numeric `5` silently never matches `"5"` and every liveness
     * assertion below would pass off the persisted status instead.
     */
    function activeRunsFor(runId: string) {
      return http.get("*/api/v1/workflows/active-runs", () =>
        HttpResponse.json(
          {
            data: [
              {
                id: runId,
                // String, like the router params these are compared against.
                workflow_id: "1",
                run_number: 5,
                status: "running",
                operation_id: "op-live",
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          },
          { status: 200 },
        ),
      );
    }

    it("shows Running while this run is in flight, not the persisted status", async () => {
      // The page previously rendered whatever the DB row said at load time and
      // never moved — a live run looked frozen. The persisted row is left at
      // "pending" so "Running" can only come from the live path.
      setupHandlers({ status: "pending", completed_at: undefined });
      server.use(activeRunsFor("5"));

      renderWithProviders(<WorkflowRunDetail />);

      await waitFor(() => {
        expect(screen.getByText("Running")).toBeInTheDocument();
      });
      expect(screen.queryByText("Pending")).not.toBeInTheDocument();
    });

    it("overlays live node statuses when this tab is driving this run", async () => {
      setupHandlers({ status: "running", completed_at: undefined });
      server.use(activeRunsFor("5"));
      mockContextOverride.current = {
        workflowId: "1",
        nodeStatuses: new Map([
          ["source", { nodeId: "source", status: "running" }],
        ]),
      };

      renderWithProviders(<WorkflowRunDetail />);

      // The live event wins over the persisted "completed" node record.
      await waitFor(() => {
        expect(screen.getByTestId("node-status-source")).toHaveTextContent(
          "running",
        );
      });
      mockContextOverride.current = null;
    });

    it("ignores live node statuses belonging to a different workflow", async () => {
      // Node ids are definition-local ("source"), so a run streaming elsewhere
      // in this tab collides by name — leaking its statuses onto this run's
      // graph would show another workflow's progress as this one's.
      setupHandlers({ status: "running", completed_at: undefined });
      server.use(activeRunsFor("5"));
      mockContextOverride.current = {
        workflowId: "some-other-workflow",
        nodeStatuses: new Map([
          ["source", { nodeId: "source", status: "failed" }],
        ]),
      };

      renderWithProviders(<WorkflowRunDetail />);

      await waitFor(() => {
        expect(screen.getByTestId("node-status-source")).toBeInTheDocument();
      });
      // Persisted record stands; the foreign "failed" never lands.
      expect(screen.getByTestId("node-status-source")).toHaveTextContent(
        "completed",
      );
      mockContextOverride.current = null;
    });

    it("does not go live for a different run on the same workflow", async () => {
      // Opening an older run while a newer one streams must not hijack the
      // connection or mislabel the run being viewed.
      setupHandlers();
      server.use(activeRunsFor("999"));

      renderWithProviders(<WorkflowRunDetail />);

      // Header badge plus one per completed node, so match on count.
      await waitFor(() => {
        expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
      });
      expect(screen.queryByText("Running")).not.toBeInTheDocument();
    });
  });
});
