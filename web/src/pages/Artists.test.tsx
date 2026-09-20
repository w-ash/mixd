import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { makeArtistSummary } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { Artists } from "./Artists";

// `resetHandlers` does not unsubscribe request listeners — without this a
// capture from one test keeps recording during the next.
afterEach(() => server.events.removeAllListeners());

/** Every `GET /artists` request the component made, newest last. */
function captureRequests() {
  const urls: URL[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/v1/artists") urls.push(url);
  });
  return urls;
}

function overrideArtists(data: unknown[], total?: number) {
  server.use(
    http.get("*/api/v1/artists", () =>
      HttpResponse.json(
        { data, total: total ?? data.length, limit: 50, offset: 0 },
        { status: 200 },
      ),
    ),
  );
}

const ARTISTS = [
  makeArtistSummary({
    id: "artist-1",
    name: "Brian Eno",
    track_count: 42,
    connectors: ["spotify", "lastfm"],
  }),
  makeArtistSummary({
    id: "artist-2",
    name: "David Bowie",
    track_count: 17,
    is_favorited: true,
  }),
];

describe("Artists", () => {
  it("renders a row per artist with track counts and sources", async () => {
    overrideArtists(ARTISTS);

    renderWithProviders(<Artists />);

    await waitFor(() => {
      expect(screen.getByText("Brian Eno")).toBeInTheDocument();
    });

    expect(screen.getByText("David Bowie")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Brian Eno" })).toHaveAttribute(
      "href",
      "/artists/artist-1",
    );
    // The favorited artist's heart advertises the action it will perform.
    expect(
      screen.getByRole("button", { name: "Unfavorite David Bowie" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("sends the typed search as ?search=", async () => {
    const user = userEvent.setup();
    const requests = captureRequests();
    overrideArtists(ARTISTS);

    renderWithProviders(<Artists />);
    await waitFor(() => expect(screen.getByText("Brian Eno")).toBeVisible());

    await user.type(screen.getByLabelText("Search artists"), "eno");

    await waitFor(() => {
      expect(requests.at(-1)?.searchParams.get("search")).toBe("eno");
    });
  });

  it("sends favorites_only when the scope switches to Favorites", async () => {
    const user = userEvent.setup();
    const requests = captureRequests();
    overrideArtists(ARTISTS);

    renderWithProviders(<Artists />);
    await waitFor(() => expect(screen.getByText("Brian Eno")).toBeVisible());

    await user.click(screen.getByRole("button", { name: "Favorites" }));

    await waitFor(() => {
      expect(requests.at(-1)?.searchParams.get("favorites_only")).toBe("true");
    });
  });

  it("sends the column sort as ?sort=", async () => {
    const user = userEvent.setup();
    const requests = captureRequests();
    overrideArtists(ARTISTS);

    renderWithProviders(<Artists />);
    await waitFor(() => expect(screen.getByText("Brian Eno")).toBeVisible());

    // A header that is not the active sort offers ascending first.
    await user.click(
      screen.getByRole("button", { name: /Sort by Tracks ascending/ }),
    );

    await waitFor(() => {
      expect(requests.at(-1)?.searchParams.get("sort")).toBe("track_count_asc");
    });

    // Clicking the now-active header flips the direction.
    await user.click(
      screen.getByRole("button", { name: /Sort by Tracks descending/ }),
    );

    await waitFor(() => {
      expect(requests.at(-1)?.searchParams.get("sort")).toBe(
        "track_count_desc",
      );
    });
  });

  it("favorites then unfavorites an artist through the heart", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    // Stateful backend: the refetch that follows each write has to agree with
    // the optimistic flip, or the heart snaps back and the test lies.
    const favorited = new Set<string>();
    server.use(
      http.get("*/api/v1/artists", () =>
        HttpResponse.json(
          {
            data: ARTISTS.map((artist) => ({
              ...artist,
              is_favorited: favorited.has(artist.id),
            })),
            total: ARTISTS.length,
            limit: 50,
            offset: 0,
          },
          { status: 200 },
        ),
      ),
      http.post("*/api/v1/artists/:id/favorite", ({ params }) => {
        calls.push(`POST ${params.id}`);
        favorited.add(String(params.id));
        return HttpResponse.json({ id: params.id, is_favorited: true });
      }),
      http.delete("*/api/v1/artists/:id/favorite", ({ params }) => {
        calls.push(`DELETE ${params.id}`);
        favorited.delete(String(params.id));
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(<Artists />);
    await waitFor(() => expect(screen.getByText("Brian Eno")).toBeVisible());

    await user.click(
      screen.getByRole("button", { name: "Favorite Brian Eno" }),
    );

    const pressed = await screen.findByRole("button", {
      name: "Unfavorite Brian Eno",
    });
    expect(calls).toEqual(["POST artist-1"]);

    await user.click(pressed);

    await waitFor(() =>
      expect(calls).toEqual(["POST artist-1", "DELETE artist-1"]),
    );
    expect(
      await screen.findByRole("button", { name: "Favorite Brian Eno" }),
    ).toBeVisible();
  });

  it("rolls the heart back when the favorite request fails", async () => {
    const user = userEvent.setup();
    overrideArtists(ARTISTS);
    server.use(
      http.post("*/api/v1/artists/:id/favorite", () =>
        HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "nope" } },
          { status: 500 },
        ),
      ),
    );

    renderWithProviders(<Artists />);
    await waitFor(() => expect(screen.getByText("Brian Eno")).toBeVisible());

    await user.click(
      screen.getByRole("button", { name: "Favorite Brian Eno" }),
    );

    expect(
      await screen.findByRole("button", { name: "Favorite Brian Eno" }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("points an empty library at the Import Center", async () => {
    overrideArtists([]);

    renderWithProviders(<Artists />);

    await waitFor(() => {
      expect(screen.getByText("No artists yet")).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Import Music" })).toHaveAttribute(
      "href",
      "/settings/sync",
    );
  });

  it("distinguishes an empty favorites filter from an empty library", async () => {
    overrideArtists([]);

    renderWithProviders(<Artists />, {
      routerProps: { initialEntries: ["/artists?favorites=1"] },
    });

    await waitFor(() => {
      expect(screen.getByText("No favorite artists yet")).toBeInTheDocument();
    });
    expect(screen.queryByText("No artists yet")).not.toBeInTheDocument();
  });

  it("renders an error state when the list fails", async () => {
    server.use(
      http.get("*/api/v1/artists", () =>
        HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        ),
      ),
    );

    renderWithProviders(<Artists />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load artists")).toBeInTheDocument();
    });
  });
});
