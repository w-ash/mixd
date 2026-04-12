import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { TagAutocomplete } from "./TagAutocomplete";

function mockTags(tags: Array<{ tag: string; count: number }>) {
  server.use(http.get("*/api/v1/tags", () => HttpResponse.json(tags)));
}

describe("TagAutocomplete", () => {
  it("renders existing tags as suggestions", async () => {
    mockTags([
      { tag: "mood:chill", count: 5 },
      { tag: "banger", count: 2 },
    ]);
    renderWithProviders(<TagAutocomplete onAdd={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("mood:chill")).toBeInTheDocument();
    });
    expect(screen.getByText("banger")).toBeInTheDocument();
  });

  it("calls onAdd with the suggestion tag when selected", async () => {
    mockTags([{ tag: "mood:chill", count: 5 }]);
    const onAdd = vi.fn();
    renderWithProviders(<TagAutocomplete onAdd={onAdd} />);

    await waitFor(() => {
      expect(screen.getByText("mood:chill")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByText("mood:chill"));

    expect(onAdd).toHaveBeenCalledWith("mood:chill");
  });

  it("offers an 'Add' option for new input", async () => {
    mockTags([]);
    const onAdd = vi.fn();
    renderWithProviders(<TagAutocomplete onAdd={onAdd} />);

    const input = screen.getByPlaceholderText("Add a tag…");
    await userEvent.type(input, "energy:high");

    const addRow = await screen.findByText("energy:high");
    await userEvent.click(addRow);

    expect(onAdd).toHaveBeenCalledWith("energy:high");
  });

  it("hides tags listed in `exclude`", async () => {
    mockTags([
      { tag: "mood:chill", count: 5 },
      { tag: "banger", count: 2 },
    ]);
    renderWithProviders(
      <TagAutocomplete onAdd={vi.fn()} exclude={["mood:chill"]} />,
    );

    await waitFor(() => {
      expect(screen.getByText("banger")).toBeInTheDocument();
    });
    expect(screen.queryByText("mood:chill")).not.toBeInTheDocument();
  });
});
