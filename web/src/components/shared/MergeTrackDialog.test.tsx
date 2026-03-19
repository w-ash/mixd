import { describe, expect, it } from "vitest";

import type { TrackDetailSchema } from "@/api/generated/model";
import { renderWithProviders, screen, userEvent } from "@/test/test-utils";

import { MergeTrackDialog } from "./MergeTrackDialog";

const mockWinner: TrackDetailSchema = {
  id: 1,
  title: "Test Track",
  artists: [{ name: "Artist One" }, { name: "Artist Two" }],
  album: "Test Album",
  connector_mappings: [
    {
      mapping_id: 10,
      connector_name: "spotify",
      connector_track_id: "sp-1",
      is_primary: true,
      match_method: "isrc",
      confidence: 95,
      origin: "auto",
      connector_track_title: "Test Track",
      connector_track_artists: ["Artist One"],
    },
  ],
  like_status: {},
  play_summary: { total_plays: 0 },
  playlists: [],
  isrc: null,
  duration_ms: null,
};

describe("MergeTrackDialog", () => {
  it("renders the trigger button", () => {
    renderWithProviders(<MergeTrackDialog winner={mockWinner} />);
    expect(
      screen.getByRole("button", { name: /merge with/i }),
    ).toBeInTheDocument();
  });

  it("opens dialog on trigger click", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MergeTrackDialog winner={mockWinner} />);

    await user.click(screen.getByRole("button", { name: /merge with/i }));

    expect(screen.getByText("Merge Duplicate Track")).toBeInTheDocument();
    expect(
      screen.getByText(/play counts, service connections/i),
    ).toBeInTheDocument();
  });

  it("shows search tip in dialog footer", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MergeTrackDialog winner={mockWinner} />);

    await user.click(screen.getByRole("button", { name: /merge with/i }));

    expect(screen.getByText(/search by title/i)).toBeInTheDocument();
  });
});
