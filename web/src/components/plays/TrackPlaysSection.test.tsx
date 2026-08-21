import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { TrackPlaysSection } from "./TrackPlaysSection";

const TRACK_ID = "0198c0de-0000-7000-8000-000000000001";

function overridePlays(events: unknown[]) {
  server.use(
    http.get("*/api/v1/plays", () =>
      HttpResponse.json(
        { data: events, limit: 10, next_cursor: null },
        { status: 200 },
      ),
    ),
  );
}

function overrideHistogram(bins: { bucket_start: string; count: number }[]) {
  server.use(
    http.get("*/api/v1/plays/histogram", () =>
      HttpResponse.json({ bins, bucket: "week" }, { status: 200 }),
    ),
  );
}

function makeEvent(overrides: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(),
    track_id: TRACK_ID,
    title: "Track",
    artists: "Artist",
    played_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    ms_played: null,
    service: "spotify",
    source_services: ["spotify"],
    ...overrides,
  };
}

describe("TrackPlaysSection", () => {
  it("renders recent plays and the view-all link", async () => {
    overridePlays([makeEvent()]);
    overrideHistogram([]);

    renderWithProviders(
      <TrackPlaysSection trackId={TRACK_ID} totalPlays={3} />,
    );

    await waitFor(() => {
      expect(screen.getByText("3h ago")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("link", { name: /View all plays/ }),
    ).toHaveAttribute("href", `/library/plays?track_id=${TRACK_ID}`);
  });

  it("skips the chart below the play threshold", async () => {
    overridePlays([makeEvent()]);

    renderWithProviders(
      <TrackPlaysSection trackId={TRACK_ID} totalPlays={3} />,
    );

    await waitFor(() => {
      expect(screen.getByText("3h ago")).toBeInTheDocument();
    });
    // The histogram query is disabled — no chart region rendered.
    expect(screen.queryByTestId("plays-bar-chart")).toBeNull();
  });

  it("renders the chart at or above the threshold", async () => {
    overridePlays([makeEvent()]);
    overrideHistogram([
      { bucket_start: "2026-08-03T00:00:00Z", count: 4 },
      { bucket_start: "2026-08-10T00:00:00Z", count: 2 },
    ]);

    renderWithProviders(
      <TrackPlaysSection trackId={TRACK_ID} totalPlays={6} />,
    );

    await waitFor(() => {
      expect(screen.getByText("3h ago")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("plays-bar-chart")).toBeInTheDocument();
    });
  });
});
