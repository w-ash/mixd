import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Toaster } from "#/components/ui/sonner";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { CreatePlaylistModal } from "./CreatePlaylistModal";

describe("CreatePlaylistModal", () => {
  it("renders the trigger button", () => {
    renderWithProviders(<CreatePlaylistModal />);

    expect(
      screen.getByRole("button", { name: "New Playlist" }),
    ).toBeInTheDocument();
  });

  it("opens dialog when trigger is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CreatePlaylistModal />);

    await user.click(screen.getByRole("button", { name: "New Playlist" }));

    await waitFor(() => {
      expect(screen.getByText("Create Playlist")).toBeInTheDocument();
    });

    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toBeInTheDocument();
  });

  it("disables submit button when name is empty", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CreatePlaylistModal />);

    await user.click(screen.getByRole("button", { name: "New Playlist" }));

    await waitFor(() => {
      expect(screen.getByText("Create Playlist")).toBeInTheDocument();
    });

    const submitButton = screen.getByRole("button", { name: "Create" });
    expect(submitButton).toBeDisabled();
  });

  it("enables submit button when name is provided", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CreatePlaylistModal />);

    await user.click(screen.getByRole("button", { name: "New Playlist" }));

    await waitFor(() => {
      expect(screen.getByText("Create Playlist")).toBeInTheDocument();
    });

    const nameInput = screen.getByLabelText("Name");
    await user.type(nameInput, "My New Playlist");

    const submitButton = screen.getByRole("button", { name: "Create" });
    expect(submitButton).toBeEnabled();
  });

  it("shows toast on API error", async () => {
    server.use(
      http.post("*/api/v1/playlists", () => {
        return HttpResponse.json(
          {
            error: {
              code: "VALIDATION_ERROR",
              message: "Name already exists",
            },
          },
          { status: 409 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(
      <>
        <CreatePlaylistModal />
        <Toaster />
      </>,
    );

    await user.click(screen.getByRole("button", { name: "New Playlist" }));

    await waitFor(() => {
      expect(screen.getByText("Create Playlist")).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText("Name"), "Duplicate Name");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(screen.getByText("Failed to create playlist")).toBeInTheDocument();
    });
  });

  it("submits form and closes on success", async () => {
    server.use(
      http.post("*/api/v1/playlists", () => {
        return HttpResponse.json(
          {
            id: 99,
            name: "My New Playlist",
            description: null,
            track_count: 0,
            connector_links: [],
            updated_at: "2026-03-01T12:00:00Z",
            entries: [],
          },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<CreatePlaylistModal />);

    await user.click(screen.getByRole("button", { name: "New Playlist" }));

    await waitFor(() => {
      expect(screen.getByText("Create Playlist")).toBeInTheDocument();
    });

    const nameInput = screen.getByLabelText("Name");
    await user.type(nameInput, "My New Playlist");

    const submitButton = screen.getByRole("button", { name: "Create" });
    await user.click(submitButton);

    // Dialog should close after successful creation
    await waitFor(() => {
      expect(screen.queryByText("Create Playlist")).not.toBeInTheDocument();
    });
  });
});
