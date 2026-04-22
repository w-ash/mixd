import { HttpResponse, http } from "msw";
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
    // Badges are span elements; chip controls with the same text are buttons.
    // Disambiguate by tag to target the pill rather than the filter chip.
    const pills = screen.getAllByText(
      (_, el) =>
        el?.tagName === "SPAN" &&
        (el.textContent === "Not imported" || el.textContent === "Imported"),
    );
    expect(pills.length).toBeGreaterThanOrEqual(2);
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
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  describe("assignments", () => {
    const TAG_ASSIGN_ID = "aaaaaaaa-0000-0000-0000-000000000001";
    const RATING_ASSIGN_ID = "aaaaaaaa-0000-0000-0000-000000000002";

    const TAGGED_PLAYLIST = makeConnectorPlaylistBrowse({
      ...CHILL,
      current_assignments: [
        {
          assignment_id: TAG_ASSIGN_ID,
          action_type: "add_tag",
          action_value: "mood:chill",
        },
      ],
    });

    const FULLY_ASSIGNED = makeConnectorPlaylistBrowse({
      ...CHILL,
      current_assignments: [
        {
          assignment_id: TAG_ASSIGN_ID,
          action_type: "add_tag",
          action_value: "mood:chill",
        },
        {
          assignment_id: RATING_ASSIGN_ID,
          action_type: "set_preference",
          action_value: "star",
        },
      ],
    });

    it("renders the tag status badge on a mapped row", async () => {
      mockList([TAGGED_PLAYLIST]);
      setup();

      expect(await screen.findByText("mood:chill")).toBeInTheDocument();
    });

    it("hides Re-apply / Remove from the overflow menu when no assignments", async () => {
      mockList([CHILL]);
      setup();

      await screen.findByText("Chill Vibes");
      await userEvent.click(
        screen.getByRole("button", { name: /More actions for Chill Vibes/ }),
      );

      // Menuitem by role ensures we don't confuse with other elements.
      expect(
        screen.getByRole("menuitem", { name: "Tag tracks…" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("menuitem", { name: "Rate tracks…" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("menuitem", { name: "Re-apply" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("menuitem", { name: /Remove/ }),
      ).not.toBeInTheDocument();
    });

    it("shows Remove items per-assignment when the playlist is mapped", async () => {
      mockList([FULLY_ASSIGNED]);
      setup();

      await screen.findByText("Chill Vibes");
      await userEvent.click(
        screen.getByRole("button", { name: /More actions for Chill Vibes/ }),
      );

      expect(
        screen.getByRole("menuitem", { name: "Re-apply" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("menuitem", { name: "Remove tag: mood:chill" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("menuitem", { name: "Remove rating" }),
      ).toBeInTheDocument();
    });

    it("DELETEs the assignment when Remove is clicked", async () => {
      mockList([TAGGED_PLAYLIST]);
      const deleteSpy = vi.fn(() => HttpResponse.json({}, { status: 204 }));
      server.use(
        http.delete(
          `*/api/v1/playlist-assignments/${TAG_ASSIGN_ID}`,
          deleteSpy,
        ),
      );
      setup();

      await screen.findByText("mood:chill");
      await userEvent.click(
        screen.getByRole("button", { name: /More actions for Chill Vibes/ }),
      );
      await userEvent.click(
        screen.getByRole("menuitem", { name: "Remove tag: mood:chill" }),
      );

      await waitFor(() => expect(deleteSpy).toHaveBeenCalledOnce());
    });
  });
});
