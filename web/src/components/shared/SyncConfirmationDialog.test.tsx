import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { SyncConfirmationDialog } from "./SyncConfirmationDialog";

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  playlistId: "019d0000-0000-7000-8000-000000000001",
  linkId: "019d0000-0000-7000-8000-000000000010",
  connectorName: "spotify",
  playlistName: "Test Playlist",
  currentDirection: "push",
  onStarted: vi.fn(),
};

const PREVIEW_URL = "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview";
const SYNC_URL = "*/api/v1/playlists/:playlistId/links/:linkId/sync";

function stubPreview(body: Record<string, unknown>) {
  server.use(http.get(PREVIEW_URL, () => HttpResponse.json(body)));
}

function renderDialog(overrides = {}) {
  return renderWithProviders(
    <SyncConfirmationDialog {...defaultProps} {...overrides} />,
  );
}

describe("SyncConfirmationDialog", () => {
  it("shows the preview title while fetching", () => {
    renderDialog();
    expect(screen.getByText("Sync Preview")).toBeInTheDocument();
  });

  it("shows preview with add/remove counts and collapses unchanged", async () => {
    stubPreview({
      tracks_to_add: 5,
      tracks_to_remove: 2,
      tracks_unchanged: 10,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
    });
    renderDialog();

    await waitFor(() => expect(screen.getByText("+5")).toBeInTheDocument());
    expect(screen.getByText("-2")).toBeInTheDocument();
    expect(screen.getByText("10 unchanged tracks hidden")).toBeInTheDocument();
  });

  it("shows already-in-sync when no changes", async () => {
    stubPreview({
      tracks_to_add: 0,
      tracks_to_remove: 0,
      tracks_unchanged: 15,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
    });
    renderDialog();

    await waitFor(() =>
      expect(
        screen.getByText(/playlists are already in sync/i),
      ).toBeInTheDocument(),
    );
  });

  it("shows first-sync message when no comparison data", async () => {
    stubPreview({
      tracks_to_add: 0,
      tracks_to_remove: 0,
      tracks_unchanged: 0,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: false,
    });
    renderDialog();

    await waitFor(() =>
      expect(screen.getByText(/never been synced/i)).toBeInTheDocument(),
    );
  });

  it("shows error state when preview fetch fails", async () => {
    server.use(
      http.get(PREVIEW_URL, () =>
        HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "fail" } },
          { status: 500 },
        ),
      ),
    );
    renderDialog();

    await waitFor(() =>
      expect(
        screen.getByText(/failed to load sync preview/i),
      ).toBeInTheDocument(),
    );
  });

  it("offers both direction options in one chooser", () => {
    renderDialog();
    expect(
      screen.getByRole("radio", { name: /Spotify → Mixd/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /Mixd → Spotify/ }),
    ).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    renderDialog({ open: false });
    expect(screen.queryByText("Sync Preview")).not.toBeInTheDocument();
  });

  it("gates a destructive sync with a verb-locked, count-bearing confirm", async () => {
    stubPreview({
      tracks_to_add: 0,
      tracks_to_remove: 147,
      tracks_unchanged: 3,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
      safety_flagged: true,
      safety_removals: 147,
      safety_total: 150,
      safety_message: "This removes most of the playlist.",
      confirm_token: "tok1",
    });
    renderDialog();

    await waitFor(() =>
      expect(screen.getByText(/remove 147 tracks of 150/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Remove 147 tracks" }),
    ).toBeInTheDocument();
  });

  it("does not gate a non-destructive sync", async () => {
    stubPreview({
      tracks_to_add: 3,
      tracks_to_remove: 1,
      tracks_unchanged: 10,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
      safety_flagged: false,
    });
    renderDialog();

    await waitFor(() => expect(screen.getByText("+3")).toBeInTheDocument());
    expect(
      screen.queryByText(/this sync will remove/i),
    ).not.toBeInTheDocument();
  });

  it("calls onStarted with the operation id on 202", async () => {
    stubPreview({
      tracks_to_add: 5,
      tracks_to_remove: 0,
      tracks_unchanged: 1,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
      confirm_token: "tok1",
    });
    server.use(
      http.post(SYNC_URL, () =>
        HttpResponse.json(
          { operation_id: "op-1", run_id: "run-1" },
          { status: 202 },
        ),
      ),
    );
    const onStarted = vi.fn();
    renderDialog({ onStarted });

    // Count-bearing, direction-free: the DirectionChooser owns direction.
    const btn = await screen.findByRole("button", {
      name: "Sync 5 tracks",
    });
    await userEvent.click(btn);

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("op-1"));
  });

  it("re-prompts and stays open on a stale-token 409", async () => {
    stubPreview({
      tracks_to_add: 0,
      tracks_to_remove: 147,
      tracks_unchanged: 3,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
      safety_flagged: true,
      safety_removals: 147,
      safety_total: 150,
      confirm_token: "tok1",
    });
    server.use(
      http.post(SYNC_URL, () =>
        HttpResponse.json(
          {
            error: {
              code: "CONFIRMATION_REQUIRED",
              message: "stale",
              details: { confirm_token: "tok2", removals: "147", total: "150" },
            },
          },
          { status: 409 },
        ),
      ),
    );
    const onStarted = vi.fn();
    renderDialog({ onStarted });

    const btn = await screen.findByRole("button", {
      name: "Remove 147 tracks",
    });
    await userEvent.click(btn);

    await waitFor(() =>
      expect(
        screen.getByText(/playlist changed since the preview/i),
      ).toBeInTheDocument(),
    );
    expect(onStarted).not.toHaveBeenCalled();
  });

  it("clears the 409-derived destructive state when the direction changes", async () => {
    stubPreview({
      tracks_to_add: 0,
      tracks_to_remove: 147,
      tracks_unchanged: 3,
      direction: "push",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
      safety_flagged: true,
      safety_removals: 147,
      safety_total: 150,
      confirm_token: "tok1",
    });
    server.use(
      http.post(SYNC_URL, () =>
        HttpResponse.json(
          {
            error: {
              code: "CONFIRMATION_REQUIRED",
              message: "stale",
              details: { confirm_token: "tok2", removals: "147", total: "150" },
            },
          },
          { status: 409 },
        ),
      ),
    );
    renderDialog();

    // Trigger the destructive 409 → the dialog re-prompts and stays destructive.
    await userEvent.click(
      await screen.findByRole("button", { name: "Remove 147 tracks" }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/playlist changed since the preview/i),
      ).toBeInTheDocument(),
    );

    // The other direction's preview is non-destructive.
    stubPreview({
      tracks_to_add: 4,
      tracks_to_remove: 0,
      tracks_unchanged: 1,
      direction: "pull",
      connector_name: "spotify",
      playlist_name: "Test Playlist",
      has_comparison_data: true,
      safety_flagged: false,
    });

    // Switching direction must drop the stale 409 counts/error/token, not stay
    // locked to "Remove 147 tracks" with the old direction's numbers.
    await userEvent.click(
      screen.getByRole("radio", { name: /Spotify → Mixd/ }),
    );

    await waitFor(() =>
      expect(
        screen.queryByText(/playlist changed since the preview/i),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /Remove 147 tracks/ }),
    ).not.toBeInTheDocument();
  });
});
