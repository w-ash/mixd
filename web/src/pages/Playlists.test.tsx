import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { makePlaylistSummary } from "@/test/factories";
import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { Playlists } from "./Playlists";

describe("Playlists", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Playlists />);

    // Skeleton elements should be present during loading
    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders playlist table when API returns data", async () => {
    const playlists = [
      makePlaylistSummary({
        id: 1,
        name: "Chill Vibes",
        description: "Relaxing tracks",
        track_count: 42,
      }),
      makePlaylistSummary({
        id: 2,
        name: "Workout Mix",
        description: null,
        track_count: 18,
        connector_links: [
          {
            connector_name: "spotify",
            sync_direction: "push",
            sync_status: "synced",
          },
          {
            connector_name: "lastfm",
            sync_direction: "pull",
            sync_status: "never_synced",
          },
        ],
        updated_at: "2026-02-20T08:30:00Z",
      }),
    ];
    server.use(
      http.get("*/api/v1/playlists", () => {
        return HttpResponse.json(
          { data: playlists, total: 2, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Playlists />);

    await waitFor(() => {
      expect(screen.getByText("Chill Vibes")).toBeInTheDocument();
    });

    expect(screen.getByText("Workout Mix")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("18")).toBeInTheDocument();
    expect(screen.getByText("Relaxing tracks")).toBeInTheDocument();
  });

  it("renders empty state when API returns no playlists", async () => {
    server.use(
      http.get("*/api/v1/playlists", () => {
        return HttpResponse.json(
          { data: [], total: 0, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Playlists />);

    await waitFor(() => {
      expect(screen.getByText("No playlists yet")).toBeInTheDocument();
    });
  });

  it("renders error state when API fails", async () => {
    server.use(
      http.get("*/api/v1/playlists", () => {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        );
      }),
    );

    renderWithProviders(<Playlists />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load playlists")).toBeInTheDocument();
    });
  });

  it("shows pagination controls when total exceeds page size", async () => {
    const playlists = Array.from({ length: 50 }, (_, i) =>
      makePlaylistSummary({ id: i + 1, name: `Playlist ${i + 1}` }),
    );

    server.use(
      http.get("*/api/v1/playlists", () => {
        return HttpResponse.json(
          { data: playlists, total: 120, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Playlists />);

    await waitFor(() => {
      expect(screen.getByText("Playlist 1")).toBeInTheDocument();
    });

    // Pagination controls should appear
    expect(screen.getByText(/1–50 of 120/)).toBeInTheDocument();
    expect(screen.getByLabelText("Go to next page")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // page 3 link
  });

  it("hides pagination when all items fit on one page", async () => {
    server.use(
      http.get("*/api/v1/playlists", () => {
        return HttpResponse.json(
          {
            data: [
              makePlaylistSummary({
                id: 1,
                name: "Only Playlist",
                description: null,
                track_count: 5,
                connector_links: [],
              }),
            ],
            total: 1,
            limit: 50,
            offset: 0,
          },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Playlists />);

    await waitFor(() => {
      expect(screen.getByText("Only Playlist")).toBeInTheDocument();
    });

    // No pagination controls
    expect(screen.queryByLabelText("Go to next page")).not.toBeInTheDocument();
  });
});
