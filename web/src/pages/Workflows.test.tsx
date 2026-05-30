import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { Workflows } from "./Workflows";

const WF_A = "11111111-1111-1111-1111-111111111111";
const WF_B = "22222222-2222-2222-2222-222222222222";

function listResponse(data: unknown[]) {
  return HttpResponse.json(
    { data, total: data.length, limit: 50, offset: 0 },
    { status: 200 },
  );
}

describe("Workflows", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Workflows />);

    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders workflow table when API returns data", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Current Obsessions",
            description: "Tracks with 8+ plays in last 30 days",
            definition_version: 3,
            task_count: 4,
            node_types: [
              "source.liked_tracks",
              "filter.play_count",
              "sorter.play_count",
              "destination.playlist",
            ],
            updated_at: "2026-02-15T12:00:00Z",
            last_run: {
              id: 1,
              status: "completed",
              definition_version: 3,
              completed_at: "2026-02-15T11:00:00Z",
              output_track_count: 20,
            },
          },
          {
            id: WF_B,
            name: "My Custom Flow",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source.liked_tracks", "destination.playlist"],
            updated_at: "2026-03-01T08:30:00Z",
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Current Obsessions").length).toBeGreaterThan(
        0,
      );
    });

    expect(screen.getAllByText("My Custom Flow").length).toBeGreaterThan(0);
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows no Template badge — templates live in the gallery, not the list", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Current Obsessions",
            description: null,
            definition_version: 1,
            task_count: 3,
            node_types: ["source.liked_tracks"],
            updated_at: "2026-02-15T12:00:00Z",
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Current Obsessions").length).toBeGreaterThan(
        0,
      );
    });

    expect(screen.queryByText("Template")).not.toBeInTheDocument();
  });

  it("shows last run status in the table", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Current Obsessions",
            description: null,
            definition_version: 1,
            task_count: 3,
            node_types: ["source"],
            updated_at: "2026-02-15T12:00:00Z",
            last_run: {
              id: 5,
              status: "completed",
              definition_version: 1,
            },
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    });
  });

  it("renders per-row run, edit, and duplicate actions on every row", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Flow A",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source"],
            updated_at: "2026-02-15T12:00:00Z",
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Flow A").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByTitle("Run workflow").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Edit workflow").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Duplicate workflow").length).toBeGreaterThan(
      0,
    );
  });

  it("duplicates a workflow when its row Duplicate action is clicked", async () => {
    const user = userEvent.setup();
    let duplicatedId: string | null = null;

    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Flow A",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source"],
            updated_at: "2026-02-15T12:00:00Z",
          },
        ]),
      ),
      http.post("*/api/v1/workflows/:id/duplicate", ({ params }) => {
        duplicatedId = params.id as string;
        return HttpResponse.json(
          {
            id: WF_B,
            name: "Flow A (copy)",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source"],
            updated_at: "2026-03-01T08:30:00Z",
            definition: {
              id: WF_B,
              name: "Flow A (copy)",
              description: "",
              version: "1.0",
              tasks: [],
            },
          },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Flow A").length).toBeGreaterThan(0);
    });

    await user.click(screen.getAllByTitle("Duplicate workflow")[0]);

    await waitFor(() => {
      expect(duplicatedId).toBe(WF_A);
    });
  });

  it("renders empty state when API returns no workflows", async () => {
    server.use(http.get("*/api/v1/workflows", () => listResponse([])));

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("No workflows yet")).toBeInTheDocument();
    });
  });

  it("renders error state when API fails", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        ),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load workflows")).toBeInTheDocument();
    });
  });
});
