import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { TagFilter } from "./TagFilter";

function setup(overrides: Partial<Parameters<typeof TagFilter>[0]> = {}) {
  server.use(http.get("*/api/v1/tags", () => HttpResponse.json([])));
  const onTagsChange = vi.fn();
  const onModeChange = vi.fn();
  renderWithProviders(
    <TagFilter
      tags={[]}
      mode="and"
      onTagsChange={onTagsChange}
      onModeChange={onModeChange}
      {...overrides}
    />,
  );
  return { onTagsChange, onModeChange };
}

describe("TagFilter", () => {
  it("shows a single 'Filter by tag' button when empty", () => {
    setup();
    expect(
      screen.getByRole("button", { name: "Add tag filter" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });

  it("renders chips for active filter tags", () => {
    setup({ tags: ["mood:chill"] });
    expect(screen.getByText("mood:chill")).toBeInTheDocument();
  });

  it("removes a tag when its chip × is clicked", async () => {
    const { onTagsChange } = setup({ tags: ["mood:chill", "banger"] });
    await userEvent.click(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    );
    expect(onTagsChange).toHaveBeenCalledWith(["banger"]);
  });

  it("hides the mode toggle when fewer than 2 tags", () => {
    setup({ tags: ["mood:chill"] });
    expect(
      screen.queryByRole("group", { name: "Tag filter mode" }),
    ).not.toBeInTheDocument();
  });

  it("shows the mode toggle when ≥2 tags are selected", () => {
    setup({ tags: ["mood:chill", "energy:high"] });
    expect(
      screen.getByRole("group", { name: "Tag filter mode" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("switches mode to OR when 'Any' is clicked", async () => {
    const { onModeChange } = setup({
      tags: ["mood:chill", "energy:high"],
      mode: "and",
    });
    await userEvent.click(screen.getByRole("button", { name: "Any" }));
    expect(onModeChange).toHaveBeenCalledWith("or");
  });

  it("adds a new tag selected from the autocomplete", async () => {
    server.use(
      http.get("*/api/v1/tags", () =>
        HttpResponse.json([{ tag: "banger", count: 4 }]),
      ),
    );
    const onTagsChange = vi.fn();
    renderWithProviders(
      <TagFilter
        tags={["mood:chill"]}
        mode="and"
        onTagsChange={onTagsChange}
        onModeChange={vi.fn()}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Add tag filter" }),
    );
    await waitFor(() => {
      expect(screen.getByText("banger")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByText("banger"));

    expect(onTagsChange).toHaveBeenCalledWith(["mood:chill", "banger"]);
  });
});
