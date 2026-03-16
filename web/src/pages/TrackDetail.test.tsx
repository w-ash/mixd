import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { TrackDetail } from "./TrackDetail";

const mockTrack = {
  id: 42,
  title: "Paranoid Android",
  artists: [{ name: "Radiohead" }],
  album: "OK Computer",
  duration_ms: 386000,
  release_date: "1997-06-16",
  isrc: "GBAYE9700100",
  connector_mappings: [
    {
      mapping_id: 10,
      connector_name: "spotify",
      connector_track_id: "sp-123",
      match_method: "direct_import",
      confidence: 100,
      origin: "automatic",
      is_primary: true,
      connector_track_title: "Paranoid Android",
      connector_track_artists: ["Radiohead"],
    },
    {
      mapping_id: 20,
      connector_name: "lastfm",
      connector_track_id: "lf-456",
      match_method: "artist_title",
      confidence: 85,
      origin: "manual_override",
      is_primary: true,
      connector_track_title: "Paranoid Android - Remastered",
      connector_track_artists: ["Radiohead"],
    },
  ],
  like_status: {
    spotify: { is_liked: true, liked_at: "2025-03-15T10:30:00Z" },
    lastfm: { is_liked: false, liked_at: null },
  },
  play_summary: {
    total_plays: 127,
    first_played: "2024-01-10T14:00:00Z",
    last_played: "2026-02-28T20:15:00Z",
  },
  playlists: [
    { id: 1, name: "Chill Vibes", description: "Relaxing tracks" },
    { id: 2, name: "Rock Classics", description: null },
  ],
};

function overrideTrackDetail(data: Record<string, unknown>, status = 200) {
  server.use(
    http.get("*/api/v1/tracks/:trackId", () => {
      return HttpResponse.json(data, { status });
    }),
  );
}

function renderTrackDetail(trackId = 42) {
  return renderWithProviders(
    <Routes>
      <Route path="library/:id" element={<TrackDetail />} />
    </Routes>,
    { routerProps: { initialEntries: [`/library/${trackId}`] } },
  );
}

describe("TrackDetail", () => {
  it("renders loading skeleton initially", () => {
    renderTrackDetail();
    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("displays track metadata when loaded", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getAllByText("Radiohead").length).toBeGreaterThan(0);
    expect(screen.getByText("OK Computer")).toBeInTheDocument();
    expect(screen.getByText("6:26")).toBeInTheDocument();
    expect(screen.getByText(/1997/)).toBeInTheDocument();
    expect(screen.getByText("GBAYE9700100")).toBeInTheDocument();
  });

  it("displays connector mappings with provenance badges", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    // Match method badges
    expect(screen.getByText("Direct")).toBeInTheDocument();
    expect(screen.getByText("Artist/Title")).toBeInTheDocument();

    // Confidence badges
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();

    // Manual override badge
    expect(screen.getByText("Manual")).toBeInTheDocument();
  });

  it("shows title mismatch warning when connector title differs", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    // The title mismatch warning text
    expect(screen.getByText(/Service title differs/)).toBeInTheDocument();
  });

  it("displays like status per service", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getByText("spotify")).toBeInTheDocument();
    expect(screen.getByText("lastfm")).toBeInTheDocument();
    expect(screen.getByText("Liked")).toBeInTheDocument();
    expect(screen.getByText("Not liked")).toBeInTheDocument();
  });

  it("displays play summary", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getByText("127")).toBeInTheDocument();
  });

  it("displays playlists containing the track", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getByText("Chill Vibes")).toBeInTheDocument();
    expect(screen.getByText("Rock Classics")).toBeInTheDocument();
    expect(screen.getByText("Relaxing tracks")).toBeInTheDocument();
  });

  it("shows back link to library", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    const backLink = screen.getByRole("link", { name: /Library/ });
    expect(backLink).toBeInTheDocument();
    expect(backLink).toHaveAttribute("href", "/library");
  });

  it("shows merge button", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getByText("Merge with...")).toBeInTheDocument();
  });

  it("shows error state for nonexistent track", async () => {
    overrideTrackDetail(
      { error: { code: "NOT_FOUND", message: "Not found" } },
      404,
    );

    renderTrackDetail(99999);

    await waitFor(() => {
      expect(screen.getByText("Track not found")).toBeInTheDocument();
    });
  });

  it("shows empty play history when no plays", async () => {
    overrideTrackDetail({
      ...mockTrack,
      play_summary: { total_plays: 0, first_played: null, last_played: null },
    });

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getByText("No play history recorded.")).toBeInTheDocument();
  });

  it("shows empty playlists state", async () => {
    overrideTrackDetail({ ...mockTrack, playlists: [] });

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getByText("Not in any playlists.")).toBeInTheDocument();
  });

  it("shows text labels on mapping action buttons", async () => {
    overrideTrackDetail(mockTrack);

    renderTrackDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Paranoid Android",
      );
    });

    expect(screen.getAllByText("Relink").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unlink").length).toBeGreaterThan(0);
  });
});
