import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { ImportPlaylistsConfirmDialog } from "./ImportPlaylistsConfirmDialog";

function setup(
  overrides: Partial<Parameters<typeof ImportPlaylistsConfirmDialog>[0]> = {},
) {
  const onOpenChange = vi.fn();
  const onImported = vi.fn();
  renderWithProviders(
    <ImportPlaylistsConfirmDialog
      open={true}
      onOpenChange={onOpenChange}
      playlists={[
        { id: "sp1", name: "Chill Vibes" },
        { id: "sp2", name: "Workout Mix" },
      ]}
      onImported={onImported}
      {...overrides}
    />,
  );
  return { onOpenChange, onImported };
}

describe("ImportPlaylistsConfirmDialog", () => {
  it("renders the playlist count and selected names", async () => {
    setup();

    expect(
      await screen.findByRole("heading", { name: "Import 2 playlists" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Chill Vibes")).toBeInTheDocument();
    expect(screen.getByText("Workout Mix")).toBeInTheDocument();
  });

  it("defaults to Spotify-managed (pull) direction", async () => {
    setup();

    const pullRadio = screen.getByRole("radio", {
      name: /Spotify-managed/,
    });
    expect(pullRadio).toBeChecked();
  });

  it("submits with selected sync direction and invokes onImported on success", async () => {
    server.use(
      http.post(
        "*/api/v1/connectors/spotify/playlists/import",
        async ({ request }) => {
          const body = (await request.json()) as {
            sync_direction: string;
            connector_playlist_ids: string[];
          };
          expect(body.sync_direction).toBe("push");
          expect(body.connector_playlist_ids).toEqual(["sp1", "sp2"]);
          return HttpResponse.json({
            succeeded: [
              {
                connector_playlist_identifier: "sp1",
                canonical_playlist_id: "uuid-1",
                resolved: 10,
                unresolved: 0,
              },
              {
                connector_playlist_identifier: "sp2",
                canonical_playlist_id: "uuid-2",
                resolved: 5,
                unresolved: 2,
              },
            ],
            skipped_unchanged: [],
            failed: [],
          });
        },
      ),
    );

    const { onOpenChange, onImported } = setup();

    // Switch to Mixd-managed.
    await userEvent.click(screen.getByRole("radio", { name: /Mixd-managed/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Import 2 playlists" }),
    );

    await waitFor(() => {
      expect(onImported).toHaveBeenCalledOnce();
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("truncates the playlist list at 10 with '… and N more'", async () => {
    const many = Array.from({ length: 15 }, (_, i) => ({
      id: `pl${i}`,
      name: `Playlist ${i}`,
    }));
    setup({ playlists: many });

    expect(await screen.findByText("Playlist 0")).toBeInTheDocument();
    expect(screen.getByText("Playlist 9")).toBeInTheDocument();
    // The 11th name is not shown; the overflow message appears instead.
    expect(screen.queryByText("Playlist 10")).not.toBeInTheDocument();
    expect(screen.getByText("… and 5 more")).toBeInTheDocument();
  });

  it("disables the Import button when the ID list is empty", async () => {
    setup({ playlists: [] });

    expect(
      await screen.findByRole("button", { name: "Import 0 playlists" }),
    ).toBeDisabled();
  });

  it("closes without submitting on cancel", async () => {
    const { onOpenChange } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
