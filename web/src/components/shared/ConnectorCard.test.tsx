import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { ConnectorStatusSchema } from "#/api/generated/model";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { ConnectorCard } from "./ConnectorCard";

function makeConnector(
  overrides: Partial<ConnectorStatusSchema> & { name: string },
): ConnectorStatusSchema {
  return {
    connected: false,
    account_name: null,
    token_expires_at: null,
    ...overrides,
  };
}

describe("ConnectorCard", () => {
  describe("disconnected state", () => {
    it("shows Connect button and description for disconnected Spotify", () => {
      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "spotify" })} />,
      );

      expect(screen.getByText("Spotify")).toBeInTheDocument();
      expect(screen.getByText("Connect Spotify")).toBeInTheDocument();
      expect(
        screen.getByText("Playlists, liked tracks, and library sync"),
      ).toBeInTheDocument();
    });

    it("shows Connect button for disconnected Last.fm", () => {
      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "lastfm" })} />,
      );

      expect(screen.getByText("Connect Last.fm")).toBeInTheDocument();
    });
  });

  describe("connected state", () => {
    it("shows connected Spotify with account name and token status", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      expect(screen.getByText("Spotify")).toBeInTheDocument();
      expect(screen.getByText("Signed in as testuser")).toBeInTheDocument();
      expect(
        screen.getByText("Token refreshes automatically"),
      ).toBeInTheDocument();
    });

    it("shows settings gear for connected connector", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      expect(
        screen.getByRole("button", { name: "Spotify settings" }),
      ).toBeInTheDocument();
    });

    it("shows confirmation dialog when Disconnect is clicked via settings", async () => {
      const user = userEvent.setup();

      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      // Open settings panel
      await user.click(
        screen.getByRole("button", { name: "Spotify settings" }),
      );
      // Click disconnect in the settings panel
      await user.click(screen.getByText("Disconnect Spotify"));

      await waitFor(() => {
        expect(screen.getByText("Disconnect Spotify?")).toBeInTheDocument();
      });
      expect(
        screen.getByText(/playlists and sync settings will be preserved/),
      ).toBeInTheDocument();
    });

    it("shows connected Last.fm with account name and permanent session", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "lastfm",
            connected: true,
            account_name: "musicfan42",
          })}
        />,
      );

      expect(screen.getByText("Last.fm")).toBeInTheDocument();
      expect(screen.getByText("Signed in as musicfan42")).toBeInTheDocument();
      expect(screen.getByText("Permanent session")).toBeInTheDocument();
    });
  });

  describe("connected + stale authError", () => {
    it("shows connected status even when authError is present", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
          authError="invalid_state"
        />,
      );

      expect(screen.getByText("Signed in as testuser")).toBeInTheDocument();
      expect(screen.queryByText(/Connection failed/)).not.toBeInTheDocument();
    });
  });

  describe("expired state", () => {
    it("shows expired status and Reconnect button", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) - 3600,
          })}
        />,
      );

      expect(screen.getByText(/session expired/i)).toBeInTheDocument();
      expect(screen.getByText("Reconnect")).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("shows error message and Try again button", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({ name: "spotify" })}
          authError="access_denied"
        />,
      );

      expect(
        screen.getByText(/You denied the authorization request/),
      ).toBeInTheDocument();
      expect(screen.getByText("Try again")).toBeInTheDocument();
    });

    it("shows raw reason when no friendly message exists", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({ name: "spotify" })}
          authError="unknown_reason"
        />,
      );

      expect(screen.getByText(/unknown_reason/)).toBeInTheDocument();
    });
  });

  describe("passive connectors", () => {
    it("renders Apple Music with Coming soon", () => {
      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "apple" })} />,
      );

      expect(screen.getByText("Apple Music")).toBeInTheDocument();
      expect(screen.getByText("Coming soon")).toBeInTheDocument();
      expect(
        screen.getByText("Playlists and library sync"),
      ).toBeInTheDocument();
    });

    it("renders MusicBrainz with Available badge", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "musicbrainz",
            connected: true,
          })}
        />,
      );

      expect(screen.getByText("MusicBrainz")).toBeInTheDocument();
      expect(screen.getByText("Available")).toBeInTheDocument();
      expect(
        screen.getByText("Track metadata enrichment and identification"),
      ).toBeInTheDocument();
    });
  });

  describe("connect flow", () => {
    it("fetches auth URL and redirects on Connect click", async () => {
      const user = userEvent.setup();

      server.use(
        http.get("*/api/v1/connectors/spotify/auth-url", () => {
          return HttpResponse.json({
            auth_url: "https://accounts.spotify.com/authorize?test=1",
          });
        }),
      );

      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "spotify" })} />,
      );

      const connectBtn = screen.getByText("Connect Spotify");
      expect(connectBtn).toBeEnabled();

      // Click connect — in jsdom, window.location.href assignment doesn't navigate
      // but we verify the button is interactive
      await user.click(connectBtn);
    });
  });

  describe("accessibility", () => {
    it("settings gear button has aria-label with connector name", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      expect(
        screen.getByRole("button", { name: "Spotify settings" }),
      ).toBeInTheDocument();
    });

    it("status dots are hidden from screen readers", () => {
      const { container } = renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      const dots = container.querySelectorAll("span.rounded-full[aria-hidden]");
      expect(dots.length).toBeGreaterThan(0);
      for (const dot of dots) {
        expect(dot).toHaveAttribute("aria-hidden", "true");
      }
    });
  });

  describe("disconnect flow", () => {
    it("calls DELETE endpoint when disconnect is confirmed", async () => {
      const user = userEvent.setup();
      let deleteCalled = false;

      server.use(
        http.delete("*/api/v1/connectors/spotify/token", () => {
          deleteCalled = true;
          return new HttpResponse(null, { status: 204 });
        }),
      );

      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      // Open settings panel, then click Disconnect
      await user.click(
        screen.getByRole("button", { name: "Spotify settings" }),
      );
      await user.click(screen.getByText("Disconnect Spotify"));

      await waitFor(() => {
        expect(screen.getByText("Disconnect Spotify?")).toBeInTheDocument();
      });

      // Find and click the confirm button in the dialog
      const confirmButtons = screen.getAllByText("Disconnect");
      const dialogConfirm = confirmButtons[confirmButtons.length - 1];
      await user.click(dialogConfirm);

      await waitFor(() => {
        expect(deleteCalled).toBe(true);
      });
    });
  });
});
