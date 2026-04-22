import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ActiveFilterChips } from "./ActiveFilterChips";

function baseProps() {
  return {
    search: null as string | null,
    liked: null as string | null,
    connector: null as string | null,
    preference: null as string | null,
    tags: [] as string[],
    onClearFilter: vi.fn(),
    onRemoveTag: vi.fn(),
    onClearAll: vi.fn(),
  };
}

describe("ActiveFilterChips", () => {
  it("renders nothing when no filters are active", () => {
    const { container } = render(<ActiveFilterChips {...baseProps()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders one chip per active filter + a Clear all link", () => {
    render(
      <ActiveFilterChips
        {...baseProps()}
        search="radiohead"
        preference="star"
        liked="true"
        connector="spotify"
        tags={["mood:chill"]}
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

  it("dismissing a non-tag chip calls onClearFilter with the right key", async () => {
    const onClearFilter = vi.fn();
    render(
      <ActiveFilterChips
        {...baseProps()}
        preference="star"
        onClearFilter={onClearFilter}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /Remove Preference:/ }),
    );
    expect(onClearFilter).toHaveBeenCalledWith("preference");
  });

  it("dismissing a tag chip calls onRemoveTag with the tag", async () => {
    const onRemoveTag = vi.fn();
    render(
      <ActiveFilterChips
        {...baseProps()}
        tags={["mood:chill", "energy:low"]}
        onRemoveTag={onRemoveTag}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    );
    expect(onRemoveTag).toHaveBeenCalledWith("mood:chill");
    expect(onRemoveTag).toHaveBeenCalledTimes(1);
  });

  it("clicking Clear all fires onClearAll", async () => {
    const onClearAll = vi.fn();
    render(
      <ActiveFilterChips
        {...baseProps()}
        preference="yah"
        onClearAll={onClearAll}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  it("treats liked='false' as an active filter labeled 'Not liked'", () => {
    render(<ActiveFilterChips {...baseProps()} liked="false" />);
    expect(screen.getByText("Not liked")).toBeInTheDocument();
  });
});
