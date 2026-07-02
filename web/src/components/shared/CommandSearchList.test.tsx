import { delay, HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { LibraryTrackSchema } from "#/api/generated/model";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { CommandSearchList } from "./CommandSearchList";

const TRACK_ID = "019d0000-0000-7000-8000-000000000001";

function makeTrack(
  overrides: Partial<LibraryTrackSchema> = {},
): LibraryTrackSchema {
  return {
    id: TRACK_ID,
    title: "Paranoid Android",
    artists: [{ name: "Radiohead" }],
    album: "OK Computer",
    duration_ms: 383_000,
    isrc: null,
    connector_names: ["spotify"],
    is_liked: false,
    ...overrides,
  };
}

/**
 * Install a `/tracks` handler returning `tracks`, recording every request URL.
 * Pass `{ pending: true }` to keep the request in flight (loading state).
 */
function installTracks(
  tracks: LibraryTrackSchema[],
  opts: { pending?: boolean } = {},
): string[] {
  const urls: string[] = [];
  server.use(
    http.get("*/api/v1/tracks", async ({ request }) => {
      urls.push(request.url);
      if (opts.pending) await delay("infinite");
      return HttpResponse.json(
        { data: tracks, total: tracks.length, limit: 50, offset: 0 },
        { status: 200 },
      );
    }),
  );
  return urls;
}

function input() {
  return screen.getByPlaceholderText("Search tracks...");
}

describe("CommandSearchList", () => {
  it("shows the threshold prompt and fires no request under 2 characters", async () => {
    const urls = installTracks([makeTrack()]);
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={() => {}} />);

    await user.type(input(), "a");

    await waitFor(() =>
      expect(
        screen.getByText(/type at least 2 characters/i),
      ).toBeInTheDocument(),
    );
    expect(urls).toHaveLength(0);
  });

  it("shows the loading state while a ≥2-char search is in flight", async () => {
    installTracks([], { pending: true });
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={() => {}} />);

    await user.type(input(), "song");

    expect(await screen.findByText(/searching/i)).toBeInTheDocument();
  });

  it("shows the empty state when a search returns no tracks", async () => {
    installTracks([]);
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={() => {}} />);

    await user.type(input(), "song");

    expect(await screen.findByText("No tracks found.")).toBeInTheDocument();
  });

  it("renders the title, artist/album subtitle, and connector icons", async () => {
    installTracks([makeTrack()]);
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={() => {}} />);

    await user.type(input(), "para");

    expect(await screen.findByText("Paranoid Android")).toBeInTheDocument();
    expect(screen.getByText(/Radiohead — OK Computer/)).toBeInTheDocument();
    expect(screen.getByText("Spotify")).toBeInTheDocument();
  });

  it("forwards the default limit of 10", async () => {
    const urls = installTracks([]);
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={() => {}} />);

    await user.type(input(), "song");

    await waitFor(() => expect(urls.length).toBeGreaterThan(0));
    expect(urls.at(-1)).toContain("limit=10");
  });

  it("forwards an explicit limit of 20", async () => {
    const urls = installTracks([]);
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={() => {}} limit={20} />);

    await user.type(input(), "song");

    await waitFor(() => expect(urls.length).toBeGreaterThan(0));
    expect(urls.at(-1)).toContain("limit=20");
  });

  it("suppresses the fetch when enabled is false, even at ≥2 chars", async () => {
    const urls = installTracks([makeTrack()]);
    const user = userEvent.setup();
    renderWithProviders(
      <CommandSearchList onSelect={() => {}} enabled={false} />,
    );

    await user.type(input(), "song");

    // With the query gated off, the ladder falls through to the empty state.
    await waitFor(() =>
      expect(screen.getByText("No tracks found.")).toBeInTheDocument(),
    );
    expect(urls).toHaveLength(0);
  });

  it("filters out excludeTrackId before the empty check", async () => {
    installTracks([makeTrack()]);
    const user = userEvent.setup();
    renderWithProviders(
      <CommandSearchList onSelect={() => {}} excludeTrackId={TRACK_ID} />,
    );

    await user.type(input(), "para");

    expect(await screen.findByText("No tracks found.")).toBeInTheDocument();
    expect(screen.queryByText("Paranoid Android")).not.toBeInTheDocument();
  });

  it("fires onSelect for the highlighted row on ArrowDown + Enter", async () => {
    installTracks([makeTrack()]);
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={onSelect} />);

    await user.type(input(), "para");
    await screen.findByText("Paranoid Android");
    await user.keyboard("{ArrowDown}{Enter}");

    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: TRACK_ID, title: "Paranoid Android" }),
    );
  });

  it("fires onSelect when a row is clicked", async () => {
    installTracks([makeTrack()]);
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CommandSearchList onSelect={onSelect} />);

    await user.type(input(), "para");
    await user.click(await screen.findByText("Paranoid Android"));

    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: TRACK_ID }),
    );
  });

  it("focuses the input on mount", async () => {
    installTracks([]);
    renderWithProviders(<CommandSearchList onSelect={() => {}} />);

    await waitFor(() => expect(input()).toHaveFocus());
  });

  it("renders rowLeading and rowTrailing for each result", async () => {
    installTracks([
      makeTrack({ id: "id-a", title: "Track A" }),
      makeTrack({ id: "id-b", title: "Track B" }),
    ]);
    const user = userEvent.setup();
    renderWithProviders(
      <CommandSearchList
        onSelect={() => {}}
        rowLeading={(track) => <span>LEAD-{track.title}</span>}
        rowTrailing={() => <span>TRAIL</span>}
      />,
    );

    await user.type(input(), "track");
    await screen.findByText("Track A");

    expect(screen.getByText("LEAD-Track A")).toBeInTheDocument();
    expect(screen.getByText("LEAD-Track B")).toBeInTheDocument();
    expect(screen.getAllByText("TRAIL")).toHaveLength(2);
  });
});
