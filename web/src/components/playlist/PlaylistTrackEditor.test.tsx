/**
 * Component tests for the manual playlist track-editing surface (v0.8.11).
 *
 * Driven through the full PlaylistDetail page so the tracks query cache (which
 * the editor mutates optimistically) actually feeds the rendered list. A real
 * <Toaster /> is mounted so the deferred-commit "Undo" action is clickable.
 *
 * Drag reorder isn't simulated here — dnd-kit pointer/keyboard drag is
 * impractical to drive in jsdom; the reorder use case is covered by
 * tests/integration/test_manual_playlist_editing.py, and the drag UI by the
 * visual-audit / manual pass.
 */
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { PlaylistTrackEditor } from "#/components/playlist/PlaylistTrackEditor";
import { Toaster } from "#/components/ui/sonner";
import { PlaylistDetail } from "#/pages/PlaylistDetail";
import { makePlaylistDetail, makePlaylistEntries } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

const PLAYLIST_ID = "019d0000-0000-7000-8000-000000000001";

function entries() {
  return makePlaylistEntries([
    { title: "Track A", artist: "Artist A" },
    { title: "Track B", artist: "Artist B" },
    { title: "Track C", artist: "Artist C" },
  ]);
}

function installBaseHandlers(rows: ReturnType<typeof entries>) {
  const detail = makePlaylistDetail({
    id: PLAYLIST_ID,
    track_count: rows.length,
    entries: [],
  });
  server.use(
    http.get("*/api/v1/playlists/:id", () =>
      HttpResponse.json(detail, { status: 200 }),
    ),
    http.get("*/api/v1/playlists/:id/tracks", () =>
      HttpResponse.json(
        { data: rows, total: rows.length, limit: 50, offset: 0 },
        { status: 200 },
      ),
    ),
    http.get("*/api/v1/playlists/:id/links", () =>
      HttpResponse.json([], { status: 200 }),
    ),
  );
  return detail;
}

function renderPage() {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="playlists/:id" element={<PlaylistDetail />} />
      </Routes>
      <Toaster />
    </>,
    { routerProps: { initialEntries: [`/playlists/${PLAYLIST_ID}`] } },
  );
}

describe("PlaylistTrackEditor — remove + Undo (deferred commit)", () => {
  beforeEach(() => installBaseHandlers(entries()));

  it("undo restores the row and never sends a DELETE", async () => {
    let deleteCount = 0;
    server.use(
      http.delete("*/api/v1/playlists/:id/tracks", () => {
        deleteCount += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Track A");
    await user.click(screen.getByRole("button", { name: /remove track a/i }));

    // Optimistically gone, with an Undo affordance.
    await waitFor(() =>
      expect(screen.queryByText("Track A")).not.toBeInTheDocument(),
    );
    await user.click(await screen.findByRole("button", { name: /undo/i }));

    // Row comes back; nothing reached the server, so identity is preserved.
    await screen.findByText("Track A");
    expect(deleteCount).toBe(0);
  });

  it("commits the pending removal (one DELETE) when another edit preempts it", async () => {
    const deleteBodies: Array<{ entry_ids: string[] }> = [];
    server.use(
      http.delete("*/api/v1/playlists/:id/tracks", async ({ request }) => {
        deleteBodies.push((await request.json()) as { entry_ids: string[] });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const rows = entries();
    installBaseHandlers(rows);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Track A");
    await user.click(screen.getByRole("button", { name: /remove track a/i }));
    // Deferred: no DELETE yet.
    expect(deleteBodies).toHaveLength(0);

    // A second removal flushes the first one immediately.
    await user.click(screen.getByRole("button", { name: /remove track b/i }));
    await waitFor(() => expect(deleteBodies).toHaveLength(1));
    expect(deleteBodies[0].entry_ids).toEqual([rows[0].id]);
  });
});

describe("AddTracksDialog — multi-select add", () => {
  beforeEach(() => installBaseHandlers(entries()));

  it("adds all selected tracks in one POST", async () => {
    const found = [
      {
        id: "019d0000-0000-7000-8000-000000000900",
        title: "Song One",
        artists: [{ name: "New Artist" }],
        album: null,
        duration_ms: 200_000,
        connector_names: [],
      },
      {
        id: "019d0000-0000-7000-8000-000000000901",
        title: "Song Two",
        artists: [{ name: "New Artist" }],
        album: null,
        duration_ms: 210_000,
        connector_names: [],
      },
    ];
    const addBodies: Array<{ track_ids: string[] }> = [];
    server.use(
      http.get("*/api/v1/tracks", () =>
        HttpResponse.json(
          { data: found, total: found.length, limit: 20, offset: 0 },
          { status: 200 },
        ),
      ),
      http.post("*/api/v1/playlists/:id/tracks", async ({ request }) => {
        addBodies.push((await request.json()) as { track_ids: string[] });
        return HttpResponse.json(makePlaylistDetail({ id: PLAYLIST_ID }), {
          status: 200,
        });
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole("button", { name: /add tracks/i }),
    );
    await user.type(
      await screen.findByPlaceholderText(/search your library/i),
      "song",
    );
    await user.click(await screen.findByText("Song One"));
    await user.click(await screen.findByText("Song Two"));
    await user.click(screen.getByRole("button", { name: /add 2 tracks/i }));

    await waitFor(() => expect(addBodies).toHaveLength(1));
    expect(addBodies[0].track_ids).toEqual([found[0].id, found[1].id]);
  });
});

describe("PlaylistTrackEditor — selection reconciles with the live entry set", () => {
  it("prunes a selected entry that disappears from the list", async () => {
    const rows = entries(); // Track A, B, C
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(
      <PlaylistTrackEditor playlistId={PLAYLIST_ID} entries={rows} />,
    );

    await user.click(
      screen.getByRole("checkbox", { name: /select all tracks/i }),
    );
    expect(screen.getByText("3 selected")).toBeInTheDocument();

    // A background refetch drops Track A. The selection must follow the live
    // set — a stale id would inflate the count and poison the batch DELETE.
    rerender(
      <PlaylistTrackEditor playlistId={PLAYLIST_ID} entries={rows.slice(1)} />,
    );

    expect(await screen.findByText("2 selected")).toBeInTheDocument();
  });
});

describe("PlaylistTrackEditor — pending removal commits on unmount", () => {
  beforeEach(() => installBaseHandlers(entries()));

  it("flushes the deferred DELETE when the editor unmounts", async () => {
    const deleteBodies: Array<{ entry_ids: string[] }> = [];
    server.use(
      http.delete("*/api/v1/playlists/:id/tracks", async ({ request }) => {
        deleteBodies.push((await request.json()) as { entry_ids: string[] });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const rows = entries();
    installBaseHandlers(rows);
    const user = userEvent.setup();
    const { unmount } = renderPage();

    await screen.findByText("Track A");
    await user.click(screen.getByRole("button", { name: /remove track a/i }));
    expect(deleteBodies).toHaveLength(0); // deferred

    // Navigating away unmounts the editor; the pending removal must still commit.
    unmount();
    await waitFor(() => expect(deleteBodies).toHaveLength(1));
    expect(deleteBodies[0].entry_ids).toEqual([rows[0].id]);
  });
});
