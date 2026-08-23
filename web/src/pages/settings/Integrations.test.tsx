import { delay, HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { toasts } from "#/lib/toasts";
import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
  within,
} from "#/test/test-utils";

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

  it("flips a connector card to connected after an auth callback redirect, even though the callback's invalidation races the page's own initial fetch", async () => {
    // The page mounts fresh off the OAuth redirect: `useGetConnectorsApiV1ConnectorsGet`
    // fires its initial GET on mount, and the `?auth=apple_music&status=success`
    // effect fires `invalidateQueries` in the same tick. The backend is already
    // correct by the time of the redirect, but the *first* GET here simulates
    // a request that raced ahead of that correctness (matching the live repro:
    // card stays "disconnected" until something forces a second round-trip).
    let callCount = 0;
    server.use(
      http.get("*/api/v1/connectors", () => {
        callCount += 1;
        const connected = callCount > 1;
        return HttpResponse.json(
          [
            makeConnectorMetadata({
              name: "apple_music",
              connected,
              status: connected ? "connected" : "disconnected",
            }),
          ],
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Integrations />, {
      routerProps: {
        initialEntries: [
          "/settings/integrations?auth=apple_music&status=success",
        ],
      },
    });

    // The callback's invalidation must eventually produce a *second* network
    // round-trip that flips the card — not just redisplay the first response.
    await waitFor(() => {
      expect(screen.queryByText("Connect Apple Music")).not.toBeInTheDocument();
    });
    expect(callCount).toBeGreaterThanOrEqual(2);
  });

  it("renders a physical-category connector under its own section (guards the silent category drop)", async () => {
    server.use(
      http.get("*/api/v1/connectors", () => {
        return HttpResponse.json(
          [
            ...allConnectors,
            makeConnectorMetadata({
              name: "discogs",
              category: "physical",
              auth_method: "token",
              capabilities: [],
              connected: true,
              detail: "3 releases",
            }),
          ],
          { status: 200 },
        );
      }),
    );

    renderWithProviders(<Integrations />);

    await waitFor(() => {
      expect(screen.getByText("Physical media")).toBeInTheDocument();
    });
    expect(screen.getByText("Discogs")).toBeInTheDocument();
  });

  it("flips the Discogs card to connected after the token form submits", async () => {
    const user = userEvent.setup();
    let callCount = 0;
    server.use(
      http.get("*/api/v1/connectors", () => {
        callCount += 1;
        const connected = callCount > 1;
        return HttpResponse.json(
          [
            makeConnectorMetadata({
              name: "discogs",
              connected,
              detail: connected ? "3 releases" : undefined,
            }),
          ],
          { status: 200 },
        );
      }),
      http.put("*/api/v1/connectors/discogs/token", () => {
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(<Integrations />);

    await user.click(await screen.findByText("Connect Discogs"));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Discogs personal access token"),
      "abc123token",
    );
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));

    // Success invalidates the connectors query — the refetch flips the card.
    await waitFor(() => {
      expect(screen.getByText("connected · 3 releases")).toBeInTheDocument();
    });
    expect(callCount).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Connect Discogs")).not.toBeInTheDocument();
  });

  it("holds the dialog open and busy — and defers the success toast — until the post-connect refetch settles", async () => {
    // Regression for the live bug: the success toast fired (and the dialog
    // closed) the instant the PUT resolved, before the connectors refetch
    // had landed — so the card still read "Connect Discogs" under a
    // "Discogs connected" toast. Delay the *second* GET (the post-invalidate
    // refetch) so a wrong ordering is observable mid-flight.
    const user = userEvent.setup();
    const successSpy = vi.spyOn(toasts, "success");
    let callCount = 0;
    server.use(
      http.get("*/api/v1/connectors", async () => {
        callCount += 1;
        if (callCount > 1) {
          await delay(50);
        }
        const connected = callCount > 1;
        return HttpResponse.json(
          [
            makeConnectorMetadata({
              name: "discogs",
              connected,
              detail: connected ? "3 releases" : undefined,
            }),
          ],
          { status: 200 },
        );
      }),
      http.put("*/api/v1/connectors/discogs/token", () => {
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(<Integrations />);

    await user.click(await screen.findByText("Connect Discogs"));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Discogs personal access token"),
      "abc123token",
    );
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));

    // The PUT has resolved (204) but the refetch it triggered is still
    // in flight: the dialog must stay open and busy, and success must not
    // have been declared yet.
    expect(
      within(dialog).getByRole("button", { name: "Validating..." }),
    ).toBeInTheDocument();
    expect(successSpy).not.toHaveBeenCalled();

    await waitFor(() => {
      expect(successSpy).toHaveBeenCalledWith("Discogs connected");
    });

    // By the time success is declared, the refetch has already landed: the
    // dialog is closed and the card shows connected — one continuous
    // operation from the user's perspective, not toast-then-refetch.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("connected · 3 releases")).toBeInTheDocument();
    expect(screen.queryByText("Connect Discogs")).not.toBeInTheDocument();
    expect(callCount).toBeGreaterThanOrEqual(2);
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
