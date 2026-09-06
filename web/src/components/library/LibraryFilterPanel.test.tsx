import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LibraryFilters } from "#/hooks/useLibraryFilters";
import { mockMatchMedia } from "#/test/test-utils";

import { LibraryFilterPanel } from "./LibraryFilterPanel";

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

function baseProps() {
  return {
    expanded: true,
    filters: makeFilters(),
    setFilter: vi.fn(),
    setFilters: vi.fn(),
    connectors: [
      { name: "spotify", display_name: "Spotify" },
      { name: "lastfm", display_name: "Last.fm" },
      // biome-ignore lint/suspicious/noExplicitAny: minimal test shape
    ] as any,
  };
}

describe("LibraryFilterPanel", () => {
  it("renders all three filter groups when expanded", () => {
    render(<LibraryFilterPanel {...baseProps()} />);
    expect(screen.getByText("Preference")).toBeInTheDocument();
    expect(screen.getByText("Tags")).toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
  });

  it("is collapsed but not removed when expanded=false (state preserved for transition)", () => {
    render(<LibraryFilterPanel {...baseProps()} expanded={false} />);
    const panel = document.querySelector("#library-filter-panel");
    // Panel stays mounted with `data-state="closed"` + `aria-hidden` so the
    // collapse transition animates and nested component state isn't blown away.
    expect(panel).toHaveAttribute("data-state", "closed");
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });

  it("clicking a preference button writes the preference filter", async () => {
    const setFilter = vi.fn();
    render(<LibraryFilterPanel {...baseProps()} setFilter={setFilter} />);
    await userEvent.click(screen.getByRole("button", { name: /Star/ }));
    expect(setFilter).toHaveBeenCalledWith("preference", "star");
  });

  it("editing the minimum play count writes both play params at once", async () => {
    const setFilters = vi.fn();
    render(
      <LibraryFilterPanel
        {...baseProps()}
        filters={makeFilters({ neverPlayed: true })}
        setFilters={setFilters}
      />,
    );

    await userEvent.type(screen.getByLabelText("Minimum play count"), "5");
    // One navigation, so "never played" can't survive a min-plays edit.
    expect(setFilters).toHaveBeenCalledWith({
      minPlays: 5,
      neverPlayed: false,
    });
  });

  it("renders liked/connector selects with expected labels", () => {
    // Radix Select + jsdom don't cooperate (hasPointerCapture missing) so we
    // don't click-through the popover here; end-to-end behavior is covered by
    // Library.tsx's existing integration and the Select primitive's own tests.
    render(<LibraryFilterPanel {...baseProps()} />);
    expect(
      screen.getByRole("combobox", { name: "Filter by liked status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Filter by connector" }),
    ).toBeInTheDocument();
  });
});

describe("LibraryFilterPanel — mobile branch", () => {
  afterEach(() => {
    mockMatchMedia(1280);
  });

  it("renders as a Sheet (native <dialog>) below lg: when expanded", () => {
    mockMatchMedia(390);
    render(<LibraryFilterPanel {...baseProps()} expanded onClose={vi.fn()} />);
    const sheet = screen.getByRole("dialog", { name: "Library filters" });
    expect(sheet).toBeInTheDocument();
    expect(sheet).toHaveAttribute("open");
    // Mobile branch must not also mount the desktop disclosure markup.
    expect(document.querySelector("#library-filter-panel")).toBeNull();
  });

  it("clicking the close button calls onClose", async () => {
    mockMatchMedia(390);
    const onClose = vi.fn();
    render(<LibraryFilterPanel {...baseProps()} expanded onClose={onClose} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Close filters" }),
    );
    expect(onClose).toHaveBeenCalled();
  });
});
