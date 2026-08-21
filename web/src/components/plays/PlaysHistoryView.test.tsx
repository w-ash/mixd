import { fireEvent } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { PlaysHistoryView } from "./PlaysHistoryView";

const localIso = (day: number, hour: number, minute = 0) =>
  new Date(2026, 7, day, hour, minute).toISOString();

function makeEvent(id: string, trackId: string, playedAt: string) {
  return {
    id,
    track_id: trackId,
    title: `Song ${trackId}`,
    artists: `Artist ${trackId}`,
    played_at: playedAt,
    ms_played: 180_000,
    service: "spotify",
    source_services: ["spotify"],
  };
}

function overridePlays(events: unknown[]) {
  server.use(
    http.get("*/api/v1/plays", () =>
      HttpResponse.json(
        { data: events, limit: 100, next_cursor: null },
        { status: 200 },
      ),
    ),
    http.get("*/api/v1/plays/histogram", () =>
      HttpResponse.json(
        {
          bins: [
            { bucket_start: "2026-08-01T00:00:00Z", count: events.length },
          ],
          bucket: "day",
        },
        { status: 200 },
      ),
    ),
  );
}

describe("PlaysHistoryView", () => {
  it("groups the feed under day headers", async () => {
    const now = new Date();
    const today = new Date(now);
    today.setHours(9, 0, 0, 0);
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    yesterday.setHours(9, 0, 0, 0);
    overridePlays([
      makeEvent("e1", "a", today.toISOString()),
      makeEvent("e2", "b", yesterday.toISOString()),
    ]);

    renderWithProviders(<PlaysHistoryView initialItemCount={2} />);

    await waitFor(() => {
      expect(screen.getByText("Today")).toBeInTheDocument();
    });
    expect(screen.getByText("Yesterday")).toBeInTheDocument();
    expect(screen.getByText("Song a")).toBeInTheDocument();
    expect(screen.getByText("Song b")).toBeInTheDocument();
  });

  it("collapses consecutive plays of one track into a ×N row", async () => {
    overridePlays([
      makeEvent("e1", "a", localIso(10, 15, 30)),
      makeEvent("e2", "a", localIso(10, 15, 20)),
      makeEvent("e3", "a", localIso(10, 15, 10)),
    ]);

    renderWithProviders(<PlaysHistoryView initialItemCount={1} />);

    await waitFor(() => {
      expect(screen.getByText("×3")).toBeInTheDocument();
    });
    // One collapsed row, not three.
    expect(screen.getAllByText("Song a")).toHaveLength(1);
  });

  it("shows the empty state when there are no plays", async () => {
    overridePlays([]);

    renderWithProviders(<PlaysHistoryView />);

    await waitFor(() => {
      expect(screen.getByText("No plays yet")).toBeInTheDocument();
    });
  });

  // Regression: `from` and `to` were written by two back-to-back setFilter
  // calls, so only `to` survived — the feed stayed filtered and the chip
  // (which needs both) vanished, leaving no way to undo it.
  it("clears both range bounds when the chip is dismissed", async () => {
    const requests: string[] = [];
    server.use(
      http.get("*/api/v1/plays", ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json(
          { data: [], limit: 100, next_cursor: null },
          { status: 200 },
        );
      }),
      http.get("*/api/v1/plays/histogram", () =>
        HttpResponse.json({ bins: [], bucket: "day" }, { status: 200 }),
      ),
    );

    renderWithProviders(<PlaysHistoryView />, {
      routerProps: {
        initialEntries: [
          "/library/plays?from=2026-08-01T00:00:00.000Z&to=2026-08-02T00:00:00.000Z",
        ],
      },
    });

    await waitFor(() => {
      const first = new URL(requests[0]);
      expect(first.searchParams.get("since")).toBe("2026-08-01T00:00:00.000Z");
      expect(first.searchParams.get("until")).toBe("2026-08-02T00:00:00.000Z");
    });

    fireEvent.click(screen.getByRole("button", { name: "Clear date range" }));

    await waitFor(() => {
      const last = new URL(requests[requests.length - 1]);
      expect(last.searchParams.get("since")).toBeNull();
      expect(last.searchParams.get("until")).toBeNull();
    });
    expect(
      screen.queryByRole("button", { name: "Clear date range" }),
    ).toBeNull();
  });

  it("a bar click narrows the feed to that bin, both bounds applied", async () => {
    const requests: string[] = [];
    server.use(
      http.get("*/api/v1/plays", ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json(
          { data: [], limit: 100, next_cursor: null },
          { status: 200 },
        );
      }),
      http.get("*/api/v1/plays/histogram", () =>
        HttpResponse.json(
          {
            bins: [{ bucket_start: "2026-08-02T00:00:00Z", count: 3 }],
            bucket: "day",
          },
          { status: 200 },
        ),
      ),
    );

    const { container } = renderWithProviders(
      <PlaysHistoryView chartWidth={400} />,
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBe(
        1,
      );
    });
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle")[0]);

    await waitFor(() => {
      const last = new URL(requests[requests.length - 1]);
      expect(last.searchParams.get("since")).toBe("2026-08-02T00:00:00.000Z");
      expect(last.searchParams.get("until")).toBe("2026-08-03T00:00:00.000Z");
    });
    expect(
      screen.getByRole("button", { name: "Clear date range" }),
    ).toBeTruthy();
  });
});
