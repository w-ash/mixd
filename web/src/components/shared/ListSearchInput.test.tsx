import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ListSearchInput, ResultCount } from "./ListSearchInput";

function setup(value: string, isSearching = false) {
  const onChange = vi.fn();
  render(
    <ListSearchInput
      value={value}
      onChange={onChange}
      isSearching={isSearching}
      placeholder="Search things..."
      ariaLabel="Search things"
    />,
  );
  return {
    onChange,
    input: screen.getByRole("searchbox", { name: "Search things" }),
  };
}

describe("ListSearchInput", () => {
  it("clears the search with null when Escape is pressed on a non-empty box", async () => {
    const { onChange, input } = setup("abc");
    input.focus();
    await userEvent.keyboard("{Escape}");
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("ignores Escape on an empty box", async () => {
    const { onChange, input } = setup("");
    input.focus();
    await userEvent.keyboard("{Escape}");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("reports typed text", async () => {
    const { onChange, input } = setup("");
    await userEvent.type(input, "x");
    expect(onChange).toHaveBeenCalledWith("x");
  });

  it("shows the Searching cue only while a search is pending", () => {
    setup("abc", true);
    expect(screen.getByText("Searching...")).toBeInTheDocument();
  });

  it("hides the Searching cue when idle", () => {
    setup("abc", false);
    expect(screen.queryByText("Searching...")).not.toBeInTheDocument();
  });
});

describe("ResultCount", () => {
  it.each([
    [0, "No tracks match current filters."],
    [1, "1 track matches current filters."],
    [1234, "1,234 tracks match current filters."],
  ])("announces %i results", (total, text) => {
    render(<ResultCount total={total} noun="track" />);
    expect(screen.getByText(text)).toHaveAttribute("aria-live", "polite");
  });
});
