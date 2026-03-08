import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { Workflows } from "./Workflows";

describe("Workflows", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Workflows />);

    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders workflow table when API returns data", async () => {
    server.use(
      http.get("*/api/v1/workflows", () => {
        return HttpResponse.json(
          {
            data: [
              {
                id: 1,
                name: "Current Obsessions",
                description: "Tracks with 8+ plays in last 30 days",
                is_template: true,
                source_template: "current_obsessions",
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
                id: 2,
                name: "My Custom Flow",
                description: null,
                is_template: false,
                source_template: null,
                definition_version: 1,
                task_count: 2,
                node_types: ["source.liked_tracks", "destination.playlist"],
                updated_at: "2026-03-01T08:30:00Z",
              },
            ],
            total: 2,
            limit: 50,
            offset: 0,
          },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("Current Obsessions")).toBeInTheDocument();
    });

    expect(screen.getByText("My Custom Flow")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Template")).toBeInTheDocument();
  });

  it("shows last run status in the table", async () => {
    server.use(
      http.get("*/api/v1/workflows", () => {
        return HttpResponse.json(
          {
            data: [
              {
                id: 1,
                name: "Current Obsessions",
                description: null,
                is_template: false,
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
            ],
            total: 1,
            limit: 50,
            offset: 0,
          },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("Completed")).toBeInTheDocument();
    });
  });

  it("renders per-row run buttons", async () => {
    server.use(
      http.get("*/api/v1/workflows", () => {
        return HttpResponse.json(
          {
            data: [
              {
                id: 1,
                name: "Flow A",
                description: null,
                is_template: false,
                definition_version: 1,
                task_count: 2,
                node_types: ["source"],
                updated_at: "2026-02-15T12:00:00Z",
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("Flow A")).toBeInTheDocument();
    });

    expect(screen.getByTitle("Run workflow")).toBeInTheDocument();
  });

  it("renders empty state when API returns no workflows", async () => {
    server.use(
      http.get("*/api/v1/workflows", () => {
        return HttpResponse.json(
          { data: [], total: 0, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("No workflows yet")).toBeInTheDocument();
    });
  });

  it("renders error state when API fails", async () => {
    server.use(
      http.get("*/api/v1/workflows", () => {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load workflows")).toBeInTheDocument();
    });
  });
});
