import { fireEvent } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { Library } from "./Library";

function makeTracks(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    title: `Track ${i + 1}`,
    artists: [{ name: `Artist ${i + 1}` }],
    album: `Album ${i + 1}`,
    duration_ms: 210000 + i * 1000,
    isrc: null,
    connector_names: ["spotify"],
    is_liked: i % 2 === 0,
  }));
}

function overrideTracks(data: unknown[], total?: number) {
  server.use(
    http.get("*/api/v1/tracks", () => {
      return HttpResponse.json(
        { data, total: total ?? data.length, limit: 50, offset: 0 },
        { status: 200 },
      );
    }),
  );
}

describe("Library", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Library />);
    const skeletons = document.querySelectorAll('[class*="shimmer"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders track table when API returns data", async () => {
    overrideTracks(makeTracks(3));

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Track 1").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText("Track 2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Track 3").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Artist 1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Album 1").length).toBeGreaterThan(0);
  });

  it("renders empty state when no tracks exist", async () => {
    overrideTracks([]);
    // Empty-state copy is now derived from the backend's connector list
    // (replacing the old hardcoded "Spotify or Last.fm" string).
    server.use(
      http.get("*/api/v1/connectors", () =>
        HttpResponse.json(
          [
            makeConnectorMetadata({ name: "spotify" }),
            makeConnectorMetadata({ name: "lastfm" }),
          ],
          { status: 200 },
        ),
      ),
    );

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getByText("No tracks yet")).toBeInTheDocument();
    });

    expect(
      screen.getByText(/Import your music from Spotify or Last\.fm/),
    ).toBeInTheDocument();
  });

  it("renders error state when API fails", async () => {
    server.use(
      http.get("*/api/v1/tracks", () => {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        );
      }),
    );

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load tracks")).toBeInTheDocument();
    });
  });

  it("shows pagination when total exceeds page size", async () => {
    overrideTracks(makeTracks(50), 120);

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Track 1").length).toBeGreaterThan(0);
    });

    expect(screen.getByText(/1[–-]50 of 120/)).toBeInTheDocument();
    expect(screen.getByLabelText("Go to next page")).toBeInTheDocument();
  });

  it("hides pagination when all items fit on one page", async () => {
    overrideTracks(makeTracks(3));

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Track 1").length).toBeGreaterThan(0);
    });

    expect(screen.queryByLabelText("Go to next page")).not.toBeInTheDocument();
  });

  it("displays duration formatted as m:ss", async () => {
    overrideTracks([
      {
        id: 1,
        title: "Duration Test",
        artists: [{ name: "Test" }],
        album: null,
        duration_ms: 195000, // 3:15
        isrc: null,
        connector_names: [],
        is_liked: false,
      },
    ]);

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Duration Test").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText("3:15").length).toBeGreaterThan(0);
  });

  it("shows liked heart icon for liked tracks", async () => {
    overrideTracks([
      {
        id: 1,
        title: "Liked Track",
        artists: [{ name: "A" }],
        album: null,
        duration_ms: null,
        isrc: null,
        connector_names: [],
        is_liked: true,
      },
    ]);

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Liked Track").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByLabelText("Liked").length).toBeGreaterThan(0);
  });

  it("renders search input", async () => {
    overrideTracks(makeTracks(1));

    renderWithProviders(<Library />);

    const searchInput = screen.getByLabelText("Search tracks");
    expect(searchInput).toBeInTheDocument();
  });

  it("renders filter dropdowns", async () => {
    overrideTracks(makeTracks(1));

    renderWithProviders(<Library />);

    expect(screen.getByLabelText("Filter by liked status")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by connector")).toBeInTheDocument();
  });

  it("displays track count in header", async () => {
    overrideTracks(makeTracks(5), 5);

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(
        screen.getByText("5 tracks across all services."),
      ).toBeInTheDocument();
    });
  });

  it("shows sortable column headers", async () => {
    overrideTracks(makeTracks(1));

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Track 1").length).toBeGreaterThan(0);
    });

    expect(
      screen.getByRole("button", { name: /Sort by Title/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Sort by Artist/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Sort by Duration/ }),
    ).toBeInTheDocument();
  });

  it("requests most-recently-played sort by default", async () => {
    let sortParam: string | null = null;
    server.use(
      http.get("*/api/v1/tracks", ({ request }) => {
        sortParam = new URL(request.url).searchParams.get("sort");
        return HttpResponse.json(
          { data: [], total: 0, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(sortParam).toBe("last_played_desc");
    });
  });

  it("renders play count and last-played columns with values", async () => {
    const [track] = makeTracks(1);
    overrideTracks([
      {
        ...track,
        total_plays: 47,
        last_played: new Date(Date.now() - 3 * 3_600_000).toISOString(),
      },
    ]);

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Track 1").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText("47").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3h ago").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: /Sort by Plays/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Sort by Last Played/ }),
    ).toBeInTheDocument();
  });

  it("shows an em-dash for never-played tracks", async () => {
    overrideTracks([
      { ...makeTracks(1)[0], total_plays: 0, last_played: null },
    ]);

    renderWithProviders(<Library />);

    await waitFor(() => {
      expect(screen.getAllByText("Track 1").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  // Regression: min_plays and never_played were written by two back-to-back
  // setFilter calls. The second rebuilt from the pre-navigation params, so the
  // min_plays write was discarded and the filter did nothing at all.
  it("sends the edited minimum play count to the API", async () => {
    const requests: string[] = [];
    server.use(
      http.get("*/api/v1/tracks", ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json(
          { data: [], total: 0, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    // Starting with a play filter set means the panel renders expanded.
    renderWithProviders(<Library />, {
      routerProps: { initialEntries: ["/library?min_plays=10"] },
    });

    const minInput = await screen.findByLabelText("Minimum play count");
    fireEvent.change(minInput, { target: { value: "25" } });

    await waitFor(() => {
      const last = new URL(requests[requests.length - 1]);
      expect(last.searchParams.get("min_plays")).toBe("25");
    });
  });

  it("clearing the minimum drops min_plays without stranding never_played", async () => {
    const requests: string[] = [];
    server.use(
      http.get("*/api/v1/tracks", ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json(
          { data: [], total: 0, limit: 50, offset: 0 },
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Library />, {
      routerProps: {
        initialEntries: ["/library?min_plays=10&never_played=true"],
      },
    });

    const minInput = await screen.findByLabelText("Minimum play count");
    fireEvent.change(minInput, { target: { value: "" } });

    await waitFor(() => {
      const last = new URL(requests[requests.length - 1]);
      expect(last.searchParams.get("min_plays")).toBeNull();
      expect(last.searchParams.get("never_played")).toBeNull();
    });
  });
});
