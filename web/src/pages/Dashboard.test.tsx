import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { Dashboard } from "./Dashboard";

const mockStats = {
  total_tracks: 1234,
  total_plays: 56789,
  total_playlists: 12,
  total_liked: 456,
  tracks_by_connector: { spotify: 1000, lastfm: 800 },
  liked_by_connector: { spotify: 300, lastfm: 150 },
  plays_by_connector: { lastfm: 45000, spotify: 11789 },
  playlists_by_connector: { spotify: 3 },
};

describe("Dashboard", () => {
  it("renders loading skeleton while fetching", () => {
    renderWithProviders(<Dashboard />);

    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders stat cards with formatted counts", async () => {
    server.use(
      http.get("*/api/v1/stats/dashboard", () => {
        return HttpResponse.json(mockStats, { status: 200 });
      }),
    );

    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("1,234")).toBeInTheDocument();
    });

    expect(screen.getByText("56,789")).toBeInTheDocument();
    expect(screen.getByText("456")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();

    expect(screen.getByText("Tracks across 2 services")).toBeInTheDocument();
    expect(screen.getByText("Total Plays")).toBeInTheDocument();
    expect(screen.getByText("Liked Tracks")).toBeInTheDocument();
    expect(screen.getByText("Playlists \u00b7 3 linked")).toBeInTheDocument();
  });

  it("renders per-connector breakdowns", async () => {
    server.use(
      http.get("*/api/v1/stats/dashboard", () => {
        return HttpResponse.json(mockStats, { status: 200 });
      }),
    );

    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("1,234")).toBeInTheDocument();
    });

    // Per-connector breakdown counts (tracks_by_connector)
    expect(screen.getByText("1,000")).toBeInTheDocument();
    expect(screen.getByText("800")).toBeInTheDocument();

    // Per-connector breakdown counts (liked_by_connector)
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();
  });

  it("renders empty state when no tracks", async () => {
    server.use(
      http.get("*/api/v1/stats/dashboard", () => {
        return HttpResponse.json(
          {
            total_tracks: 0,
            total_plays: 0,
            total_playlists: 0,
            total_liked: 0,
            tracks_by_connector: {},
            liked_by_connector: {},
            plays_by_connector: {},
            playlists_by_connector: {},
          },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("No data yet")).toBeInTheDocument();
    });

    expect(
      screen.getByText("Connect services in Settings to get started."),
    ).toBeInTheDocument();
    expect(screen.getByText("Go to Settings")).toBeInTheDocument();
  });

  it("renders error state on API failure", async () => {
    server.use(
      http.get("*/api/v1/stats/dashboard", () => {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        );
      }),
    );

    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load statistics")).toBeInTheDocument();
    });
  });
});
