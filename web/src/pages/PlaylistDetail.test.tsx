import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";

import type {
  PlaylistDetailSchema,
  PlaylistEntrySchema,
} from "#/api/generated/model";
import {
  makeConnectorMetadata,
  makeConnectorPlaylistBrowse,
  makePlaylistDetail,
  makePlaylistEntries,
} from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

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
    // Deterministic empty links — avoids the faker default rendering an
    // UnmatchedBadge tooltip (which would need a TooltipProvider the test
    // harness doesn't mount).
    http.get("*/api/v1/playlists/:playlistId/links", () => {
      return HttpResponse.json([], { status: 200 });
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
      expect(screen.getAllByText("Midnight City").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Added")).toBeInTheDocument();
    expect(screen.getAllByText(/Feb 20, 2026/).length).toBeGreaterThan(0);
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
      expect(screen.getAllByText("Old Import").length).toBeGreaterThan(0);
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

  it("renders back link to playlists list", async () => {
    setupHandlers(makePlaylistDetail(), []);

    renderPlaylistDetail();

    await waitFor(() => {
      const backLink = screen.getByRole("link", { name: /Playlists/ });
      expect(backLink).toHaveAttribute("href", "/playlists");
    });
  });

  it("renders track titles as links to library detail", async () => {
    const entries = makePlaylistEntries([
      {
        title: "Midnight City",
        artist: "M83",
        duration_ms: 244_000,
        added_at: "2026-02-20T08:30:00Z",
      },
    ]);
    setupHandlers(makePlaylistDetail(), entries);

    renderPlaylistDetail();

    await waitFor(() => {
      expect(screen.getAllByText("Midnight City").length).toBeGreaterThan(0);
    });

    const trackLinks = screen.getAllByRole("link", { name: "Midnight City" });
    expect(trackLinks.length).toBeGreaterThan(0);
    for (const link of trackLinks) {
      expect(link).toHaveAttribute(
        "href",
        expect.stringContaining("/library/"),
      );
    }
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

describe("PlaylistDetail — unresolved tracks + repair", () => {
  function entriesWithUnresolved(): PlaylistEntrySchema[] {
    return [
      {
        id: "019d0001-0000-7000-8000-000000000101",
        position: 1,
        track: {
          id: "019d0000-0000-7000-8000-000000000101",
          title: "Resolved Song",
          artists: [{ name: "Artist A" }],
          album: null,
          duration_ms: 180_000,
        },
        added_at: null,
        is_resolved: true,
      },
      {
        id: "019d0001-0000-7000-8000-000000000102",
        position: 2,
        track: {
          id: "019d0000-0000-7000-8000-000000000102",
          title: "Mystery Song",
          artists: [{ name: "Artist B" }],
          album: null,
          duration_ms: null,
        },
        added_at: null,
        is_resolved: false,
      },
    ];
  }

  it("surfaces unresolved entries with a roll-up repair badge", async () => {
    setupHandlers(
      makePlaylistDetail({ track_count: 2 }),
      entriesWithUnresolved(),
    );
    renderPlaylistDetail();

    expect(
      await screen.findByRole("button", { name: /Repair unresolved \(1\)/ }),
    ).toBeInTheDocument();
    // The unmatched entry is first-class (visible) with an explicit status.
    // ResponsiveTable renders both a card and a table copy, so allow duplicates.
    expect(screen.getAllByText("Mystery Song").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unresolved").length).toBeGreaterThan(0);
  });

  it("hides the repair badge when every entry is resolved", async () => {
    const entries = entriesWithUnresolved().map((e) => ({
      ...e,
      is_resolved: true,
    }));
    setupHandlers(makePlaylistDetail({ track_count: 2 }), entries);
    renderPlaylistDetail();

    await screen.findAllByText("Resolved Song");
    expect(
      screen.queryByRole("button", { name: /Repair unresolved/ }),
    ).not.toBeInTheDocument();
  });

  it("posts to the repair endpoint when the badge is clicked", async () => {
    let repairCalled = false;
    setupHandlers(
      makePlaylistDetail({ track_count: 2 }),
      entriesWithUnresolved(),
    );
    server.use(
      http.post("*/api/v1/playlists/:playlistId/repair", () => {
        repairCalled = true;
        return HttpResponse.json(
          { repaired: 1, still_unresolved: 0 },
          { status: 200 },
        );
      }),
    );
    renderPlaylistDetail();

    const btn = await screen.findByRole("button", {
      name: /Repair unresolved \(1\)/,
    });
    await userEvent.click(btn);

    await waitFor(() => expect(repairCalled).toBe(true));
  });
});

describe("PlaylistDetail — linked services row", () => {
  function makeLink(overrides: Record<string, unknown> = {}) {
    return {
      id: "lnk_1",
      connector_name: "spotify",
      connector_playlist_identifier: "spfy_123",
      connector_playlist_name: "Roadtrip Mix",
      sync_direction: "pull",
      direction_label: "Spotify → Mixd (replaces Mixd)",
      sync_status: "never_synced",
      last_synced: null,
      last_sync_error: null,
      last_sync_tracks_added: null,
      last_sync_tracks_removed: null,
      // 0 keeps the UnmatchedBadge (and its tooltip) out of the harness.
      last_sync_tracks_unmatched: 0,
      ...overrides,
    };
  }

  function renderWithLink(overrides: Record<string, unknown> = {}) {
    setupHandlers(makePlaylistDetail(), []);
    server.use(
      http.get("*/api/v1/playlists/:playlistId/links", () =>
        HttpResponse.json([makeLink(overrides)], { status: 200 }),
      ),
    );
    return renderPlaylistDetail();
  }

  it("renders a single direction-free 'Sync' action (direction lives in the chip)", async () => {
    renderWithLink({ sync_direction: "pull" });

    // The action button is just the verb; no "Sync to/from {connector}".
    expect(
      await screen.findByRole("button", { name: "Sync" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Sync (to|from)/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps the direction toggle visible at rest", async () => {
    renderWithLink({ sync_direction: "pull" });

    // Always-visible (muted) toggle — not hover-gated.
    expect(await screen.findByTitle(/Click to switch to push/)).toBeVisible();
  });

  it("renders 'Never synced' as muted text, not an icon+color status", async () => {
    renderWithLink({ sync_status: "never_synced" });

    const label = await screen.findByText("Never synced");
    // A plain muted span — not the StatusIndicator (whose label span sits beside
    // an icon and carries no color class of its own).
    expect(label.tagName).toBe("SPAN");
    expect(label.className).toContain("text-text-muted");
    expect(label.querySelector("svg")).toBeNull();
  });

  it("still renders icon+color+text for a meaningful (error) status", async () => {
    renderWithLink({
      sync_status: "error",
      last_sync_error: "Auth token expired.",
    });

    const label = await screen.findByText("Sync failed");
    // StatusIndicator: the label sits next to an icon svg in the same wrapper.
    expect(label.parentElement?.querySelector("svg")).not.toBeNull();
  });
});

describe("PlaylistDetail — browse-to-link", () => {
  it("browses the connector, picks a playlist, and links it without typing an ID", async () => {
    setupHandlers(makePlaylistDetail(), []);
    let linkBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/v1/connectors", () =>
        HttpResponse.json(
          [makeConnectorMetadata({ name: "spotify", connected: true })],
          { status: 200 },
        ),
      ),
      http.get("*/api/v1/connectors/spotify/playlists", () =>
        HttpResponse.json({
          data: [
            makeConnectorPlaylistBrowse({
              connector_playlist_identifier: "sp_pick_1",
              name: "Roadtrip Mix",
              track_count: 42,
            }),
          ],
          from_cache: true,
          fetched_at: new Date().toISOString(),
        }),
      ),
      http.post("*/api/v1/playlists/:playlistId/links", async ({ request }) => {
        linkBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            id: "lnk_new",
            connector_name: "spotify",
            connector_playlist_identifier: "sp_pick_1",
            connector_playlist_name: "Roadtrip Mix",
            sync_direction: "push",
            direction_label: "Mixd → Spotify (replaces Spotify)",
            sync_status: "never_synced",
            last_synced: null,
            last_sync_error: null,
            last_sync_tracks_added: null,
            last_sync_tracks_removed: null,
            last_sync_tracks_unmatched: 0,
          },
          { status: 201 },
        );
      }),
    );

    renderPlaylistDetail();

    // Open the link dialog, then browse instead of pasting an ID.
    await userEvent.click(
      await screen.findByRole("button", { name: "Link Playlist" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Browse .* playlists/ }),
    );

    // Pick a playlist from the browse list; the choice surfaces on the button.
    await userEvent.click(await screen.findByText("Roadtrip Mix"));
    expect(
      await screen.findByRole("button", { name: /Selected: Roadtrip Mix/ }),
    ).toBeInTheDocument();

    // Submit — the link is created with the browsed identifier, no typing.
    await userEvent.click(screen.getByRole("button", { name: "Link" }));

    await waitFor(() => expect(linkBody).not.toBeNull());
    expect(linkBody).toMatchObject({
      connector: "spotify",
      connector_playlist_identifier: "sp_pick_1",
    });
  });
});
