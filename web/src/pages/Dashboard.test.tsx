import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { MatchMethodHealthSchema } from "#/api/generated/model";
import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

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
  preference_counts: { star: 42, yah: 88, hmm: 19, nah: 23 },
};

const mockMatchingHealth: MatchMethodHealthSchema = {
  stats: [
    {
      match_method: "direct_import",
      connector_name: "spotify",
      category: "Primary Import",
      description: "Standard Spotify import",
      total_count: 847,
      recent_count: 45,
      avg_confidence: 100.0,
      min_confidence: 100,
      max_confidence: 100,
    },
    {
      match_method: "artist_title",
      connector_name: "lastfm",
      category: "Primary Import",
      description: "Standard Last.fm import",
      total_count: 512,
      recent_count: 31,
      avg_confidence: 90.0,
      min_confidence: 85,
      max_confidence: 95,
    },
    {
      match_method: "canonical_reuse",
      connector_name: "lastfm",
      category: "Identity Resolution",
      description: "Canonical reuse — existing track matched",
      total_count: 242,
      recent_count: 12,
      avg_confidence: 92.3,
      min_confidence: 87,
      max_confidence: 100,
    },
    {
      match_method: "search_fallback",
      connector_name: "spotify",
      category: "Error Recovery",
      description: "Dead Spotify ID → search fallback",
      total_count: 15,
      recent_count: 2,
      avg_confidence: 42.0,
      min_confidence: 30,
      max_confidence: 55,
    },
  ],
  total_mappings: 1616,
  recent_days: 30,
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

  it("renders getting-started checklist when no tracks", async () => {
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
            preference_counts: {},
          },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Welcome to Mixd")).toBeInTheDocument();
    });

    expect(screen.getByText("Connect services")).toBeInTheDocument();
    expect(screen.getByText("Import your music")).toBeInTheDocument();
    expect(screen.getByText("Explore your library")).toBeInTheDocument();
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

  it("renders database unavailable state when DB is down", async () => {
    server.use(
      http.get("*/api/v1/stats/dashboard", () => {
        return HttpResponse.json(
          {
            error: {
              code: "DATABASE_UNAVAILABLE",
              message:
                "Database connection unavailable. Ensure PostgreSQL is running.",
            },
          },
          { status: 503 },
        );
      }),
    );

    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Database unavailable")).toBeInTheDocument();
    });

    expect(
      screen.getByText(
        "Cannot connect to PostgreSQL. Make sure the database is running, then reload.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Reload")).toBeInTheDocument();
  });
});

describe("Matching Health Section", () => {
  function useBothEndpoints(
    matchingOverride: MatchMethodHealthSchema = mockMatchingHealth,
  ) {
    server.use(
      http.get("*/api/v1/stats/dashboard", () => {
        return HttpResponse.json(mockStats, { status: 200 });
      }),
      http.get("*/api/v1/stats/matching", () => {
        return HttpResponse.json(matchingOverride, { status: 200 });
      }),
    );
  }

  it("renders matching health tables with categories", async () => {
    useBothEndpoints();
    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Match Method Health")).toBeInTheDocument();
    });

    // Category headers
    expect(screen.getByText("Primary Import")).toBeInTheDocument();
    expect(screen.getByText("Identity Resolution")).toBeInTheDocument();
    expect(screen.getByText("Error Recovery")).toBeInTheDocument();

    // Method names
    expect(screen.getByText("direct_import")).toBeInTheDocument();
    expect(screen.getByText("artist_title")).toBeInTheDocument();
    expect(screen.getByText("canonical_reuse")).toBeInTheDocument();
    expect(screen.getByText("search_fallback")).toBeInTheDocument();

    // Total mappings summary
    expect(screen.getByText(/1,616 total mappings/)).toBeInTheDocument();
  });

  it("does not render matching section when no mappings", async () => {
    useBothEndpoints({
      stats: [],
      total_mappings: 0,
      recent_days: 30,
    });

    renderWithProviders(<Dashboard />);

    // Wait for dashboard stats to load
    await waitFor(() => {
      expect(screen.getByText("1,234")).toBeInTheDocument();
    });

    expect(screen.queryByText("Match Method Health")).not.toBeInTheDocument();
  });

  it("shows confidence with appropriate color", async () => {
    useBothEndpoints();
    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Match Method Health")).toBeInTheDocument();
    });

    // High confidence (100.0) — success token
    const highConf = screen.getByText("100.0");
    expect(highConf).toHaveClass("text-status-connected");

    // Low confidence (42.0) — error token
    const lowConf = screen.getByText("42.0");
    expect(lowConf).toHaveClass("text-destructive");
  });

  it("displays formatted counts in table cells", async () => {
    useBothEndpoints();
    renderWithProviders(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Match Method Health")).toBeInTheDocument();
    });

    // Category totals (shown as badges in category headers)
    expect(screen.getByText("1,359")).toBeInTheDocument(); // Primary Import total
    // 242 appears both as category total and table cell (canonical_reuse total_count)
    expect(screen.getAllByText("242")).toHaveLength(2);
  });
});
