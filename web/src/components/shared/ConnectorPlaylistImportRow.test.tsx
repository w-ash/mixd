import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type {
  ActiveAssignmentSchema,
  ConnectorPlaylistBrowseSchema,
} from "#/api/generated/model";
import { makeConnectorPlaylistBrowse } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { ConnectorPlaylistImportRow } from "./ConnectorPlaylistImportRow";

const TAG_ASSIGN_ID = "aaaaaaaa-0000-0000-0000-000000000001";
const RATING_ASSIGN_ID = "aaaaaaaa-0000-0000-0000-000000000002";

const TAG: ActiveAssignmentSchema = {
  assignment_id: TAG_ASSIGN_ID,
  action_type: "add_tag",
  action_value: "mood:chill",
};

const RATING: ActiveAssignmentSchema = {
  assignment_id: RATING_ASSIGN_ID,
  action_type: "set_preference",
  action_value: "star",
};

const CHILL = makeConnectorPlaylistBrowse({
  connector_playlist_identifier: "sp1",
  name: "Chill Vibes",
  track_count: 247,
});

function setup(playlist: ConnectorPlaylistBrowseSchema = CHILL) {
  const onSelectedChange = vi.fn();
  const onAssign = vi.fn();
  renderWithProviders(
    <ConnectorPlaylistImportRow
      playlist={playlist}
      connectorName="spotify"
      selected={false}
      onSelectedChange={onSelectedChange}
      onAssign={onAssign}
    />,
  );
  return { onSelectedChange, onAssign };
}

const openMenu = () =>
  userEvent.click(
    screen.getByRole("button", { name: /More actions for Chill Vibes/ }),
  );

describe("ConnectorPlaylistImportRow", () => {
  it("renders the playlist, its meta line and its assignment chips", () => {
    setup(
      makeConnectorPlaylistBrowse({ ...CHILL, current_assignments: [TAG] }),
    );

    expect(screen.getByText("Chill Vibes")).toBeInTheDocument();
    expect(screen.getByText("247")).toBeInTheDocument();
    expect(screen.getByText("mood:chill")).toBeInTheDocument();
    expect(screen.getByText("Not imported")).toBeInTheDocument();
  });

  it("reports checkbox toggles to the parent", async () => {
    const { onSelectedChange } = setup();

    await userEvent.click(screen.getByText("Chill Vibes"));

    expect(onSelectedChange).toHaveBeenCalledWith(true);
  });

  it("offers only tag/rate actions when the playlist has no assignments", async () => {
    const { onAssign } = setup();

    await openMenu();

    expect(
      screen.getByRole("menuitem", { name: "Rate tracks…" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: "Re-apply" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /Remove/ }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("menuitem", { name: "Tag tracks…" }),
    );
    expect(onAssign).toHaveBeenCalledWith("tag", CHILL);
  });

  it("offers re-apply and a remove item per assignment when mapped", async () => {
    setup(
      makeConnectorPlaylistBrowse({
        ...CHILL,
        current_assignments: [TAG, RATING],
      }),
    );

    await openMenu();

    expect(
      screen.getByRole("menuitem", { name: "Re-apply" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Remove tag: mood:chill" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Remove rating" }),
    ).toBeInTheDocument();
    // A rating already set reads as an update, not a fresh rating.
    expect(
      screen.getByRole("menuitem", { name: "Update rating…" }),
    ).toBeInTheDocument();
  });

  it("DELETEs the assignment behind the Remove item", async () => {
    const deleteSpy = vi.fn(() => HttpResponse.json({}, { status: 204 }));
    server.use(
      http.delete(`*/api/v1/playlist-assignments/${TAG_ASSIGN_ID}`, deleteSpy),
    );
    setup(
      makeConnectorPlaylistBrowse({ ...CHILL, current_assignments: [TAG] }),
    );

    await openMenu();
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Remove tag: mood:chill" }),
    );

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledOnce());
  });

  it("applies every assignment when Re-apply is chosen", async () => {
    const applySpy = vi.fn(() =>
      HttpResponse.json({ tags_applied: 3, preferences_applied: 1 }),
    );
    server.use(http.post("*/api/v1/playlist-assignments/:id/apply", applySpy));
    setup(
      makeConnectorPlaylistBrowse({
        ...CHILL,
        current_assignments: [TAG, RATING],
      }),
    );

    await openMenu();
    await userEvent.click(screen.getByRole("menuitem", { name: "Re-apply" }));

    await waitFor(() => expect(applySpy).toHaveBeenCalledTimes(2));
  });
});
