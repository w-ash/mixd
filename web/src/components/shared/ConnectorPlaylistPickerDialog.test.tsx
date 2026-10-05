import { delay, HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorPlaylistBrowseSchema } from "#/api/generated/model";
import {
  makeConnectorMetadata,
  makeConnectorPlaylistBrowse,
} from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { ConnectorPlaylistPickerDialog } from "./ConnectorPlaylistPickerDialog";

const CHILL = makeConnectorPlaylistBrowse({
  connector_playlist_identifier: "sp1",
  name: "Chill Vibes",
  track_count: 247,
});

const WORKOUT = makeConnectorPlaylistBrowse({
  connector_playlist_identifier: "sp2",
  name: "Workout Mix",
  track_count: 156,
  is_public: false,
  import_status: "imported",
});

const LATE_NIGHT = makeConnectorPlaylistBrowse({
  connector_playlist_identifier: "sp3",
  name: "Late Night",
  track_count: 89,
  collaborative: true,
  is_public: false,
});

function mockList(
  playlists: ConnectorPlaylistBrowseSchema[],
  fromCache = true,
) {
  server.use(
    http.get("*/api/v1/connectors/spotify/playlists", () =>
      HttpResponse.json({
        data: playlists,
        from_cache: fromCache,
        fetched_at: new Date().toISOString(),
      }),
    ),
  );
}

const SPOTIFY_CONNECTOR = makeConnectorMetadata({
  name: "spotify",
  connected: true,
  status: "connected",
});

function setup(
  overrides: Partial<Parameters<typeof ConnectorPlaylistPickerDialog>[0]> = {},
) {
  const onOpenChange = vi.fn();
  const onConfirm = vi.fn();
  renderWithProviders(
    <ConnectorPlaylistPickerDialog
      open={true}
      connector={SPOTIFY_CONNECTOR}
      onOpenChange={onOpenChange}
      onConfirm={onConfirm}
      {...overrides}
    />,
  );
  return { onOpenChange, onConfirm };
}

describe("ConnectorPlaylistPickerDialog", () => {
  it("renders playlists and import status", async () => {
    mockList([CHILL, WORKOUT]);
    setup();

    expect(await screen.findByText("Chill Vibes")).toBeInTheDocument();
    expect(screen.getByText("Workout Mix")).toBeInTheDocument();
    // Each label appears once as a status filter chip and once as the row pill:
    // Chill Vibes is not imported, Workout Mix is imported.
    expect(screen.getAllByText("Not imported")).toHaveLength(2);
    expect(screen.getAllByText("Imported")).toHaveLength(2);
  });

  it("keeps the refresh button pending until the new list has landed", async () => {
    // The spinner is the only signal that a force-refresh is in flight; if the
    // mutation resolves before the refetch, it stops over the stale list.
    let call = 0;
    server.use(
      http.get("*/api/v1/connectors/spotify/playlists", async () => {
        call += 1;
        // Only the post-refresh refetch is delayed, so a spinner that stopped
        // early is observable while the second response is still in the air.
        if (call > 2) await delay(150);
        return HttpResponse.json({
          data: call > 2 ? [CHILL, WORKOUT] : [CHILL],
          from_cache: call <= 1,
          fetched_at: new Date().toISOString(),
        });
      }),
    );
    setup();

    await screen.findByText("Chill Vibes");
    await userEvent.click(screen.getByLabelText("Refresh from Spotify"));

    let stoppedOverTheStaleList = false;
    await waitFor(
      () => {
        const button = screen.getByLabelText(
          "Refresh from Spotify",
        ) as HTMLButtonElement;
        if (!button.disabled && screen.queryByText("Workout Mix") === null) {
          stoppedOverTheStaleList = true;
        }
        expect(screen.getByText("Workout Mix")).toBeInTheDocument();
      },
      { interval: 10 },
    );
    expect(stoppedOverTheStaleList).toBe(false);
  });

  it("filters by search (client-side, case-insensitive substring)", async () => {
    mockList([CHILL, WORKOUT, LATE_NIGHT]);
    setup();

    await screen.findByText("Chill Vibes");
    const input = screen.getByLabelText("Search Spotify playlists");
    await userEvent.type(input, "chill");

    await waitFor(() => {
      expect(screen.getByText("Chill Vibes")).toBeInTheDocument();
      expect(screen.queryByText("Workout Mix")).not.toBeInTheDocument();
      expect(screen.queryByText("Late Night")).not.toBeInTheDocument();
    });
  });

  it("filters by import status chip", async () => {
    mockList([CHILL, WORKOUT, LATE_NIGHT]);
    setup();

    await screen.findByText("Chill Vibes");
    await userEvent.click(screen.getByRole("button", { name: "Imported" }));

    await waitFor(() => {
      expect(screen.queryByText("Chill Vibes")).not.toBeInTheDocument();
      expect(screen.getByText("Workout Mix")).toBeInTheDocument();
      expect(screen.queryByText("Late Night")).not.toBeInTheDocument();
    });
  });

  it("filters by Collaborative attribute chip", async () => {
    mockList([CHILL, WORKOUT, LATE_NIGHT]);
    setup();

    await screen.findByText("Chill Vibes");
    await userEvent.click(
      screen.getByRole("button", { name: "Collaborative" }),
    );

    await waitFor(() => {
      expect(screen.queryByText("Chill Vibes")).not.toBeInTheDocument();
      expect(screen.queryByText("Workout Mix")).not.toBeInTheDocument();
      expect(screen.getByText("Late Night")).toBeInTheDocument();
    });
  });

  it("supports multi-select and the Import button emits selected ids + names", async () => {
    mockList([CHILL, WORKOUT, LATE_NIGHT]);
    const { onConfirm } = setup();

    await screen.findByText("Chill Vibes");

    // Click the two row labels (labels wrap the checkbox — more natural
    // than hunting for unlabeled checkboxes by index).
    await userEvent.click(screen.getByText("Chill Vibes"));
    await userEvent.click(screen.getByText("Late Night"));

    const importBtn = screen.getByRole("button", {
      name: /Import 2 playlists/,
    });
    expect(importBtn).toBeEnabled();
    await userEvent.click(importBtn);

    expect(onConfirm).toHaveBeenCalledOnce();
    const emitted = onConfirm.mock.calls[0][0] as {
      id: string;
      name: string;
    }[];
    expect(emitted.map((p) => p.id).sort()).toEqual(["sp1", "sp3"]);
    expect(emitted.map((p) => p.name).sort()).toEqual([
      "Chill Vibes",
      "Late Night",
    ]);
  });

  it("keeps rows picked under an earlier search", async () => {
    mockList([CHILL, WORKOUT, LATE_NIGHT]);
    const { onConfirm } = setup();

    await screen.findByText("Chill Vibes");
    await userEvent.click(screen.getByText("Chill Vibes"));

    // Narrow to a row the first pick does not match, then pick there too.
    const input = screen.getByLabelText("Search Spotify playlists");
    await userEvent.type(input, "night");
    await waitFor(() =>
      expect(screen.queryByText("Chill Vibes")).not.toBeInTheDocument(),
    );
    await userEvent.click(screen.getByText("Late Night"));

    // The header speaks for the visible row; the footer for the whole pick.
    expect(screen.getByText(/1 of 1 selected/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /Import 2 playlists/ }),
    );

    const emitted = onConfirm.mock.calls[0][0] as { id: string }[];
    expect(emitted.map((p) => p.id).sort()).toEqual(["sp1", "sp3"]);
  });

  it("disables the Import button when nothing is selected", async () => {
    mockList([CHILL]);
    setup();

    await screen.findByText("Chill Vibes");
    expect(
      screen.getByRole("button", { name: /Import 0 playlists/ }),
    ).toBeDisabled();
  });

  it("select-all toggles indeterminate state correctly", async () => {
    mockList([CHILL, LATE_NIGHT]);
    setup();

    await screen.findByText("Chill Vibes");
    const header = screen.getByLabelText("Select all visible playlists");

    expect(header).toHaveAttribute("data-state", "unchecked");
    await userEvent.click(header);
    expect(header).toHaveAttribute("data-state", "checked");

    // Deselect one row → header becomes indeterminate.
    await userEvent.click(screen.getByText("Chill Vibes"));
    expect(header).toHaveAttribute("data-state", "indeterminate");
  });

  it("shows empty-state when API returns no playlists", async () => {
    mockList([]);
    setup();

    expect(await screen.findByText("No playlists")).toBeInTheDocument();
  });

  it("shows filter-mismatch empty-state when filters hide everything", async () => {
    mockList([WORKOUT]);
    setup();

    await screen.findByText("Workout Mix");
    await userEvent.click(screen.getByRole("button", { name: "Not imported" }));

    expect(
      await screen.findByText("No playlists match your filters"),
    ).toBeInTheDocument();
  });

  it("resets selection and search on close", async () => {
    mockList([CHILL]);
    const { onOpenChange } = setup();

    await screen.findByText("Chill Vibes");
    await userEvent.click(screen.getByText("Chill Vibes"));
    const input = screen.getByLabelText("Search Spotify playlists");
    await userEvent.type(input, "chill");
    expect(
      screen.getByRole("button", { name: "Import 1 playlist" }),
    ).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    // The parent keeps `open` here, so the reset is visible in place.
    expect(input).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Import 0 playlists" }),
    ).toBeDisabled();
  });

  describe("select mode (link-to-existing flow)", () => {
    it("emits a single playlist on row click and strips import chrome", async () => {
      mockList([CHILL, WORKOUT]);
      const { onConfirm } = setup({ mode: "select" });

      // Wait for the list, then confirm the title reflects link/select intent.
      expect(await screen.findByText("Chill Vibes")).toBeVisible();
      expect(screen.getByText(/^Select a .* playlist$/)).toBeInTheDocument();

      // No multi-select Import action button (the "Import N playlists" footer —
      // distinct from the "Imported" status filter chip), no select-all, no
      // per-row assignment menu.
      expect(
        screen.queryByRole("button", { name: /Import \d+ playlist/ }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Select all visible playlists"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /More actions/ }),
      ).not.toBeInTheDocument();

      // Clicking a row confirms exactly that one playlist.
      await userEvent.click(screen.getByText("Chill Vibes"));
      expect(onConfirm).toHaveBeenCalledWith([
        { id: "sp1", name: "Chill Vibes" },
      ]);
    });
  });
});
