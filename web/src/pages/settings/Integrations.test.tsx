import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { Integrations } from "./Integrations";

const allConnectors = [
  makeConnectorMetadata({
    name: "spotify",
    connected: true,
    account_name: "testuser",
    token_expires_at: Math.floor(Date.now() / 1000) + 3600,
    status: "connected",
  }),
  makeConnectorMetadata({
    name: "lastfm",
    connected: true,
    account_name: "lfmuser",
    status: "connected",
  }),
  makeConnectorMetadata({ name: "musicbrainz", connected: true }),
  makeConnectorMetadata({ name: "apple_music" }),
];

describe("Integrations", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Integrations />);

    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders connector cards grouped into sections", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json(allConnectors, { status: 200 });
      }),
    );

    renderWithProviders(<Integrations />);

    await waitFor(() => {
      expect(screen.getByText("Spotify")).toBeInTheDocument();
    });

    // Section headings derived from the backend's `category` field
    expect(screen.getByText("Streaming")).toBeInTheDocument();
    expect(screen.getByText("Play history")).toBeInTheDocument();
    expect(screen.getByText("Metadata & enrichment")).toBeInTheDocument();

    // Connector names
    expect(screen.getByText("Last.fm")).toBeInTheDocument();
    expect(screen.getByText("MusicBrainz")).toBeInTheDocument();
    expect(screen.getByText("Apple Music")).toBeInTheDocument();
  });

  it("renders error state when API fails", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        );
      }),
    );

    renderWithProviders(<Integrations />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load connectors")).toBeInTheDocument();
    });
  });

  it("renders empty state when no connectors returned", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json([], { status: 200 });
      }),
    );

    renderWithProviders(<Integrations />);

    await waitFor(() => {
      expect(screen.getByText("No connectors configured")).toBeInTheDocument();
    });
  });

  it("shows connect buttons for disconnected services", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json(
          [
            makeConnectorMetadata({ name: "spotify" }),
            makeConnectorMetadata({ name: "lastfm" }),
            makeConnectorMetadata({ name: "musicbrainz", connected: true }),
            makeConnectorMetadata({ name: "apple_music" }),
          ],
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Integrations />);

    await waitFor(() => {
      expect(screen.getByText("Spotify")).toBeInTheDocument();
    });

    // Connect buttons in the cards
    expect(screen.getByText("Connect Spotify")).toBeInTheDocument();
    expect(screen.getByText("Connect Last.fm")).toBeInTheDocument();
  });

  it("updates page description", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json(allConnectors, { status: 200 });
      }),
    );

    renderWithProviders(<Integrations />);

    await waitFor(() => {
      expect(screen.getByText("Integrations")).toBeInTheDocument();
    });

    expect(screen.getByText(/Your music services/)).toBeInTheDocument();
  });
});
