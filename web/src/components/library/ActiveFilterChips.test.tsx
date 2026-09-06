import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LibraryFilters } from "#/hooks/useLibraryFilters";

import { ActiveFilterChips } from "./ActiveFilterChips";

function makeFilters(overrides: Partial<LibraryFilters> = {}): LibraryFilters {
  return {
    search: null,
    preference: null,
    liked: null,
    connector: null,
    tags: [],
    tagMode: "and",
    minPlays: null,
    neverPlayed: false,
    playedWithin: null,
    notPlayedWithin: null,
    sort: { field: "last_played", dir: "desc" },
    ...overrides,
  };
}

describe("ActiveFilterChips", () => {
  it("renders nothing when no filters are active", () => {
    const { container } = render(
      <ActiveFilterChips
        filters={makeFilters()}
        setFilter={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one chip per active filter + a Clear all link", () => {
    render(
      <ActiveFilterChips
        filters={makeFilters({
          search: "radiohead",
          preference: "star",
          liked: "true",
          connector: "spotify",
          tags: ["mood:chill"],
        })}
        setFilter={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );

    expect(screen.getByText(/Search: "radiohead"/)).toBeInTheDocument();
    expect(screen.getByText(/Preference: ★ Starred/)).toBeInTheDocument();
    expect(screen.getByText("Liked")).toBeInTheDocument();
    expect(screen.getByText(/Source: Spotify/)).toBeInTheDocument();
    expect(screen.getByText("mood:chill")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Clear all" }),
    ).toBeInTheDocument();
  });

  it("labels a connector chip from the brand table, not a capitalized name", () => {
    render(
      <ActiveFilterChips
        filters={makeFilters({ connector: "lastfm" })}
        setFilter={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(screen.getByText("Source: Last.fm")).toBeInTheDocument();
  });

  it("dismissing a non-tag chip clears that filter", async () => {
    const setFilter = vi.fn();
    render(
      <ActiveFilterChips
        filters={makeFilters({ preference: "star" })}
        setFilter={setFilter}
        onClearAll={vi.fn()}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /Remove Preference:/ }),
    );
    expect(setFilter).toHaveBeenCalledWith("preference", null);
  });

  it("dismissing a tag chip writes back the remaining tags", async () => {
    const setFilter = vi.fn();
    render(
      <ActiveFilterChips
        filters={makeFilters({ tags: ["mood:chill", "energy:low"] })}
        setFilter={setFilter}
        onClearAll={vi.fn()}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    );
    expect(setFilter).toHaveBeenCalledWith("tags", ["energy:low"]);
    expect(setFilter).toHaveBeenCalledTimes(1);
  });

  it("clicking Clear all fires onClearAll", async () => {
    const onClearAll = vi.fn();
    render(
      <ActiveFilterChips
        filters={makeFilters({ preference: "yah" })}
        setFilter={vi.fn()}
        onClearAll={onClearAll}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  it("treats liked='false' as an active filter labeled 'Not liked'", () => {
    render(
      <ActiveFilterChips
        filters={makeFilters({ liked: "false" })}
        setFilter={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(screen.getByText("Not liked")).toBeInTheDocument();
  });

  it("keeps a zero minimum-play filter visible", () => {
    // `0` is falsy but a real filter — the chip must not vanish for it.
    render(
      <ActiveFilterChips
        filters={makeFilters({ minPlays: 0 })}
        setFilter={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(screen.getByText("0+ plays")).toBeInTheDocument();
  });
});
