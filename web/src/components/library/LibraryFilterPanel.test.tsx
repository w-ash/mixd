import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { countActiveFilters, LibraryFilterPanel } from "./LibraryFilterPanel";

function baseProps() {
  return {
    expanded: true,
    preference: null as null,
    liked: null as null,
    connector: null as null,
    tags: [] as string[],
    tagMode: "and" as const,
    connectors: [
      { name: "spotify", display_name: "Spotify" },
      { name: "lastfm", display_name: "Last.fm" },
      // biome-ignore lint/suspicious/noExplicitAny: minimal test shape
    ] as any,
    onPreferenceChange: vi.fn(),
    onLikedChange: vi.fn(),
    onConnectorChange: vi.fn(),
    onTagsChange: vi.fn(),
    onTagModeChange: vi.fn(),
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

  it("clicking a preference button fires onPreferenceChange", async () => {
    const onPreferenceChange = vi.fn();
    render(
      <LibraryFilterPanel
        {...baseProps()}
        onPreferenceChange={onPreferenceChange}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Star/ }));
    expect(onPreferenceChange).toHaveBeenCalledWith("star");
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

describe("countActiveFilters", () => {
  it("is 0 for an empty state", () => {
    expect(
      countActiveFilters({
        preference: null,
        liked: null,
        connector: null,
        tags: [],
      }),
    ).toBe(0);
  });

  it("counts each filter group at most once", () => {
    expect(
      countActiveFilters({
        preference: "star",
        liked: "true",
        connector: "spotify",
        tags: ["mood:chill", "energy:low"],
      }),
    ).toBe(4); // tags counts as 1 regardless of length
  });

  it("treats liked='false' as an active filter", () => {
    expect(
      countActiveFilters({
        preference: null,
        liked: "false",
        connector: null,
        tags: [],
      }),
    ).toBe(1);
  });

  it("treats liked='all'/garbage as inactive", () => {
    expect(
      countActiveFilters({
        preference: null,
        liked: "all",
        connector: null,
        tags: [],
      }),
    ).toBe(0);
  });
});
