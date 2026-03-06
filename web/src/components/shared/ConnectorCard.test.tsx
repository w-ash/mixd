import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test/test-utils";

import { ConnectorCard } from "./ConnectorCard";

describe("ConnectorCard", () => {
  it("renders connected Spotify with account name and token status", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "spotify",
          connected: true,
          account_name: "testuser",
          token_expires_at: Math.floor(Date.now() / 1000) + 3600,
        }}
      />,
    );

    expect(screen.getByText("Spotify")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(
      screen.getByText("Playlists, liked tracks, listening history"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Signed in as testuser/)).toBeInTheDocument();
    expect(
      screen.getByText(/token refreshes automatically/),
    ).toBeInTheDocument();
  });

  it("renders disconnected Spotify with auth hint", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "spotify",
          connected: false,
          account_name: null,
          token_expires_at: null,
        }}
      />,
    );

    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(
      screen.getByText("Playlists, liked tracks, listening history"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Not connected \u00b7 run CLI to authenticate"),
    ).toBeInTheDocument();
  });

  it("renders expired token badge and detail", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "spotify",
          connected: true,
          account_name: "testuser",
          token_expires_at: Math.floor(Date.now() / 1000) - 3600,
        }}
      />,
    );

    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText(/token expired/)).toBeInTheDocument();
  });

  it("renders connected Last.fm with account name", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "lastfm",
          connected: true,
          account_name: "musicfan42",
          token_expires_at: null,
        }}
      />,
    );

    expect(screen.getByText("Last.fm")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(
      screen.getByText("Scrobble counts, play history, loved tracks"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Signed in as musicfan42/)).toBeInTheDocument();
  });

  it("renders MusicBrainz with Available badge and public API detail", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "musicbrainz",
          connected: true,
          account_name: null,
          token_expires_at: null,
        }}
      />,
    );

    expect(screen.getByText("MusicBrainz")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();
    expect(
      screen.getByText("Track identification, metadata enrichment"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Public API \u00b7 no authentication required"),
    ).toBeInTheDocument();
  });

  it("renders Apple Music with Coming soon badge and detail", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "apple",
          connected: false,
          account_name: null,
          token_expires_at: null,
        }}
      />,
    );

    expect(screen.getByText("Apple Music")).toBeInTheDocument();
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.getByText("Library, playlists")).toBeInTheDocument();
    expect(
      screen.getByText("Coming soon \u00b7 connector under development"),
    ).toBeInTheDocument();
  });

  it("renders disconnected auth detail for unconfigured connectors", () => {
    renderWithProviders(
      <ConnectorCard
        connector={{
          name: "lastfm",
          connected: false,
          account_name: null,
          token_expires_at: null,
        }}
      />,
    );

    expect(
      screen.getByText("Not connected \u00b7 run CLI to authenticate"),
    ).toBeInTheDocument();
  });
});
