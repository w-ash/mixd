import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test/test-utils";

import { ConnectorCard } from "./ConnectorCard";

describe("ConnectorCard", () => {
  it("renders connected Spotify with account name and description", () => {
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
    expect(screen.getByText("testuser")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(
      screen.getByText("Playlists, liked tracks, listening history"),
    ).toBeInTheDocument();
  });

  it("renders disconnected Spotify with setup hint", () => {
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
      screen.getByText("Run the CLI to connect your Spotify account."),
    ).toBeInTheDocument();
  });

  it("renders expired token badge", () => {
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
    expect(screen.getByText("musicfan42")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(
      screen.getByText("Scrobble counts, play history, loved tracks"),
    ).toBeInTheDocument();
  });

  it("renders MusicBrainz with Available badge", () => {
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
  });

  it("renders Apple Music with Coming soon badge", () => {
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
    expect(
      screen.getByText("Coming soon — connector under development."),
    ).toBeInTheDocument();
  });
});
