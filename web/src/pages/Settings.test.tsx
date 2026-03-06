import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { Settings } from "./Settings";

describe("Settings", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Settings />);

    const skeletons = document.querySelectorAll('[class*="animate-pulse"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders connector cards grouped into sections", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json(
          [
            {
              name: "spotify",
              connected: true,
              account_name: "testuser",
              token_expires_at: Math.floor(Date.now() / 1000) + 3600,
            },
            {
              name: "lastfm",
              connected: true,
              account_name: "lfmuser",
              token_expires_at: null,
            },
            {
              name: "musicbrainz",
              connected: true,
              account_name: null,
              token_expires_at: null,
            },
            {
              name: "apple",
              connected: false,
              account_name: null,
              token_expires_at: null,
            },
          ],
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Settings />);

    await waitFor(() => {
      expect(screen.getByText("Spotify")).toBeInTheDocument();
    });

    // Section headings
    expect(screen.getByText("Streaming")).toBeInTheDocument();
    expect(screen.getByText("Data & Enrichment")).toBeInTheDocument();

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

    renderWithProviders(<Settings />);

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

    renderWithProviders(<Settings />);

    await waitFor(() => {
      expect(screen.getByText("No connectors configured")).toBeInTheDocument();
    });
  });
});
