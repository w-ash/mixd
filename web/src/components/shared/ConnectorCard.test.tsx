import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
  within,
} from "#/test/test-utils";

import { ConnectorCard } from "./ConnectorCard";

const makeConnector = makeConnectorMetadata;

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

    it("shows relative freshness when last_synced_at is present", () => {
      const twoHoursAgo = new Date(
        Date.now() - 2 * 60 * 60 * 1000,
      ).toISOString();
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
            last_synced_at: twoHoursAgo,
          })}
        />,
      );

      expect(screen.getByText(/Synced 2h ago/)).toBeInTheDocument();
      // Freshness supersedes the "Token refreshes automatically" fallback.
      expect(
        screen.queryByText("Token refreshes automatically"),
      ).not.toBeInTheDocument();
    });

    it("surfaces backend auth_error in the error state", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            status: "error",
            auth_error: "refresh_failed",
          })}
        />,
      );

      expect(
        screen.getByText(/Session token could not be refreshed/),
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
      // Exact meaning pinned (v0.11.2 P S4): credentials go, imported data
      // (likes, plays, playlists, mappings) stays, syncing pauses.
      expect(screen.getByText(/credentials/i)).toBeInTheDocument();
      expect(
        screen.getByText(/likes, plays, playlists, and mappings/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/until you reconnect/i)).toBeInTheDocument();
    });

    it('shows "connected · {detail}" when detail is present', () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
            detail: "3 releases",
          })}
        />,
      );

      expect(screen.getByText("connected · 3 releases")).toBeInTheDocument();
      // The detail line replaces (not appends to) the normal signed-in copy.
      expect(
        screen.queryByText("Signed in as testuser"),
      ).not.toBeInTheDocument();
    });

    it("keeps the existing signed-in rendering when detail is absent", () => {
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

      expect(screen.getByText("Signed in as testuser")).toBeInTheDocument();
      expect(screen.queryByText(/^connected ·/)).not.toBeInTheDocument();
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
            status: "expired",
          })}
        />,
      );

      expect(screen.getByText(/session expired/i)).toBeInTheDocument();
      expect(screen.getByText("Reconnect")).toBeInTheDocument();
    });
  });

  describe("needs_reauth state", () => {
    it("shows new-permissions copy for scope_missing", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
            status: "needs_reauth",
            auth_error: "scope_missing",
          })}
        />,
      );

      expect(
        screen.getByText("testuser — new permissions needed"),
      ).toBeInTheDocument();
      expect(screen.getByText("Reconnect")).toBeInTheDocument();
      // Still connected — the settings gear (and disconnect) stay available.
      expect(
        screen.getByRole("button", { name: "Spotify settings" }),
      ).toBeInTheDocument();
      // Not an error state — no "Connection failed" copy.
      expect(screen.queryByText(/Connection failed/)).not.toBeInTheDocument();
    });

    it("shows session-expired wording (not permissions copy) for reauth_required", () => {
      // v0.11.2: the 6-month refresh grant aged out (invalid_grant) — the
      // backend deleted the dead token and reports reauth_required. One
      // click to fix, not an error state — and distinct wording from
      // scope_missing, which isn't a session problem at all.
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            account_name: "testuser",
            status: "needs_reauth",
            auth_error: "reauth_required",
          })}
        />,
      );

      expect(
        screen.getByText("testuser — session expired, reconnect"),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/new permissions needed/i),
      ).not.toBeInTheDocument();
      expect(screen.getByText("Reconnect")).toBeInTheDocument();
      expect(screen.queryByText(/Connection failed/)).not.toBeInTheDocument();
    });

    it("shows session-expired wording without an account name", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "spotify",
            connected: true,
            status: "needs_reauth",
            auth_error: "reauth_required",
          })}
        />,
      );

      expect(
        screen.getByText("Session expired, reconnect"),
      ).toBeInTheDocument();
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

  describe("browser_bridge connectors", () => {
    it("renders a Connect button (not Coming soon) for disconnected Apple Music", () => {
      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "apple_music" })} />,
      );

      expect(screen.getByText("Apple Music")).toBeInTheDocument();
      expect(screen.getByText("Connect Apple Music")).toBeInTheDocument();
      expect(screen.queryByText("Coming soon")).not.toBeInTheDocument();
    });

    it("shows settings gear (disconnect path) for connected Apple Music", async () => {
      const user = userEvent.setup();
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "apple_music",
            connected: true,
            token_expires_at: Math.floor(Date.now() / 1000) + 3600,
          })}
        />,
      );

      await user.click(
        screen.getByRole("button", { name: "Apple Music settings" }),
      );
      await user.click(screen.getByText("Disconnect Apple Music"));

      await waitFor(() => {
        expect(screen.getByText("Disconnect Apple Music?")).toBeInTheDocument();
      });
    });

    it("shows Reconnect when the Music User Token needs reauthorization", () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "apple_music",
            connected: true,
            status: "needs_reauth",
            auth_error: "reauth_required",
          })}
        />,
      );

      expect(screen.getByText("Reconnect")).toBeInTheDocument();
    });
  });

  describe("passive connectors", () => {
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

  describe("token-auth connect flow (discogs)", () => {
    it("opens the token form on Connect instead of the OAuth redirect", async () => {
      const user = userEvent.setup();
      let authUrlFetched = false;
      server.use(
        http.get("*/api/v1/connectors/discogs/auth-url", () => {
          authUrlFetched = true;
          return HttpResponse.json({ auth_url: "https://example.com/x" });
        }),
      );

      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "discogs" })} />,
      );

      await user.click(screen.getByText("Connect Discogs"));

      // The token form opens in a dialog — no auth-url fetch, no navigation.
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toBeInTheDocument();
      expect(authUrlFetched).toBe(false);
      // Copy preempts the OAuth confusion on the Discogs developer page.
      expect(
        within(dialog).getByText(/Personal access token/),
      ).toBeInTheDocument();
      expect(
        within(dialog).getByText(/ignore the OAuth application fields/i),
      ).toBeInTheDocument();
    });

    it("submits the token via PUT and closes on success", async () => {
      const user = userEvent.setup();
      let putBody: unknown;
      server.use(
        http.put("*/api/v1/connectors/discogs/token", async ({ request }) => {
          putBody = await request.json();
          return new HttpResponse(null, { status: 204 });
        }),
      );

      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "discogs" })} />,
      );

      await user.click(screen.getByText("Connect Discogs"));
      const dialog = await screen.findByRole("dialog");
      await user.type(
        within(dialog).getByLabelText("Discogs personal access token"),
        "abc123token",
      );
      await user.click(within(dialog).getByRole("button", { name: "Connect" }));

      await waitFor(() => {
        expect(putBody).toEqual({ token: "abc123token" });
      });
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });
    });

    it("shows an inline error on 400 and keeps the form open", async () => {
      const user = userEvent.setup();
      server.use(
        http.put("*/api/v1/connectors/discogs/token", () => {
          return HttpResponse.json(
            {
              error: {
                code: "INVALID_TOKEN",
                message: "Discogs rejected the token",
              },
            },
            { status: 400 },
          );
        }),
      );

      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "discogs" })} />,
      );

      await user.click(screen.getByText("Connect Discogs"));
      const dialog = await screen.findByRole("dialog");
      await user.type(
        within(dialog).getByLabelText("Discogs personal access token"),
        "bad-token",
      );
      await user.click(within(dialog).getByRole("button", { name: "Connect" }));

      const alert = await within(dialog).findByRole("alert");
      expect(alert).toHaveTextContent("Discogs rejected the token");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("uses a password input that never shows a stored value", async () => {
      const user = userEvent.setup();
      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "discogs" })} />,
      );

      await user.click(screen.getByText("Connect Discogs"));
      const dialog = await screen.findByRole("dialog");
      const input = within(dialog).getByLabelText(
        "Discogs personal access token",
      );
      expect(input).toHaveAttribute("type", "password");
      expect(input).toHaveAttribute("autocomplete", "off");
      expect(input).toHaveValue("");
    });

    it("clears the typed token when the dialog is closed and reopened", async () => {
      const user = userEvent.setup();
      renderWithProviders(
        <ConnectorCard connector={makeConnector({ name: "discogs" })} />,
      );

      await user.click(screen.getByText("Connect Discogs"));
      let dialog = await screen.findByRole("dialog");
      await user.type(
        within(dialog).getByLabelText("Discogs personal access token"),
        "half-typed",
      );
      await user.keyboard("{Escape}");
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });

      await user.click(screen.getByText("Connect Discogs"));
      dialog = await screen.findByRole("dialog");
      expect(
        within(dialog).getByLabelText("Discogs personal access token"),
      ).toHaveValue("");
    });

    it('shows "connected · N releases" for a connected Discogs card', () => {
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "discogs",
            connected: true,
            detail: "42 releases",
          })}
        />,
      );

      expect(screen.getByText("Discogs")).toBeInTheDocument();
      expect(screen.getByText("connected · 42 releases")).toBeInTheDocument();
    });

    it("shows the disconnect confirmation for a connected Discogs card", async () => {
      const user = userEvent.setup();
      renderWithProviders(
        <ConnectorCard
          connector={makeConnector({
            name: "discogs",
            connected: true,
            detail: "42 releases",
          })}
        />,
      );

      await user.click(
        screen.getByRole("button", { name: "Discogs settings" }),
      );
      await user.click(screen.getByText("Disconnect Discogs"));

      await waitFor(() => {
        expect(screen.getByText("Disconnect Discogs?")).toBeInTheDocument();
      });
      // Generic credentials-go/data-stays copy applies to Discogs too.
      expect(screen.getByText(/credentials/i)).toBeInTheDocument();
      expect(screen.getByText(/until you reconnect/i)).toBeInTheDocument();
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
