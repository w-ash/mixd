import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { SyncConfirmationDialog } from "./SyncConfirmationDialog";

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  playlistId: "019d0000-0000-7000-8000-000000000001",
  linkId: "019d0000-0000-7000-8000-000000000010",
  connectorName: "spotify",
  playlistName: "Test Playlist",
  currentDirection: "push",
  isPending: false,
  onConfirm: vi.fn(),
};

function renderDialog(overrides = {}) {
  return renderWithProviders(
    <SyncConfirmationDialog {...defaultProps} {...overrides} />,
  );
}

describe("SyncConfirmationDialog", () => {
  it("shows loading state while fetching preview", () => {
    // Default MSW handler will respond, but there's a brief loading state
    renderDialog();
    expect(screen.getByText("Sync Preview")).toBeInTheDocument();
  });

  it("shows preview with add/remove counts", async () => {
    server.use(
      http.get(
        "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview",
        () => {
          return HttpResponse.json({
            tracks_to_add: 5,
            tracks_to_remove: 2,
            tracks_unchanged: 10,
            direction: "push",
            connector_name: "spotify",
            playlist_name: "Test Playlist",
            has_comparison_data: true,
          });
        },
      ),
    );

    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("+5")).toBeInTheDocument();
    });
    expect(screen.getByText("-2")).toBeInTheDocument();
    expect(screen.getByText("10 tracks unchanged")).toBeInTheDocument();
  });

  it("shows already-in-sync when no changes", async () => {
    server.use(
      http.get(
        "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview",
        () => {
          return HttpResponse.json({
            tracks_to_add: 0,
            tracks_to_remove: 0,
            tracks_unchanged: 15,
            direction: "push",
            connector_name: "spotify",
            playlist_name: "Test Playlist",
            has_comparison_data: true,
          });
        },
      ),
    );

    renderDialog();

    await waitFor(() => {
      expect(
        screen.getByText(/playlists are already in sync/i),
      ).toBeInTheDocument();
    });
  });

  it("shows first-sync message when no comparison data", async () => {
    server.use(
      http.get(
        "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview",
        () => {
          return HttpResponse.json({
            tracks_to_add: 0,
            tracks_to_remove: 0,
            tracks_unchanged: 0,
            direction: "push",
            connector_name: "spotify",
            playlist_name: "Test Playlist",
            has_comparison_data: false,
          });
        },
      ),
    );

    renderDialog();

    await waitFor(() => {
      expect(screen.getByText(/never been synced/i)).toBeInTheDocument();
    });
  });

  it("shows error state when preview fetch fails", async () => {
    server.use(
      http.get(
        "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview",
        () => {
          return HttpResponse.json(
            { error: { code: "INTERNAL_ERROR", message: "fail" } },
            { status: 500 },
          );
        },
      ),
    );

    renderDialog();

    await waitFor(() => {
      expect(
        screen.getByText(/failed to load sync preview/i),
      ).toBeInTheDocument();
    });
  });

  it("shows direction toggle buttons", () => {
    renderDialog();
    // Both direction buttons are visible: "Local → Spotify" and "Spotify → Local"
    const buttons = screen.getAllByRole("button", { name: /local/i });
    expect(buttons.length).toBe(2);
  });

  it("does not render when closed", () => {
    renderDialog({ open: false });
    expect(screen.queryByText("Sync Preview")).not.toBeInTheDocument();
  });

  it("shows destructive warning when safety_flagged is true", async () => {
    server.use(
      http.get(
        "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview",
        () => {
          return HttpResponse.json({
            tracks_to_add: 0,
            tracks_to_remove: 147,
            tracks_unchanged: 3,
            direction: "push",
            connector_name: "spotify",
            playlist_name: "Test Playlist",
            has_comparison_data: true,
            safety_flagged: true,
            safety_message:
              "This will remove 147 of 150 tracks. 3 will remain.",
          });
        },
      ),
    );

    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Destructive sync detected")).toBeInTheDocument();
    });
    expect(
      screen.getByText("This will remove 147 of 150 tracks. 3 will remain."),
    ).toBeInTheDocument();
  });

  it("does not show warning when safety_flagged is false", async () => {
    server.use(
      http.get(
        "*/api/v1/playlists/:playlistId/links/:linkId/sync/preview",
        () => {
          return HttpResponse.json({
            tracks_to_add: 3,
            tracks_to_remove: 1,
            tracks_unchanged: 10,
            direction: "push",
            connector_name: "spotify",
            playlist_name: "Test Playlist",
            has_comparison_data: true,
            safety_flagged: false,
            safety_message: null,
          });
        },
      ),
    );

    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("+3")).toBeInTheDocument();
    });
    expect(
      screen.queryByText("Destructive sync detected"),
    ).not.toBeInTheDocument();
  });
});
