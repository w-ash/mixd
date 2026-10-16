import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { TrackDetailSchema } from "#/api/generated/model";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { MergeTrackDialog } from "./MergeTrackDialog";

const mockWinner: TrackDetailSchema = {
  id: "019d0000-0000-7000-8000-000000000001",
  title: "Test Track",
  artists: [{ name: "Artist One" }, { name: "Artist Two" }],
  album: "Test Album",
  connector_mappings: [
    {
      mapping_id: "019d0000-0000-7000-8000-000000000010",
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
  it("opens dialog on trigger click", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MergeTrackDialog winner={mockWinner} />);

    await user.click(screen.getByRole("button", { name: /merge with/i }));

    expect(screen.getByText("Merge Duplicate Track")).toBeInTheDocument();
    expect(
      screen.getByText(/play counts, service connections/i),
    ).toBeInTheDocument();
  });

  it("merges the picked duplicate into this track on confirm", async () => {
    const LOSER_ID = "019d0000-0000-7000-8000-000000000002";
    server.use(
      http.get("*/api/v1/tracks", () =>
        HttpResponse.json(
          {
            data: [
              {
                id: LOSER_ID,
                title: "Test Track (dupe)",
                artists: [{ name: "Artist One" }],
                album: null,
                duration_ms: null,
                isrc: null,
                connector_names: ["lastfm"],
                is_liked: false,
              },
            ],
            total: 1,
            limit: 10,
            offset: 0,
          },
          { status: 200 },
        ),
      ),
    );
    const merges: Array<{ winnerId: string; body: unknown }> = [];
    server.use(
      http.post(
        "*/api/v1/tracks/:trackId/merge",
        async ({ params, request }) => {
          merges.push({
            winnerId: String(params.trackId),
            body: await request.json(),
          });
          return HttpResponse.json(mockWinner, { status: 200 });
        },
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<MergeTrackDialog winner={mockWinner} />);

    await user.click(screen.getByRole("button", { name: /merge with/i }));
    expect(screen.getByText(/search by title/i)).toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("Search for the duplicate..."),
      "test",
    );
    await user.click(await screen.findByText("Test Track (dupe)"));

    // Comparison step: the irreversible warning replaces the search tip.
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
    expect(screen.queryByText(/search by title/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm Merge" }));

    await waitFor(() =>
      expect(
        screen.queryByText("Merge Duplicate Track"),
      ).not.toBeInTheDocument(),
    );
    expect(merges).toEqual([
      { winnerId: mockWinner.id, body: { loser_id: LOSER_ID } },
    ]);
  });
});
