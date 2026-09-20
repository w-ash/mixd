import { delay, HttpResponse, http } from "msw";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { installHttpCache } from "#/test/http-cache";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { ArtistDetail } from "./ArtistDetail";

const ARTIST_ID = "11111111-1111-1111-1111-111111111111";

const mockArtist = {
  id: ARTIST_ID,
  name: "Radiohead",
  mbid: "a74b1b7f-71a5-4011-9441-d0b5e4122711",
  kind: "group",
  track_count: 42,
  is_favorited: false,
  connectors: ["spotify", "lastfm"],
  connector_mappings: [
    {
      connector_name: "spotify",
      connector_artist_identifier: "4Z8W4fKeB5YxbusRsdQVPb",
      name: "Radiohead",
      is_primary: true,
      match_method: "mbid",
      confidence: 100,
      external_url: "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb",
    },
    {
      connector_name: "lastfm",
      connector_artist_identifier: "Radiohead",
      name: "Radiohead",
      is_primary: false,
      match_method: "artist_title",
      confidence: 80,
      external_url: null,
    },
  ],
  related: [] as unknown[],
};

function overrideArtist(data: Record<string, unknown>, status = 200) {
  server.use(
    http.get("*/api/v1/artists/:artistId", () =>
      HttpResponse.json(data, { status }),
    ),
  );
}

/** Capture the query the tracks section fires, and answer it. */
function overrideTracks(tracks: unknown[] = [], seen: URL[] = []) {
  server.use(
    http.get("*/api/v1/tracks", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json(
        { data: tracks, total: tracks.length, limit: 25, offset: 0 },
        { status: 200 },
      );
    }),
  );
  return seen;
}

function renderArtistDetail(artistId = ARTIST_ID) {
  return renderWithProviders(
    <Routes>
      <Route path="artists/:id" element={<ArtistDetail />} />
    </Routes>,
    { routerProps: { initialEntries: [`/artists/${artistId}`] } },
  );
}

describe("ArtistDetail", () => {
  let uninstallCache: (() => void) | undefined;
  afterEach(() => {
    uninstallCache?.();
    uninstallCache = undefined;
  });

  it("renders identity fields from the detail response", async () => {
    overrideArtist(mockArtist);
    overrideTracks();

    renderArtistDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Radiohead",
      );
    });

    expect(screen.getByText("Group")).toBeInTheDocument();
    expect(screen.getByText("42 tracks")).toBeInTheDocument();

    const mbidLink = screen.getByRole("link", {
      name: "a74b1b7f-71a5-4011-9441-d0b5e4122711",
    });
    expect(mbidLink).toHaveAttribute(
      "href",
      "https://musicbrainz.org/artist/a74b1b7f-71a5-4011-9441-d0b5e4122711",
    );
  });

  it("shows an em-dash for an artist with no kind or MBID", async () => {
    overrideArtist({ ...mockArtist, kind: null, mbid: null });
    overrideTracks();

    renderArtistDetail();

    await waitFor(() => {
      expect(screen.getAllByText("—").length).toBe(2);
    });
  });

  it("flips the heart before the server answers, then unfavorites", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    let favorited = false;
    overrideTracks();
    // Stateful favorite: the refetch after each write has to agree with the
    // optimistic flip, or the heart would snap back on its own.
    server.use(
      http.get("*/api/v1/artists/:artistId", () =>
        HttpResponse.json({ ...mockArtist, is_favorited: favorited }),
      ),
      http.post("*/api/v1/artists/:artistId/favorite", async () => {
        await delay(150);
        favorited = true;
        calls.push("POST");
        return HttpResponse.json(
          { artist_id: ARTIST_ID, is_favorited: true, changed: true },
          { status: 200 },
        );
      }),
      http.delete("*/api/v1/artists/:artistId/favorite", async () => {
        await delay(150);
        favorited = false;
        calls.push("DELETE");
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderArtistDetail();

    const toggle = await screen.findByRole("button", {
      name: "Favorite Radiohead",
    });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);

    // Found while the POST is still in flight — this is the optimistic write,
    // not the response.
    const pressed = await screen.findByRole("button", {
      name: "Unfavorite Radiohead",
    });
    expect(pressed).toHaveAttribute("aria-pressed", "true");
    expect(calls).toEqual([]);

    await waitFor(() => {
      expect(calls).toEqual(["POST"]);
    });

    await user.click(
      screen.getByRole("button", { name: "Unfavorite Radiohead" }),
    );

    await waitFor(() => {
      expect(calls).toEqual(["POST", "DELETE"]);
    });
    expect(
      await screen.findByRole("button", { name: "Favorite Radiohead" }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps the heart flipped when the API caches its reads", async () => {
    // `GET /artists/{id}` answers with `Cache-Control: max-age`, so the refetch
    // a write triggers is a browser cache hit unless the read revalidates —
    // which served the pre-write body and snapped the heart back.
    const uninstall = installHttpCache();
    uninstallCache = uninstall;
    const user = userEvent.setup();
    const calls: string[] = [];
    let favorited = false;
    overrideTracks();
    server.use(
      http.get("*/api/v1/artists/:artistId", () =>
        HttpResponse.json(
          { ...mockArtist, is_favorited: favorited },
          { status: 200, headers: { "Cache-Control": "max-age=10" } },
        ),
      ),
      http.post("*/api/v1/artists/:artistId/favorite", () => {
        favorited = true;
        calls.push("POST");
        return HttpResponse.json(
          { artist_id: ARTIST_ID, is_favorited: true, changed: true },
          { status: 200 },
        );
      }),
      http.delete("*/api/v1/artists/:artistId/favorite", () => {
        favorited = false;
        calls.push("DELETE");
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderArtistDetail();

    await user.click(
      await screen.findByRole("button", { name: "Favorite Radiohead" }),
    );

    await waitFor(() => expect(calls).toEqual(["POST"]));
    // Survives the refetch rather than only the optimistic write.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Unfavorite Radiohead" }),
      ).toHaveAttribute("aria-pressed", "true"),
    );

    // The second click has to be the opposite write, not a repeat of the first.
    await user.click(
      screen.getByRole("button", { name: "Unfavorite Radiohead" }),
    );

    await waitFor(() => expect(calls).toEqual(["POST", "DELETE"]));
    expect(
      await screen.findByRole("button", { name: "Favorite Radiohead" }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("lists connector mappings with an external link", async () => {
    overrideArtist(mockArtist);
    overrideTracks();

    renderArtistDetail();

    await waitFor(() => {
      expect(screen.getByText("Connectors")).toBeInTheDocument();
    });

    const openLinks = screen.getAllByRole("link", { name: /Open/ });
    expect(openLinks).toHaveLength(1);
    expect(openLinks[0]).toHaveAttribute(
      "href",
      "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb",
    );
    expect(openLinks[0]).toHaveAttribute("target", "_blank");
    expect(openLinks[0]).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Primary")).toBeInTheDocument();
  });

  it("points an unmapped artist at the enrichment run", async () => {
    overrideArtist({ ...mockArtist, connector_mappings: [] });
    overrideTracks();

    renderArtistDetail();

    expect(
      await screen.findByText("Not mapped on any service yet."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Enrich Artists" }),
    ).toHaveAttribute("href", "/settings/sync");
  });

  it("hides related projects when the connectors named none", async () => {
    overrideArtist(mockArtist);
    overrideTracks();

    renderArtistDetail();

    await waitFor(() => {
      expect(screen.getByText("Connectors")).toBeInTheDocument();
    });
    expect(screen.queryByText("Related Projects")).not.toBeInTheDocument();
  });

  it("renders related projects when a connector named some", async () => {
    overrideArtist({
      ...mockArtist,
      related: [
        {
          name: "Thom Yorke",
          relation: "member",
          connector_name: "musicbrainz",
          identifier: null,
        },
        {
          name: "Radio Head",
          relation: "alias",
          connector_name: "musicbrainz",
          identifier: null,
        },
      ],
    });
    overrideTracks();

    renderArtistDetail();

    expect(await screen.findByText("Related Projects")).toBeInTheDocument();
    expect(screen.getByText("Thom Yorke")).toBeInTheDocument();
    expect(screen.getByText("member")).toBeInTheDocument();
    expect(screen.getByText("alias")).toBeInTheDocument();
  });

  it("queries the tracks list filtered to this artist", async () => {
    overrideArtist(mockArtist);
    const seen = overrideTracks([
      {
        id: 1,
        title: "Paranoid Android",
        artists: [{ name: "Radiohead", artist_id: ARTIST_ID }],
        album: "OK Computer",
        duration_ms: 386000,
        isrc: null,
        connector_names: ["spotify"],
        is_liked: true,
      },
    ]);

    renderArtistDetail();

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0);
    });
    expect(seen[0]?.searchParams.get("artist_id")).toBe(ARTIST_ID);
    expect(
      (await screen.findAllByText("Paranoid Android")).length,
    ).toBeGreaterThan(0);
  });

  it("shows a not-found state for an unknown artist", async () => {
    overrideArtist({ detail: "Artist not found" }, 404);
    overrideTracks();

    renderArtistDetail("missing");

    expect(await screen.findByText("Artist not found")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
