import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { AssignPlaylistDialog } from "./AssignPlaylistDialog";

const PLAYLIST_ID = "019da92c-0000-74aa-9a86-000000000001";

function mockCreateOk() {
  const spy = vi.fn((request: Request) => {
    return request.json().then((body: unknown) => {
      const typed = body as {
        connector_playlist_id: string;
        action_type: "add_tag" | "set_preference";
        action_value: string;
      };
      return HttpResponse.json(
        {
          assignment: {
            id: "aaaaaaaa-0000-0000-0000-000000000001",
            connector_playlist_id: typed.connector_playlist_id,
            action_type: typed.action_type,
            action_value: typed.action_value,
          },
          result: {
            preferences_applied:
              typed.action_type === "set_preference" ? 12 : 0,
            preferences_cleared: 0,
            tags_applied: typed.action_type === "add_tag" ? 42 : 0,
            tags_cleared: 0,
            conflicts_logged: 0,
            assignments_processed: 1,
          },
        },
        { status: 201 },
      );
    });
  });
  server.use(
    http.post("*/api/v1/playlist-assignments", ({ request }) => spy(request)),
  );
  // Also stub the tags autocomplete endpoint.
  server.use(
    http.get("*/api/v1/tags", () => HttpResponse.json([], { status: 200 })),
  );
  return spy;
}

describe("AssignPlaylistDialog", () => {
  it("in tag mode, submits add_tag when the user picks a tag", async () => {
    const createSpy = mockCreateOk();
    const onOpenChange = vi.fn();

    renderWithProviders(
      <AssignPlaylistDialog
        open
        onOpenChange={onOpenChange}
        mode="tag"
        playlist={{
          connector_playlist_db_id: PLAYLIST_ID,
          name: "Chill Vibes",
          current_assignments: [],
        }}
      />,
    );

    const input = await screen.findByPlaceholderText(
      /Type a tag \(e\.g\. mood:chill\)/,
    );
    await userEvent.type(input, "mood:chill");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(createSpy).toHaveBeenCalledOnce());
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("in rate mode, Rate button is disabled until a rating is picked", async () => {
    const onOpenChange = vi.fn();

    renderWithProviders(
      <AssignPlaylistDialog
        open
        onOpenChange={onOpenChange}
        mode="rate"
        playlist={{
          connector_playlist_db_id: PLAYLIST_ID,
          name: "Chill Vibes",
          current_assignments: [],
        }}
      />,
    );

    const rateBtn = await screen.findByRole("button", { name: "Rate tracks" });
    expect(rateBtn).toBeDisabled();

    await userEvent.click(
      screen.getByRole("button", { name: /Star — always welcome/ }),
    );

    expect(screen.getByRole("button", { name: "Rate tracks" })).toBeEnabled();
  });

  it("in rate mode with existing rating, button says Update rating", async () => {
    mockCreateOk();
    renderWithProviders(
      <AssignPlaylistDialog
        open
        onOpenChange={vi.fn()}
        mode="rate"
        playlist={{
          connector_playlist_db_id: PLAYLIST_ID,
          name: "Chill Vibes",
          current_assignments: [
            {
              assignment_id: "aaaaaaaa-0000-0000-0000-000000000002",
              action_type: "set_preference",
              action_value: "star",
            },
          ],
        }}
      />,
    );

    const updateBtn = await screen.findByRole("button", {
      name: "Update rating",
    });
    expect(updateBtn).toBeDisabled();

    await userEvent.click(
      screen.getByRole("button", { name: /Yah — keep in rotation/ }),
    );
    expect(screen.getByRole("button", { name: "Update rating" })).toBeEnabled();
  });
});
