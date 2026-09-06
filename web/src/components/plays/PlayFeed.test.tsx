import { describe, expect, it } from "vitest";

import type { PlayDayGroup } from "#/lib/play-feed";
import { renderWithProviders, screen } from "#/test/test-utils";

import { PlayFeed } from "./PlayFeed";

const groups: PlayDayGroup[] = [
  {
    dayKey: "2026-08-02",
    label: "Today",
    rows: [
      {
        key: "e1",
        trackId: "t1",
        title: "Song A",
        artists: "Artist A",
        service: "spotify",
        sourceServices: ["spotify"],
        count: 3,
        newestAt: "2026-08-02T10:00:00Z",
        oldestAt: "2026-08-02T09:00:00Z",
      },
    ],
  },
  {
    dayKey: "2026-08-01",
    label: "Yesterday",
    rows: [
      {
        key: "e2",
        trackId: "t2",
        title: "Song B",
        artists: "Artist B",
        service: "lastfm",
        sourceServices: ["lastfm"],
        count: 1,
        newestAt: "2026-08-01T09:00:00Z",
        oldestAt: "2026-08-01T09:00:00Z",
      },
    ],
  },
];

describe("PlayFeed", () => {
  it("renders day headers and rows, collapsing runs into a ×N badge", () => {
    renderWithProviders(<PlayFeed groups={groups} initialItemCount={2} />);

    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("Yesterday")).toBeInTheDocument();
    expect(screen.getByText("Song A")).toBeInTheDocument();
    expect(screen.getByText("Song B")).toBeInTheDocument();
    expect(screen.getByText("×3")).toBeInTheDocument();
  });

  it("renders an empty list without crashing when there are no groups", () => {
    renderWithProviders(<PlayFeed groups={[]} initialItemCount={0} />);

    expect(screen.getByTestId("play-feed")).toBeInTheDocument();
    expect(screen.queryByText("Today")).not.toBeInTheDocument();
  });
});
