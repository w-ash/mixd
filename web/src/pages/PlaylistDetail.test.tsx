import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";

import type {
  PlaylistDetailSchema,
  PlaylistEntrySchema,
} from "@/api/generated/model";
import { makePlaylistDetail, makePlaylistEntries } from "@/test/factories";
import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { PlaylistDetail } from "./PlaylistDetail";

function setupHandlers(
  playlist: PlaylistDetailSchema,
  entries: PlaylistEntrySchema[],
) {
  server.use(
    http.get("*/api/v1/playlists/:playlistId", () => {
      return HttpResponse.json(playlist, { status: 200 });
    }),
    http.get("*/api/v1/playlists/:playlistId/tracks", () => {
      return HttpResponse.json(
        {
          data: entries,
          total: entries.length,
          limit: 50,
          offset: 0,
        },
        { status: 200 },
      );
    }),
  );
}

function renderPlaylistDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="playlists/:id" element={<PlaylistDetail />} />
    </Routes>,
    { routerProps: { initialEntries: ["/playlists/1"] } },
  );
}

describe("PlaylistDetail", () => {
  it("renders summary stats with track count, duration, and last updated", async () => {
    const entries = makePlaylistEntries([
      {
        title: "Song A",
        artist: "Artist 1",
        duration_ms: 180_000,
        added_at: "2026-01-10T08:00:00Z",
      },
      {
        title: "Song B",
        artist: "Artist 2",
        duration_ms: 240_000,
        added_at: "2026-01-12T10:00:00Z",
      },
      {
        title: "Song C",
        artist: "Artist 3",
        duration_ms: 300_000,
        added_at: "2026-01-14T14:00:00Z",
      },
    ]);
    const playlist = makePlaylistDetail({ track_count: 3 });
    setupHandlers(playlist, entries);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getByText("3 tracks")).toBeInTheDocument();
    });

    // Total duration: 180000 + 240000 + 300000 = 720000ms = 12 min
    expect(screen.getByText("12 min")).toBeInTheDocument();
    expect(screen.getByText("Updated Jan 15, 2026")).toBeInTheDocument();
  });

  it("renders total duration in hours and minutes for long playlists", async () => {
    const entries = makePlaylistEntries([
      {
        title: "Long Song",
        artist: "Artist",
        duration_ms: 5_580_000,
      },
    ]);
    const playlist = makePlaylistDetail({ track_count: 1, updated_at: null });
    setupHandlers(playlist, entries);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getByText("1 track")).toBeInTheDocument();
    });

    // 5580000ms = 93 min = 1 hr 33 min
    expect(screen.getByText("1 hr 33 min")).toBeInTheDocument();
    // No "Updated" stat when updated_at is null
    expect(screen.queryByText(/Updated/)).not.toBeInTheDocument();
  });

  it("renders date added column in track table", async () => {
    const entries = makePlaylistEntries([
      {
        title: "Midnight City",
        artist: "M83",
        album: "Hurry Up, We're Dreaming",
        duration_ms: 244_000,
        added_at: "2026-02-20T08:30:00Z",
      },
    ]);
    setupHandlers(makePlaylistDetail(), entries);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getByText("Midnight City")).toBeInTheDocument();
    });

    // Table header
    expect(screen.getByText("Added")).toBeInTheDocument();
    // Formatted date in cell
    expect(screen.getByText("Feb 20, 2026")).toBeInTheDocument();
  });

  it("shows em-dash for null added_at values", async () => {
    const entries = makePlaylistEntries([
      {
        title: "Old Import",
        artist: "Unknown",
        duration_ms: 200_000,
        added_at: null,
      },
    ]);
    setupHandlers(makePlaylistDetail(), entries);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getByText("Old Import")).toBeInTheDocument();
    });

    // The "Added" column should contain an em-dash for null added_at
    // There are multiple em-dashes (album column also uses them), so just
    // confirm the row rendered and the added column header exists
    expect(screen.getByText("Added")).toBeInTheDocument();
    const emDashes = screen.getAllByText("\u2014");
    // At least 2: one for null album, one for null added_at
    expect(emDashes.length).toBeGreaterThanOrEqual(2);
  });

  it("handles null duration_ms in total duration computation", async () => {
    const entries = makePlaylistEntries([
      {
        title: "Song With Duration",
        artist: "A",
        duration_ms: 120_000,
        added_at: "2026-01-01T00:00:00Z",
      },
      {
        title: "Song Without Duration",
        artist: "B",
        duration_ms: null,
        added_at: "2026-01-02T00:00:00Z",
      },
    ]);
    setupHandlers(makePlaylistDetail({ track_count: 2 }), entries);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getByText("2 tracks")).toBeInTheDocument();
    });

    // Only 120000ms counted (null skipped) = 2 min
    expect(screen.getByText("2 min")).toBeInTheDocument();
  });

  it("renders empty state when playlist has no tracks", async () => {
    setupHandlers(makePlaylistDetail({ track_count: 0 }), []);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getByText("This playlist is empty")).toBeInTheDocument();
    });

    // No duration stat when there are no entries
    expect(screen.queryByText(/min/)).not.toBeInTheDocument();
    expect(screen.queryByText(/hr/)).not.toBeInTheDocument();
  });
});
